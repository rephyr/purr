"""The heart of purr: send the chat, run the tools the model asks for, repeat.

Also: compacting long chats, saving/resuming sessions, undo, helpers (sub-agents),
@file attachments and !shell commands.
"""

import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import ui
from .api import ApiError, Stopped, stream_chat
from .tools import Tools, clip, run_shell, schemas

SYSTEM = """You are {model}, an AI model, working as a coding agent. You are running inside purr, \
a small terminal program that gives you tools and shows your replies to the user. purr is only \
the program around you; it is not you. If someone asks who or what you are, say you are {model} \
(served by {provider}) running in purr. Don't invent a name, persona or backstory, and don't claim \
to be a different model.

Environment
- Project folder: {root} (relative paths start here)
- Today: {date}
- System: Linux

Tools
- read_file, list_files, grep, fetch_url to look around; edit_file, write_file, run to make changes.
- todo: for work with several steps, keep a short task list and update it as you go.
- task: send a helper to explore many files or the web and report back, so this chat stays small.
- The user approves every edit and command. If they say no, stop and wait for them.
- edit_file needs old_text copied exactly from the file, without the line numbers read_file adds.

How to work
- Understand before changing: find the right files with grep or list_files and read them. Never guess what a file contains.
- Do what was asked, no more. Keep changes small and match the existing style, naming and indentation (tabs or spaces).
- After changing code, check it (run the tests or a quick command) when the project allows it.
- When something fails, read the error and fix the cause. Don't repeat a step that just failed.
- Don't create files the task doesn't need. Never run destructive commands (rm -rf, git reset --hard, force pushes) unless asked.
- If the request is unclear, ask one short question instead of guessing.

Replies
- Short and plain. Say what you changed and where (file and line).
- Be honest: say when something failed or you're unsure, and never claim you ran or checked something you didn't."""

HELPER = """

You are a helper for another agent: it gave you one research job. You can only read (files, grep, \
the web). Do the job, then reply with a clear, complete summary of what you found, with file paths \
and line numbers. Your reply is all the other agent will see."""

COMPACT = """Below is a conversation between a user and you (a coding agent). It is getting too long, \
so write a summary that lets you carry on the work without the original. Include:
- what the user wants (their goals and requests; quote important wording)
- decisions made and things the user said yes or no to
- files read and changed (paths, what changed)
- what is done, what is left, and the very next step
- errors met and how they were solved
Be complete but short. Use bullet points. Don't add anything that isn't in the conversation.

CONVERSATION:
{transcript}"""

# PURR_STATE moves saved chats and history elsewhere (tests use it to stay away from your real ones)
STATE_DIR = Path(os.environ.get("PURR_STATE") or Path.home() / ".local/state/purr")
LOG_DIR = STATE_DIR / "sessions"
GLOBAL_NOTES = Path.home() / ".config/purr/AGENTS.md"
NOTES_FILES = ["AGENTS.md"]
NOTES_MAX = 20000
ATTACH_MAX = 20000
COMPACT_AT = 0.85  # compact by itself when the context is this full
RETRY_WAITS = [2, 5, 10]  # seconds between tries when the server hiccups


def _notes(path, label):
    if not path.is_file():
        return ""
    notes = path.read_text(errors="replace")
    if len(notes) > NOTES_MAX:
        notes = notes[:NOTES_MAX] + "\n[notes cut short]"
    return f"\n\n{label}:\n{notes}"


def system_prompt(root, model_id, provider):
    text = SYSTEM.format(model=model_id, provider=provider, root=root,
                         date=datetime.date.today().isoformat())
    text += _notes(GLOBAL_NOTES, "The user's notes for every project")
    for name in NOTES_FILES:
        text += _notes(Path(root) / name, f"Project notes ({name})")
    return text


TEXT_CALL = re.compile(r"<function=([\w-]+)>(.*?)</function>", re.S)
TEXT_PARAM = re.compile(r"<parameter=([\w-]+)>\n?(.*?)\n?</parameter>", re.S)
PARAM_TYPES = {s["function"]["name"]: {k: v.get("type") for k, v in s["function"]["parameters"]["properties"].items()}
               for s in schemas()}
MENTION = re.compile(r"(?<!\S)@(\S+)")


