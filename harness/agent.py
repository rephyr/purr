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
from .limits import LOCAL, Limits
from . import checks
from .tools import Tools, clip, run_shell, schemas

SYSTEM = """You are {model}, an AI model, working as a coding agent. You are running inside purr, \
a small terminal program that gives you tools and shows your replies to the user. purr is only \
the program around you; it is not you. If someone asks who or what you are, say you are {model} \
(served by {provider}) running in purr. Don't invent a name, persona or backstory, and don't claim \
to be a different model.

Environment
- Project folder: {root} (use paths relative to it, like src/app.py, not full paths)
- Today: {date}
- System: Linux

Tools
- {look} to look around; edit_file, write_file, run to make changes.
- todo: only for work with several steps (never for questions or chat): keep a short task list and update it as you go.{task}
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

ASK_TOOLS = """Tools
- {look} to look around. This is ask mode: you can't change files or run commands.
- Explain, answer and plan. If something should change, say exactly where and what (file, \
line, the new code) instead of doing it."""

CHAT = """You are {model}, an AI model, chatting with the user inside purr, a small terminal \
program. purr is only the program around you; it is not you. If someone asks who or what you are, \
say you are {model} (served by {provider}). Don't invent a name, persona or backstory.

Be warm, natural and honest. Keep replies fairly short unless the user wants more, and say so \
when you don't know something. In this mode you can't see or change any files. Today is {date}."""

CREATE = """You are {model}, an AI model, and the user's creative partner inside purr, a small \
terminal program. purr is only the program around you; it is not you. If someone asks who you \
are, say you are {model} (served by {provider}).

Brainstorm, imagine and write with the user: names, ideas, game designs, stories, plans. Offer a \
few different directions rather than one, be specific and playful, build on what the user likes, \
and ask a question back when it would help. In this mode you can't see or change any files. \
Today is {date}."""

PLAN = """You are planning work for a small local coding model. Look around the project with \
your read tools first. Break the request below into tickets the model can do on its own, one at a \
time and in order; together they must cover the whole request. Don't do the work yourself.

Reply with only the tickets, each one exactly like this:

## A short title
Files: the exact paths it touches (one to three)
Change: what to do, naming the functions or classes to add or change
Done when: a command to run, or a fact that must be true

Rules:
- Each ticket touches one to three files and is small enough for the model to finish in one go.
- Each ticket runs in a fresh chat and can't see the others, so it must be self-contained.
- Order the tickets so each one builds on the last.
- The project's tests must still pass after every ticket, so put a function and its first caller \
in the same ticket.

The request:
{request}"""

TICKET_WORK = """(purr: plan ticket {i}/{total}. Do this ticket only; don't work on later \
tickets. When it's done, reply with a short summary of what you changed.)

The whole plan is for: {request}

Tickets (✓ = done):
{titles}

This ticket:
{ticket}"""

# mode -> (which tools, what it's for). The TUI's chip and /mode use this.
MODES = {
    "code": ("all", "does the work: reads, edits, runs"),
    "ask": ("read", "looks at the project and explains, never changes anything"),
    "plan": ("all", "a big model splits the task into tickets, then a small one works through them"),
    "chat": ("none", "just talking, no tools"),
    "create": ("none", "brainstorming and writing, no tools, a bit more random"),
}

EMPTY_NUDGE = """(purr: your reply was empty. Look at the last results you got: if anything looks \
wrong, fix it now; if everything is done, give a short summary of what you changed.)"""

FINAL_CHECK_LIGHT = """(purr: before you finish, read the user's request again. Is every part done, \
including any tests they asked for, and checked where you can? If not, do it now; otherwise reply with \
a short summary of what you changed.)"""

REMIND_EVERY = 8  # model calls between reminders of the request (reminders = false turns them off)

FINAL_CHECK = """(purr: before you finish, read the user's request again and go through it point by \
point. Is every part done, including any tests they asked for, and did you check it (run the tests \
or the code) where you can? Then think of 2 or 3 edge cases the request implies but nobody spelled \
out (empty input, a value that only shows up in one place, duplicates, the exact boundary of a \
limit) and quickly try them, for example with python3 -c. If something is missing, untested or \
breaks, fix it now. If everything is really done, reply with a short summary of what you changed.)"""

REFINE_MODES = ("auto", "on", "off")

REFINE = """You turn a user's short or vague request into a clear task for a coding agent that \
works in this project. Use only what you can see below: don't invent files, functions or \
requirements. Where the request is unclear, keep that part general instead of guessing. When tests \
fail, don't decide whether the code or the test is wrong: say to find out why they fail. Don't \
narrow the request either: if it doesn't say what exactly to return or include, don't decide it.

Write it like this, short:
Task: one or two sentences
Where: the files that most likely matter (from the list)
Steps: 2 to 5 short steps
Done when: how to check it worked (tests to run, or what should be true)

Reply with only that.

Project files:
{files}
{notes}
The user's request:
{request}"""

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
RETRY_WAITS = [2, 5, 10]  # seconds between tries when the server hiccups
REPEAT_NUDGE = 2  # same tool call this many times: tell the model to stop repeating