def calls_from_text(text):
    """Qwen models sometimes write tool calls as text instead of real calls:
    <function=read_file><parameter=path>x.py</parameter></function>. Turn those into real ones."""
    calls = []
    for n, (name, body) in enumerate(TEXT_CALL.findall(text)):
        types = PARAM_TYPES.get(name, {})
        args = {}
        for key, value in TEXT_PARAM.findall(body):
            if types.get(key) == "integer":
                value = int(value.strip()) if value.strip().lstrip("-").isdigit() else value
            elif types.get(key) == "boolean":
                value = value.strip().lower() == "true"
            elif types.get(key) == "array":
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            args[key] = value
        calls.append({"id": f"text_call_{n}", "name": name, "args": json.dumps(args)})
    cleaned = re.sub(r"</?tool_call>", "", TEXT_CALL.sub("", text)).strip()
    return calls, cleaned


_OPENCODE_KEYS = {}


def opencode_key(integration):
    """A key you saved with `opencode auth login`, so it doesn't have to live in ~/.zshrc too."""
    if integration in _OPENCODE_KEYS:
        return _OPENCODE_KEYS[integration]
    try:
        out = subprocess.run(["opencode", "auth", "export"], capture_output=True, text=True, timeout=20).stdout
        creds = json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    for c in creds:
        if c.get("integrationID") == integration and c.get("active", True):
            _OPENCODE_KEYS[integration] = (c.get("value") or {}).get("key")
            return _OPENCODE_KEYS[integration]
    return None


def is_peak(provider):
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.weekday() >= 5:
        return False
    return any(a <= now.hour < b for a, b in provider.get("peak_hours_utc", []))