def _notes(path, label):
    if not path.is_file():
        return ""
    notes = path.read_text(errors="replace")
    if len(notes) > NOTES_MAX:
        notes = notes[:NOTES_MAX] + "\n[notes cut short]"
    return f"\n\n{label}:\n{notes}"


TASK_LINE = ("\n- task: send a helper to explore many files or the web and report back, "
             "so this chat stays small.")


def system_prompt(root, model_id, provider, hidden=(), mode="code"):
    """hidden: tools this model isn't offered, so the prompt doesn't mention them either.
    mode: code (all tools), ask (look only), chat or create (no tools, their own prompt)."""
    date = datetime.date.today().isoformat()
    if mode in ("chat", "create"):  # no project, no tools: a short prompt leaves room to talk
        return (CHAT if mode == "chat" else CREATE).format(model=model_id, provider=provider, date=date)
    look = ", ".join(t for t in ("read_file", "list_files", "grep", "fetch_url") if t not in hidden)
    text = SYSTEM.format(model=model_id, provider=provider, root=root, look=look,
                         task="" if "task" in hidden else TASK_LINE, date=date)
    if mode == "ask":
        start, end = text.index("Tools\n"), text.index("\n\nHow to work")
        text = text[:start] + ASK_TOOLS.format(look=look) + text[end:]
    text += _notes(GLOBAL_NOTES, "The user's notes for every project")
    for name in NOTES_FILES:
        text += _notes(Path(root) / name, f"Project notes ({name})")
    return text


def plain_history(messages):
    """The chat without tool calls, for chat and create mode: APIs refuse tool messages when
    no tools are offered, so earlier tool use becomes plain text."""
    out, names = [], {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = "; ".join(f"{c['function']['name']}({c['function']['arguments'][:200]})" for c in m["tool_calls"])
            names.update({c["id"]: c["function"]["name"] for c in m["tool_calls"]})
            out.append({"role": "assistant", "content": f"{m.get('content') or ''}\n[used tools: {calls}]".strip()})
        elif m.get("role") == "tool":
            out.append({"role": "user", "content": f"(result of {names.get(m.get('tool_call_id'), 'a tool')}: "
                                                   f"{clip(m.get('content') or '', 1500)})"})
        else:
            out.append({k: m[k] for k in ("role", "content") if k in m})
    return out


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


def _stable(raw):
    """A tool call's arguments, normalised so the same call matches itself."""
    try:
        return json.dumps(json.loads(raw or "{}"), sort_keys=True)
    except ValueError:
        return raw or ""


def _looks_failed(result):
    """Whether a tool result reads like a failure (so a repeat is worth stopping)."""
    text = (result or "").lstrip()
    if text.startswith("error:"):
        return True
    m = re.search(r"\[exit code (-?\d+)\]", result or "")
    return bool(m and m.group(1) != "0")


TICKET_SPLIT = re.compile(r"^##\s+(.+?)\s*$", re.M)
FILE_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def parse_tickets(text):
    """The planner's reply -> [(title, body)]. Only "## " starts a ticket; a leading "# Plan"
    or intro before the first one is dropped, and "###" subheadings (Files, Done when, ...) stay
    inside the ticket they belong to. If it wrote no "## " headings, the whole reply is one ticket."""
    text = text or ""
    heads = list(TICKET_SPLIT.finditer(text))
    tickets = []
    for i, m in enumerate(heads):
        title = m.group(1).strip().strip("*#:").strip()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        if title:
            tickets.append((title, text[m.end():end].strip()))
    if not tickets:
        stripped = text.strip()
        if stripped:
            lines = stripped.splitlines()
            title = lines[0].lstrip("#*- ").strip() or "ticket"
            tickets.append((title, "\n".join(lines[1:]).strip() or stripped))
    return tickets


def ticket_body(text):
    """A ticket file without its "# title" line (the title is shown on its own)."""
    return FILE_TITLE.sub("", text or "", count=1).strip()


FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+)|^(?:FAIL|ERROR): (.+?)\s*$", re.M)


def failed_tests(output):
    """The failing test names in pytest or unittest output (empty when it names none)."""
    return {a or b for a, b in FAILED_TEST.findall(output or "")}


def ticket_title(text, fallback="ticket"):
    """The "# title" line of a ticket file (for the progress line and the record)."""
    m = FILE_TITLE.search(text or "")
    return m.group(1).strip() if m else fallback


def slug(title):
    """A ticket title -> a short, safe file name."""
    name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return name[:40].rstrip("-") or "ticket"


def one_line(text, limit=600):
    """A request squeezed onto one line, for the executor's small context."""
    return " ".join((text or "").split())[:limit]


def ensure_purr_gitignore(root):
    """.purr/.gitignore with "*", so tickets are never committed in any project."""
    purr = Path(root) / ".purr"
    purr.mkdir(parents=True, exist_ok=True)
    ignore = purr / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")


def archive_tickets(root):
    """Move the current .purr/tickets/*.md to .purr/tickets/old/<timestamp>/ (not deleted).
    Returns that folder, or None when there was no earlier plan."""
    folder = Path(root) / ".purr/tickets"
    old = list(folder.glob("*.md"))
    if not old:
        return None
    dest = folder / "old" / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for p in old:
        p.replace(dest / p.name)
    return dest