def list_sessions(folder=None):
    """Saved chats, newest first: [{"path", "title", "model", "folder", "when", "turns"}]."""
    found = []
    for p in sorted(LOG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if folder and data.get("folder") != str(folder):
            continue
        users = [m for m in data.get("messages", []) if m.get("role") == "user"]
        if not users:
            continue
        found.append({"path": p, "title": data.get("title") or (users[0].get("content") or "")[:70],
                      "model": data.get("model", "?"), "folder": data.get("folder", ""),
                      "when": datetime.datetime.fromtimestamp(p.stat().st_mtime), "turns": len(users)})
    return found


class SubView:
    """A helper's view: only its tool lines show up (dimmed, under the task line)."""

    def __init__(self, view):
        self.view = view

    def tool(self, line):
        self.view.note(f"↳ {line}")

    def note(self, s, kind="dim"):
        if kind in ("error", "warn"):
            self.view.note(f"↳ {s}", kind)

    def ask(self, question, allow_always=True):
        return "n", ""

    def thinking(self, s): pass
    def text(self, s): pass
    def end_reply(self): pass
    def diff(self, path, before, after): pass
    def status(self, s): pass
    def todos(self, items): pass


class Agent:
    def __init__(self, config, root, model_name, view, helper=False, parent=None):
        self.config = config
        self.root = Path(root).resolve()
        self.view = view
        self.helper = helper
        self.parent = parent
        perms = config.get("permissions", {})
        self.tools = Tools(self.root, view, read_only=helper, allow_run=perms.get("allow_run", []))
        self.tools.spawn = None if helper else self._helper
        self.stop_flag = False  # the TUI sets this to stop an answer (plain mode uses ctrl+c)
        self.set_model(model_name)
        self.new()

    def stopping(self):
        return self.stop_flag or bool(self.parent and self.parent.stopping())

    def set_model(self, name):
        models = self.config["models"]
        if name not in models:
            raise KeyError(f"no model called {name!r}. Have: {', '.join(models)}")
        model = models[name]
        provider = self.config["providers"][model["provider"]]
        key = None
        if provider.get("api_key_env"):
            key = os.environ.get(provider["api_key_env"])
            if not key and provider.get("opencode_auth"):
                key = opencode_key(provider["opencode_auth"])
            if not key:
                raise KeyError(f"{name} needs the {provider['api_key_env']} environment variable"
                               + (f" or `opencode auth login {provider['opencode_auth']}`"
                                  if provider.get("opencode_auth") else ""))
        self.model_name, self.model, self.provider, self.key = name, model, provider, key
        if getattr(self, "messages", None):
            # the system prompt names the model, so it changes with it
            self.messages[0] = {"role": "system", "content": self._system()}

    def _system(self):
        text = system_prompt(self.root, self.model["id"], self.model["provider"])
        return text + HELPER if self.helper else text

    def new(self):
        self.messages = [{"role": "system", "content": self._system()}]
        self.session_cost = 0.0
        self.session_out = 0
        self.last_usage = None
        self.title = ""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.log_path = None if self.helper else LOG_DIR / f"{stamp}.json"

    # ---- sessions ----

    def save_log(self):
        """The whole chat as JSON: for /resume, and to see exactly what the model sent and got back."""
        if not self.log_path or len(self.messages) < 2:
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(
            {"model": self.model_name, "folder": str(self.root), "title": self.title,
             "cost": self.session_cost, "messages": self.messages},
            indent=1, ensure_ascii=False))

    def load(self, path):
        """Carry on a saved chat."""
        data = json.loads(Path(path).read_text())
        if data.get("model") in self.config["models"]:
            try:
                self.set_model(data["model"])
            except KeyError:
                pass  # e.g. no API key right now: keep the current model
        self.messages = data["messages"]
        self.messages[0] = {"role": "system", "content": self._system()}
        self.title = data.get("title", "")
        self.session_cost = data.get("cost", 0.0)
        self.last_usage = None
        self.log_path = Path(path)

    # ---- one model call ----

    def _call(self, messages=None, tools=True, quiet=False):
        body = {
            "model": self.model["id"],
            "messages": messages or self.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = schemas(read_only=self.helper)
        body.update(self.model.get("body", {}))
        on_text = (lambda s: None) if quiet else self.view.text
        on_think = (lambda s: None) if quiet else self.view.thinking

        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                return stream_chat(self.provider["base_url"], self.key, body, on_text, on_think, self.stopping)
            except ApiError as e:
                if not e.retry or attempt == len(RETRY_WAITS):
                    raise
                wait = RETRY_WAITS[attempt]
                self.view.note(f"the model server had a problem, trying again in {wait}s", "warn")
                for _ in range(wait * 10):
                    if self.stopping():
                        raise Stopped from None
                    time.sleep(0.1)
            finally:
                if not quiet:
                    self.view.end_reply()

    def _price(self, usage):
        price = self.model.get("price")
        if not price or not usage:
            return 0.0
        if isinstance(usage.get("cost"), (int, float)):
            return usage["cost"]  # OpenRouter says what the call really cost
        prompt = usage.get("prompt_tokens", 0)
        hit = usage.get("prompt_cache_hit_tokens")
        if hit is None:
            hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", prompt - hit)
        out = usage.get("completion_tokens", 0)
        times = 2 if is_peak(self.provider) else 1
        return times * (hit * price["hit"] + miss * price["miss"] + out * price["out"]) / 1e6

    def _count(self, usage):
        """Add one call's usage to the totals. Returns its price."""
        if not usage:
            return 0.0
        self.last_usage = usage
        cost = self._price(usage)
        self.session_cost += cost
        self.session_out += usage.get("completion_tokens", 0)
        if self.parent:
            self.parent.session_cost += cost
        return cost

    def context_used(self):
        used = 0
        if self.last_usage:
            used = self.last_usage.get("prompt_tokens", 0) + self.last_usage.get("completion_tokens", 0)
        # Ollama only counts the tokens it didn't have cached, so also guess (~4 characters a token)
        guess = len(json.dumps(self.messages)) // 4 + 1500
        return max(used, guess)

    # ---- one turn: your message -> as many model calls + tools as it takes ----

    def turn(self, text):
        self.stop_flag = False
        if not self.title:
            self.title = " ".join(text.split())[:70]
        self.messages.append({"role": "user", "content": self.expand(text)})
        self.tools.begin_turn()
        turn_cost = 0.0
        steps = 0
        retries = 0
        try:
            while True:
                steps += 1
                if steps > self.config.get("max_steps", 40):
                    ans, _ = self.view.ask(f"{steps - 1} steps so far. keep going?", allow_always=False)
                    if ans != "y":
                        break
                    steps = 1
                ctx = self.model.get("context", 0)
                if ctx and self.context_used() > ctx * COMPACT_AT and len(self.messages) > 4:
                    self.compact(auto=True)
                try:
                    reply = self._call()
                except ApiError as e:
                    self.view.note(f"api error: {e}", "error")
                    break
                turn_cost += self._count(reply["usage"])

                if not reply["tool_calls"] and "<function=" in reply["text"]:
                    reply["tool_calls"], reply["text"] = calls_from_text(reply["text"])

                msg = {"role": "assistant", "content": reply["text"] or None}
                if reply["tool_calls"]:
                    msg["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                        for c in reply["tool_calls"]]
                echo = self.provider.get("echo_reasoning")
                if echo:
                    msg[echo] = reply["reasoning"]
                self.messages.append(msg)

                if reply["finish"] == "length":
                    self.view.note("(the reply hit the output limit and was cut off)", "error")
                if not reply["tool_calls"]:
                    # local models sometimes write a tool call as plain text, so it never runs
                    if "tool_call>" in (reply["text"] or "") and retries < 2:
                        retries += 1
                        self.view.note("tool call came out as text, asking it to try again")
                        self.messages.append({"role": "user", "content":
                            "(purr: your tool call came out as plain text, so it did not run. "
                            "Call the tool again.)"})
                        continue
                    break
                if self._run_tools(reply["tool_calls"]):
                    self.view.note("waiting for you", "info")
                    break
        except Stopped:
            self.view.note("stopped", "warn")
        self.save_log()
        self._status(turn_cost)

    def _run_tools(self, calls):
        """Runs the tool calls. Returns True when you said a plain no (the turn ends)."""
        done = 0
        self.tools.halt = False
        try:
            for c in calls:
                if self.stopping():
                    raise Stopped
                if self.tools.halt:
                    result = "skipped: the user stopped to give new instructions"
                else:
                    result = self.tools.call(c["name"], c["args"])
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                done += 1
            return self.tools.halt
        except (KeyboardInterrupt, Stopped):
            # every tool call needs an answer, or the next request is rejected
            for c in calls[done:]:
                self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                      "content": "cancelled: the user stopped it"})
            raise

    def _status(self, turn_cost):
        if not self.last_usage:
            return
        used = self.context_used()
        ctx = self.model.get("context", 0)
        parts = [f"{self.model_name}", f"context {ui.short(used)}/{ui.short(ctx)}"]
        if self.model.get("price"):
            parts.append(f"${turn_cost:.4f} (session ${self.session_cost:.3f})")
            if is_peak(self.provider):
                parts.append("peak price")
        self.view.status("   ".join(parts))

    # ---- compacting ----

    def transcript(self, messages):
        """The chat as plain text for the summary. Long tool results are cut short."""
        out = []
        for m in messages:
            role, content = m.get("role"), m.get("content") or ""
            if role == "user":
                out.append(f"USER: {content}")
            elif role == "assistant":
                calls = [f"{c['function']['name']}({c['function']['arguments'][:300]})"
                         for c in m.get("tool_calls") or []]
                out.append("ASSISTANT: " + content + (f"\n[called: {'; '.join(calls)}]" if calls else ""))
            elif role == "tool":
                out.append("TOOL RESULT: " + (content if len(content) < 1500 else content[:1500] + " [...]"))
        return "\n\n".join(out)

    def compact(self, auto=False):
        """Swap the chat for a summary of it. Returns (tokens before, tokens after) or None."""
        if len(self.messages) < 3:
            self.view.note("nothing to compact yet", "info")
            return None
        before = self.context_used()
        self.view.note("compacting the chat (summarising it to make room)…", "info")
        prompt = COMPACT.format(transcript=self.transcript(self.messages[1:]))
        reply = self._call([{"role": "user", "content": prompt}], tools=False, quiet=True)
        self._count(reply["usage"])
        summary = reply["text"].strip()
        if not summary:
            self.view.note("compacting failed: the model wrote nothing", "error")
            return None
        head = "(purr: the chat got long, so it was replaced by this summary.)\n\n" + summary
        if auto:
            # in the middle of a turn: carry straight on
            self.messages = [self.messages[0], {"role": "user", "content": head +
                             "\n\nCarry on with the work from where you left off."}]
        else:
            self.messages = [self.messages[0], {"role": "user", "content": head},
                             {"role": "assistant", "content": "Got it, I have the summary. What next?"}]
        self.last_usage = None
        after = self.context_used()
        self.view.note(f"compacted: {ui.short(before)} → {ui.short(after)} tokens", "info")
        self.save_log()
        return before, after

    # ---- helpers (the task tool) ----

    def _helper(self, prompt):
        helper = Agent(self.config, self.root, self.model_name, SubView(self.view), helper=True, parent=self)
        helper.turn(prompt)
        answers = [m.get("content") for m in helper.messages if m.get("role") == "assistant" and m.get("content")]
        return answers[-1] if answers else "the helper didn't report anything"

    # ---- things you type ----

    def expand(self, text):
        """@path in a message attaches that file's text for the model."""
        files = []
        for raw in MENTION.findall(text):
            name = raw.rstrip(".,;:!?)")
            p = (self.root / name).resolve()
            if p.is_file() and name not in [f for f, _ in files]:
                try:
                    body = p.read_text(errors="replace")
                except OSError:
                    continue
                if len(body) > ATTACH_MAX:
                    body = body[:ATTACH_MAX] + "\n[file cut short; use read_file for the rest]"
                files.append((name, body))
        if not files:
            return text
        return text + "".join(f'\n\n<file path="{name}">\n{body}\n</file>' for name, body in files)

    def shell(self, command):
        """!command: you run it yourself; the model sees the output next time."""
        output, code = run_shell(command, self.root)
        self.messages.append({"role": "user", "content":
                              f"(I ran this myself: `{command}`, exit code {code})\n```\n{clip(output)}\n```"})
        self.save_log()
        return output, code

    def undo(self):
        return self.tools.undo()