def write_tickets(root, tickets):
    """Write a plan to .purr/tickets/NN-slug.md. An earlier plan is archived (not deleted).
    Returns the paths, in order."""
    folder = Path(root) / ".purr/tickets"
    folder.mkdir(parents=True, exist_ok=True)
    ensure_purr_gitignore(root)
    archive_tickets(root)
    paths = []
    for i, (title, body) in enumerate(tickets, 1):
        path = folder / f"{i:02d}-{slug(title)}.md"
        path.write_text(f"# {title}\n\n{body}\n")
        paths.append(path)
    return paths


def read_tickets(root):
    """The tickets on disk, sorted by file name, read fresh: [(path, text), ...]."""
    folder = Path(root) / ".purr/tickets"
    if not folder.is_dir():
        return []
    return [(p, p.read_text(errors="replace")) for p in sorted(folder.glob("*.md"))]


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

    def plan_review(self, tickets, folder):
        return "run"  # a helper never plans, but a view needs this to exist

    def activity(self, what, detail=""):
        if what != "thinking":
            self.view.activity(what, detail)

    def tool_result(self, name, args, result): pass

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
        self.tools.code_checks = config.get("code_checks", True)
        self.tools.read_before_edit = config.get("read_before_edit", True)
        self.mode = "code"       # code, ask, plan, chat or create (MODES)
        # /refine: "auto" rewrites a short first message into a clear task (you approve it),
        # "on" every message, "off" none. purr bench measured +2 solved hard tasks from vague asks.
        # Unset, it follows the model (auto for local models, off for API ones); /refine or
        # refine = "..." in config.toml pins it.
        self.refine_pinned = config.get("refine") in REFINE_MODES
        self.refine_mode = config["refine"] if self.refine_pinned else "auto"
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
        self.limits = Limits.for_model(model)
        if not getattr(self, "refine_pinned", True):
            self.refine_mode = "auto" if self.limits.helpers == "full" else "off"
        if getattr(self, "tools", None):
            self.tools.limits = self.limits  # 32k and 1M models need different caps
        if getattr(self, "messages", None):
            # the system prompt names the model, so it changes with it
            self.messages[0] = {"role": "system", "content": self._system()}

    def set_mode(self, mode):
        """code: every tool. ask: look but never change. chat / create: no tools at all."""
        if mode not in MODES:
            raise KeyError(f"no mode called {mode!r}. Have: {', '.join(MODES)}")
        self.mode = mode
        self.tools.read_only = self.helper or mode == "ask"  # enforced, not just asked for
        self.tools.no_tools = MODES[mode][0] == "none"
        if getattr(self, "messages", None):
            self.messages[0] = {"role": "system", "content": self._system()}

    def should_refine(self, text):
        """Whether this message should go through refine first (see refine_mode)."""
        if self.mode != "code" or self.refine_mode == "off" or text.startswith(("/", "!")):
            return False
        if self.refine_mode == "on":
            return True
        words = len(text.split())
        first = not any(m.get("role") == "user" for m in self.messages)
        # auto: a short first message is a new task said briefly; follow-ups need the chat to make
        # sense (refine doesn't see it) and one-word replies ("yes", "continue") aren't tasks
        return first and 3 <= words <= 15

    def refine(self, text):
        """Your request, rewritten by the same model into a clear task (Task / Where / Steps /
        Done when) using the project's file list. The chat itself doesn't change."""
        self.view.activity("refining", "making your message clearer")
        files = self.tools.t_list_files(".").splitlines()
        listing = "\n".join(files[:80]) + (f"\n… {len(files) - 80} more files" if len(files) > 80 else "")
        notes = _notes(Path(self.root) / "AGENTS.md", "Project notes (AGENTS.md)")[:3000]
        prompt = REFINE.format(files=listing, notes=notes + "\n" if notes else "", request=text)
        reply = self._call([{"role": "user", "content": prompt}], tools=False, quiet=True)
        self._count(reply["usage"])
        return reply["text"].strip()

    # ---- plan mode: a big model writes tickets, a small one does them one at a time (MODES) ----

    def plan_turn(self, text):
        """Split the request into tickets (a big model, .purr/tickets/), let the user approve or
        edit them, then hand each ticket to the executor (a small local model) one at a time. Each
        ticket runs in a fresh agent, so the small model's context holds only the ticket in front
        of it, not the whole task."""
        self.turn_stats = {"start": time.monotonic(), "out": 0, "gen": 0.0, "calls": 0, "model_s": 0.0}
        self.title = self.title or " ".join(text.split())[:70]
        plan = self.config.get("plan", {})
        planner_name = plan.get("planner") or self.model_name
        executor_name = plan.get("executor") or "qwen3_6-iq3"
        self.view.note(f"planning with {planner_name}, then {executor_name} takes the tickets", "info")
        self.messages.append({"role": "user", "content": text})
        tickets = self._make_tickets(text, planner_name)
        if self.stopping():
            self.view.note("stopped while planning; no tickets written", "warn")
            self._plan_done("stopped while planning", "■")
            return
        if not tickets:
            self.view.note("the planner came back with no tickets, so there is nothing to do", "error")
            self.messages.append({"role": "assistant", "content": "(purr: the planner wrote no tickets)"})
            self._plan_done("0 done, 0 failed/not run", "✗")
            return
        write_tickets(self.root, tickets)
        (Path(self.root) / ".purr/tickets/request.txt").write_text(text + "\n")
        listing = "\n\n".join(f"## {title}\n{body}" for title, body in tickets)
        self.messages.append({"role": "assistant",
                              "content": f"Plan ({len(tickets)} tickets):\n\n{listing}"})
        self.view.note(f"wrote {len(tickets)} tickets to .purr/tickets/", "info")
        if not self._review_plan():
            self.view.note("the tickets are ready; /plan run starts them when you are", "info")
            self._plan_done(f"0 done, {len(tickets)} not run", "■")
            return
        self.run_tickets(1, text, executor_name)

    def _make_tickets(self, text, planner_name):
        """Ask the planner (a big model, read-only so it can look around) for the ticket list."""
        try:
            planner = Agent(self.config, self.root, planner_name, self.view, parent=self)
        except KeyError as e:  # no key / no such model: plan with what we have rather than fail
            self.view.note(f"can't use {planner_name} for planning ({e.args[0]}), "
                           f"using {self.model_name}", "warn")
            planner = Agent(self.config, self.root, self.model_name, self.view, parent=self)
        planner.set_mode("ask")
        planner.title = f"planning: {' '.join(text.split())[:60]}"
        planner.turn(PLAN.format(request=text))
        answers = [m.get("content") for m in planner.messages
                   if m.get("role") == "assistant" and m.get("content")]
        return parse_tickets(answers[-1] if answers else "")

    def _review_plan(self):
        """Show the tickets on disk and wait: run, cancel, or let the user edit them first.
        Returns True to run, False to stop (the files stay either way)."""
        while True:
            tickets = [{"path": str(p), "title": ticket_title(text, p.stem), "body": ticket_body(text)}
                       for p, text in read_tickets(self.root)]
            if not tickets:
                self.view.note("no tickets to run", "error")
                return False
            choice = self.view.plan_review(tickets, str(Path(self.root) / ".purr/tickets"))
            if choice == "run":
                return True
            if choice == "cancel":
                self.view.note("kept the tickets; nothing was run", "info")
                return False
            # "edit": the user changes the files, then we read them again

    def run_plan(self, start=1):
        """`/plan run [N]`: run the tickets already in .purr/tickets/ from ticket N, without
        planning again. The files are read fresh, so any edits the user made count."""
        self.stop_flag = False  # an Esc on an earlier answer mustn't stop this before it starts
        self.turn_stats = {"start": time.monotonic(), "out": 0, "gen": 0.0, "calls": 0, "model_s": 0.0}
        self.title = self.title or "plan"
        self.messages.append({"role": "user", "content": f"(purr: run the plan tickets from {start})"})
        self.run_tickets(start, self._saved_request())

    def run_tickets(self, start=1, request="", executor_name=None):
        """Run the tickets in .purr/tickets/ from `start` (1-based), each in a fresh agent. Reads
        every ticket file again right before it runs, so the user's edits count. The project's
        tests are run after each ticket; the first failure stops the plan and is reported. Keeps a
        record in the parent chat and saves it."""
        executor_name = executor_name or self.config.get("plan", {}).get("executor") or "qwen3_6-iq3"
        if not getattr(self, "turn_stats", None):
            self.turn_stats = {"start": time.monotonic(), "out": 0, "gen": 0.0, "calls": 0, "model_s": 0.0}
        tickets = read_tickets(self.root)
        total = len(tickets)
        if not tickets:
            self.view.note("no tickets in .purr/tickets/; use /plan <task> to write some", "error")
            return
        if not 1 <= start <= total:
            self.view.note(f"there is no ticket {start} (there are {total})", "error")
            return
        request = one_line(request or self._saved_request())
        titles = [ticket_title(text, p.stem) for p, text in tickets]
        attempted = total - (start - 1)
        self.tools.begin_turn()  # one undo frame for the whole plan
        cmd = checks.test_command(self.root)
        if cmd and "pytest" in cmd:
            cmd = re.sub(r"\s-x\b", "", cmd)  # run every test, so old failures don't hide new ones
        baseline = self._test_baseline(cmd)
        plan_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        done, end = 0, None  # end: why the plan stopped early ("stopped", "failed", "error")
        for idx in range(start - 1, total):
            if self.stopping():
                self.view.note("stopped before the next ticket", "warn")
                end = "stopped"
                break
            path = tickets[idx][0]
            text = path.read_text(errors="replace")  # re-read: the user may have edited it
            title = ticket_title(text, path.stem)
            self.view.note(f"♡ ticket {idx + 1}/{total}: {title}", "info")
            try:
                worker = Agent(self.config, self.root, executor_name, self.view, parent=self)
            except KeyError as e:
                self.view.note(f"can't hand out the tickets: {e.args[0]}", "error")
                end = "error"
                break
            worker.tools.trust_all = self.tools.trust_all
            worker.tools.always = self.tools.always            # shared: "always" carries between tickets
            worker.tools.always_run = self.tools.always_run
            worker.title = f"ticket {idx + 1}/{total}: {title}"
            worker.log_path = LOG_DIR / f"plan_{plan_stamp}_ticket-{idx + 1:02d}.json"
            worker.turn(TICKET_WORK.format(i=idx + 1, total=total, request=request,
                                           titles=self._ticket_lines(titles, idx), ticket=text))
            self._merge_undo(worker)
            if self.stopping():  # stopped half way: not checked, not done
                ok, line, end = False, f"■ ticket {idx + 1}/{total}: {title} (stopped, not checked)", "stopped"
            else:
                ok, line, baseline = self._ticket_result(idx + 1, total, title, cmd, baseline)
                end = None if ok else "failed"
            self.messages.append({"role": "assistant", "content": line})
            self.save_log()
            if not ok:
                break
            done += 1
        left = attempted - done
        if end == "stopped":
            self._plan_done(f"{done} done, {left} stopped/not run", "■")
        elif end == "failed":
            self._plan_done(f"{done} done, 1 failed, {left - 1} not run", "✗")
        elif end == "error":
            self._plan_done(f"{done} done, {left} not run", "✗")
        else:
            self._plan_done(f"{done} done", "✓")

    def _ticket_lines(self, titles, idx):
        """The ticket list for the executor: ✓ for the ones already done, the current one next."""
        return "\n".join(f"{'✓' if n < idx else '○'} {n + 1}. {title}"
                         for n, title in enumerate(titles))

    def _merge_undo(self, worker):
        """Put a ticket's file changes on the plan's undo frame, so /undo after a plan puts back
        everything the plan changed (all tickets as one undo)."""
        if not worker.tools.undo_stack:
            return
        changes = worker.tools.undo_stack.pop()
        for p, before in changes.items():
            self.tools.undo_stack[-1].setdefault(p, before)  # keep the earliest text for /undo

    def _test_baseline(self, cmd):
        """Run the tests once before the first ticket, so a ticket is only blamed for failures it
        added, not for ones the project already had. None when there's nothing to run."""
        if not cmd:
            return None
        self.view.activity("run", cmd)
        output, code = run_shell(cmd, self.root, timeout=180)
        baseline = {"code": code, "failed": failed_tests(output)}
        if code != 0:
            known = f" ({len(baseline['failed'])} failing)" if baseline["failed"] else ""
            self.view.note(f"the tests already fail before the plan{known}; tickets are only "
                           "blamed for new failures", "warn")
        return baseline

    def _ticket_result(self, i, total, title, cmd, baseline):
        """Run the project's tests after a ticket and compare them with the baseline (the run
        before this ticket). Returns (ok, one-line record for the chat, the new baseline)."""
        name = f"ticket {i}/{total}: {title}"
        if not cmd:
            return True, f"✓ {name} (not checked: no tests found)", None
        self.view.activity("run", cmd)
        output, code = run_shell(cmd, self.root, timeout=180)
        now = {"code": code, "failed": failed_tests(output)}
        if code == 0:
            return True, f"✓ {name} (tests pass)", now
        was_failing = baseline and baseline["code"] not in (0, -1)
        if was_failing and code != -1:
            if not (now["failed"] and baseline["failed"]):
                # can't tell which tests fail, so can't tell if this ticket broke one
                return True, f"✓ {name} (not checked: the tests already failed before the plan)", now
            new = sorted(now["failed"] - baseline["failed"])
            if not new:
                return True, f"✓ {name} (no new test failures; {len(now['failed'])} failed before)", now
            why = "broke " + ", ".join(new[:5]) + (f" and {len(new) - 5} more" if len(new) > 5 else "")
        else:
            why = "tests time out" if code == -1 else "tests fail"
        tail = "\n".join(output.strip().splitlines()[-15:])
        self.view.note(f"{name} left the tests failing ({cmd}):", "error")
        for line in tail.splitlines():
            self.view.note(line[:200], "error")
        self.messages.append({"role": "assistant", "content": f"(purr ran `{cmd}`:\n{tail}\n)"})
        if why == "tests fail":
            why += ": " + (tail.splitlines()[-1] if tail else "no output")
        return False, f"✗ {name} ({why})", baseline

    def _saved_request(self):
        path = Path(self.root) / ".purr/tickets/request.txt"
        return path.read_text(errors="replace") if path.is_file() else ""

    def _plan_done(self, summary, mark="✓"):
        """mark: ✓ every ticket tried is done, ✗ one failed, ■ stopped or cancelled."""
        line = f"{mark} plan · {summary}"
        self.turn_stats["summary"] = line
        self.view.note(line, "stats")
        self.save_log()

    def _system(self):
        text = system_prompt(self.root, self.model["id"], self.model["provider"], self.limits.hidden_tools,
                             self.mode)
        return text + HELPER if self.helper else text

    def new(self):
        self.messages = [{"role": "system", "content": self._system()}]
        self.session_cost = 0.0
        self.session_out = 0
        self.last_usage = None
        self.title = ""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        self.log_path = None if self.helper else LOG_DIR / f"{stamp}.json"

    # ---- sessions ----

    def save_log(self):
        """The whole chat as JSON: for /resume, and to see exactly what the model sent and got back."""
        if not self.log_path or len(self.messages) < 2:
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(
            {"model": self.model_name, "mode": self.mode, "folder": str(self.root), "title": self.title,
             "cost": self.session_cost, "messages": self.messages,
             **({"cut_off_reply": self.cut_off} if getattr(self, "cut_off", None) else {})},
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
        if data.get("mode") in MODES:
            self.set_mode(data["mode"])
        self.messages[0] = {"role": "system", "content": self._system()}
        self.title = data.get("title", "")
        self.session_cost = data.get("cost", 0.0)
        self.last_usage = None
        self.log_path = Path(path)

    # ---- one model call ----

    def _call(self, messages=None, tools=True, quiet=False):
        msgs = messages or self.messages
        if tools and MODES[self.mode][0] == "none":
            tools, msgs = False, plain_history(msgs)
        body = {
            "model": self.model["id"],
            "messages": msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = schemas(read_only=self.helper or self.mode == "ask",
                                    read_lines=self.limits.read_lines, hidden=self.limits.hidden_tools)
        body.update(self.provider.get("body", {}))  # e.g. which OpenRouter hosts may answer
        body.update(self.model.get("body", {}))
        if self.mode == "create" and messages is None:  # a little more surprising
            body["temperature"] = min(1.2, (body.get("temperature") or 0.8) + 0.3)
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
        self.cut_off = None
        if self.mode == "plan" and not self.helper:
            return self.plan_turn(text)
        if not self.title:
            self.title = " ".join(text.split())[:70]
        self.messages.append({"role": "user", "content": self.expand(text)})
        self.tools.begin_turn()
        turn_cost = 0.0
        steps = 0
        retries = 0
        self._repeats = {}  # (tool, args) -> how often it's been called, and the last result
        self.turn_stats = {"start": time.monotonic(), "out": 0, "gen": 0.0, "calls": 0, "model_s": 0.0}
        todo_only = 0  # steps in a row that did nothing but update the task list
        empty = 0      # empty answers nudged this turn
        self._checked = False  # the final check (below) runs at most once a turn
        try:
            while True:
                steps += 1
                if steps > self.config.get("max_steps", 40):
                    ans, _ = self.view.ask(f"{steps - 1} steps so far. keep going?", allow_always=False)
                    if ans != "y":
                        break
                    steps = 1
                ctx = self.model.get("context", 0)
                if ctx and len(self.messages) > 4:
                    used = self.context_used()
                    if used > ctx * self.limits.prune_at:
                        self._prune_old_tools()
                        used = self.context_used()
                    if used > ctx * self.limits.compact_at:
                        self.compact(auto=True)
                try:
                    if (self._helper_on("reminders") and self.mode == "code" and not self.helper
                            and steps > 1 and (steps - 1) % REMIND_EVERY == 0):
                        # small models lose the goal on long tasks: say it again now and then
                        self.messages.append({"role": "user", "content":
                            f"(purr: a reminder of what the user asked, so you stay on track: {text[:800]})"})
                    self.view.activity("thinking")
                    reply = self._call()
                except ApiError as e:
                    self.view.note(f"api error: {e}", "error")
                    break
                turn_cost += self._count(reply["usage"])
                self.turn_stats["out"] += (reply["usage"] or {}).get("completion_tokens", 0)
                self.turn_stats["calls"] += 1
                usage = reply["usage"] or {}
                cached = usage.get("prompt_cache_hit_tokens") or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                self.turn_stats["in"] = self.turn_stats.get("in", 0) + (usage.get("prompt_tokens") or 0)
                self.turn_stats["cached"] = self.turn_stats.get("cached", 0) + cached
                if reply.get("provider"):
                    self.turn_stats.setdefault("providers", {})
                    self.turn_stats["providers"][reply["provider"]] = self.turn_stats["providers"].get(reply["provider"], 0) + 1
                self.turn_stats["model_s"] += reply.get("call_seconds", 0.0)
                # Ollama sends a tool call as one piece at the very end, and a short answer is over
                # in a blink: then there's no writing time to measure, so count the whole call
                gen = reply.get("gen_seconds", 0.0)
                self.turn_stats["gen"] += gen if gen >= 0.3 else reply.get("call_seconds", gen)

                if not reply["tool_calls"] and "<function=" in reply["text"]:
                    reply["tool_calls"], reply["text"] = calls_from_text(reply["text"])
                    self.tools.repairs += ["tool call written as text -> real call"] * len(reply["tool_calls"])

                # content may only be null next to tool calls: Ollama refuses an empty reply's null
                msg = {"role": "assistant", "content": reply["text"] or (None if reply["tool_calls"] else "")}
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
                    # a completely empty answer isn't "done": the model stalled (seen right after
                    # it ran checks that showed bugs). Nudge it on, at most twice a turn.
                    if not (reply["text"] or "").strip() and reply["finish"] != "length" and empty < 2:
                        empty += 1
                        self.tools.repairs.append("empty reply -> nudged to continue")
                        self.view.note("the model went quiet, nudging it on")
                        self.messages.append({"role": "user", "content": EMPTY_NUDGE})
                        continue
                    # local models sometimes write a tool call as plain text, so it never runs
                    if "tool_call>" in (reply["text"] or "") and retries < 2:
                        retries += 1
                        self.view.note("tool call came out as text, asking it to try again")
                        self.messages.append({"role": "user", "content":
                            "(purr: your tool call came out as plain text, so it did not run. "
                            "Call the tool again.)"})
                        continue
                    if self._final_check():
                        continue
                    break
                if self._run_tools(reply["tool_calls"]):
                    self.view.note("waiting for you", "info")
                    break
                nudge = self._check_repeats()
                if nudge and nudge.startswith("stop"):
                    what = "failing step" if nudge == "stop failed" else "step and getting the same result"
                    self.view.note(f"it kept repeating the same {what}, so purr stopped the turn", "error")
                    break
                if nudge:
                    self.messages.append({"role": "user", "content": nudge})
                # small models can get stuck ticking their task list forever instead of
                # stopping (it never ends the turn, since a tool call always asks for more)
                if all(c["name"] == "todo" for c in reply["tool_calls"]):
                    todo_only += 1
                    if reply["text"] and not self.tools.todos_left:
                        if self._final_check():
                            continue
                        break  # it answered and everything is done: that's the end
                    if todo_only >= 3:
                        self.view.note("it only kept updating its task list, so purr ended the turn")
                        break
                else:
                    todo_only = 0
        except Stopped as e:
            self.view.note("stopped", "warn")
            self.cut_off = e.partial  # the reply it was writing: only in the log, not the chat
        self.save_log()
        self._status(turn_cost)
        changed = len(self.tools.undo_stack[-1]) if self.tools.undo_stack else 0
        if changed and not self.helper and (self.root / ".git").exists():
            self.view.note(f"✎ {changed} file{'s' * (changed != 1)} changed · /pr makes a pull request")
        self.turn_stats["summary"] = self._turn_summary()
        self.view.note(self.turn_stats["summary"], "stats")

    def _final_check(self):
        """The model wants to stop after changing files: ask it once to go through the request
        point by point first. Small models often fix the first thing, see the old tests pass and
        say "done" with half the job left (purr bench showed it). Returns True when it asked."""
        changed = self.tools.undo_stack[-1] if self.tools.undo_stack else {}
        if (self._checked or not changed or self.helper or self.mode != "code"
                or not self.config.get("final_check", True)):
            return False
        self._checked = True
        self.view.note("♡ checking the request once more before finishing")
        check = FINAL_CHECK if self._helper_on("edge_cases") else FINAL_CHECK_LIGHT
        self.messages.append({"role": "user", "content": self._test_report() + check})
        return True

    def _helper_on(self, key):
        """A training-wheels helper (reminders, edge_cases): config.toml decides if it's set there,
        else the model's size does (on for local models, off for API ones)."""
        return self.config[key] if key in self.config else self.limits.helpers == "full"

    def _test_report(self):
        """For the final check: purr runs the project's tests itself, so the model sees the real
        result instead of trusting its own "tests pass". "" when there's nothing to run."""
        cmd = checks.test_command(self.root) if self.config.get("final_check_tests", True) else None
        if not cmd:
            return ""
        self.view.activity("run", cmd)
        self.view.tool(f"run {cmd}")
        ok, _ = self.tools._run_allowed(cmd)
        if not ok:
            return ""
        output, code = run_shell(cmd, self.root, timeout=180)
        self.view.tool_result("run", {"command": cmd}, f"{output}\n[exit code {code}]")
        tail = "\n".join(output.strip().splitlines()[-40:])
        verdict = "they pass" if code == 0 else (
            "THEY FAIL: if the failure comes from your changes or is part of the request, fix it "
            "first; if it is in code the request has nothing to do with, leave it and mention it "
            "in your answer")
        return f"(purr, not the user, ran the tests itself: `{cmd}` → exit code {code}, {verdict})\n```\n{tail}\n```\n"

    def on_my_gpu(self):
        """Speed only means something for local models: an API's depends on its load, routing, ..."""
        return self.model["provider"] in LOCAL

    def tok_per_s(self):
        """Output speed this turn: tokens written / seconds spent writing them (local models only)."""
        s = getattr(self, "turn_stats", None)
        if not self.on_my_gpu() or not s or not s["out"] or s["gen"] < 0.2:
            return None
        return s["out"] / s["gen"]

    def _turn_summary(self):
        s = self.turn_stats
        parts = [ui.duration(time.monotonic() - s["start"])]
        rate = self.tok_per_s()
        if rate:
            parts.append(f"{rate:.1f} tok/s" if rate < 10 else f"{rate:.0f} tok/s")
        if s["out"]:
            parts.append(f"{ui.short(s['out'])} tokens")
        return "✓ " + " · ".join(parts)

    def _run_tools(self, calls):
        """Runs the tool calls. Returns True when you said a plain no (the turn ends)."""
        done = 0
        self.tools.halt = False
        self._executed = []  # (name, args, result) for the repeat check
        try:
            for c in calls:
                if self.stopping():
                    raise Stopped
                if self.tools.halt:
                    result = "skipped: the user stopped to give new instructions"
                else:
                    result = self.tools.call(c["name"], c["args"])
                    self._executed.append((c["name"], c["args"], result))
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                done += 1
            return self.tools.halt
        except (KeyboardInterrupt, Stopped):
            # every tool call needs an answer, or the next request is rejected
            for c in calls[done:]:
                self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                      "content": "cancelled: the user stopped it"})
            raise

    def _check_repeats(self):
        """A small model can get stuck making the same call and learning nothing.
        Count identical calls, nudge once, then stop the turn before it eats the
        whole context. Returns a message to send, "stop failed", "stop same", or None."""
        worst, worst_name = None, ""
        for name, args, result in getattr(self, "_executed", []):
            if name == "todo":  # updating the task list over and over is normal
                continue
            key = (name, _stable(args))
            failed = _looks_failed(result)
            rec = self._repeats.get(key)
            if rec and rec["result"] == result:
                # the same call came back byte-for-byte the same: it is going nowhere
                rec["n"] += 1
                rec["failed"] = rec["failed"] or failed
            else:
                rec = {"n": 1, "result": result, "failed": failed, "warned": False}
                self._repeats[key] = rec
            if worst is None or rec["n"] > worst["n"]:
                worst, worst_name = rec, name
        if not worst:
            return None
        # a failing call gets repeat_limit tries; one that works (re-reading a file) gets two more
        if worst["n"] >= self.limits.repeat_limit + (0 if worst["failed"] else 2):
            return "stop failed" if worst["failed"] else "stop same"
        if worst["n"] >= REPEAT_NUDGE and not worst["warned"]:
            worst["warned"] = True
            what = "keeps failing" if worst["failed"] else "returns the same thing"
            return (f"(purr: you have now called `{worst_name}` with the same arguments "
                    f"{worst['n']} times and it {what}. Don't repeat it. Read the result, "
                    f"try a different approach or different arguments, or stop and tell the "
                    f"user what is wrong.)")
        return None

    def _prune_old_tools(self):
        """Free room cheaply by eliding old tool output, keeping the newest results whole.
        Cheaper than a full compaction: no model call, and it keeps the messages."""
        tools = [m for m in self.messages if m.get("role") == "tool"]
        older = tools[:max(0, len(tools) - self.limits.keep_recent_tools)]
        # the stub names the call it came from, so the model knows what to redo if it needs it
        calls = {c["id"]: c["function"] for m in self.messages if m.get("role") == "assistant"
                 for c in m.get("tool_calls") or []}
        freed = 0
        for m in older:
            content = m.get("content") or ""
            if len(content) <= 400 or content.startswith("[old output of"):
                continue
            fn = calls.get(m.get("tool_call_id"), {})
            call = f"{fn.get('name', 'a tool')}({(fn.get('arguments') or '')[:160]})"
            m["content"] = (f"[old output of {call} removed to save room "
                            f"({len(content)} characters); run it again if you need it]")
            freed += len(content) - len(m["content"])
        if freed:
            self.last_usage = None  # the old count is stale; use the fresh size guess
            self.view.note(f"trimmed old tool output to save room ({ui.short(freed)} characters)", "info")
        return freed

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
        """The chat as plain text for the summary. Long tool results are cut short
        (harder on a small context, which can't afford a long transcript)."""
        cut = min(4000, max(800, self.limits.tool_output // 6))  # 32k: ~1.1k chars, 1M: 4k
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
                out.append("TOOL RESULT: " + (content if len(content) < cut else content[:cut] + " [...]"))
        text = "\n\n".join(out)
        # the whole summary request must fit too: about half the context (4 chars a token),
        # keeping the start (what the user asked for) and the most recent work
        budget = self.limits.context * 2
        if len(text) > budget:
            head = budget // 4
            text = (text[:head] + f"\n\n[... {len(text) - budget} characters of the middle left out ...]\n\n"
                    + text[-(budget - head):])
        return text

    def compact(self, auto=False):
        """Swap the chat for a summary of it. Returns (tokens before, tokens after) or None."""
        if len(self.messages) < 3:
            self.view.note("nothing to compact yet", "info")
            return None
        before = self.context_used()
        self.view.note("compacting the chat (summarising it to make room)…", "info")
        self.view.activity("compacting")
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
                if len(body) > self.limits.attach_max:
                    body = body[:self.limits.attach_max] + "\n[file cut short; use read_file for the rest]"
                files.append((name, body))
        if not files:
            return text
        return text + "".join(f'\n\n<file path="{name}">\n{body}\n</file>' for name, body in files)

    def shell(self, command):
        """!command: you run it yourself; the model sees the output next time."""
        output, code = run_shell(command, self.root)
        self.messages.append({"role": "user", "content":
                              f"(I ran this myself: `{command}`, exit code {code})\n```\n"
                              f"{clip(output, self.limits.tool_output)}\n```"})
        self.save_log()
        return output, code

    def undo(self):
        return self.tools.undo()
