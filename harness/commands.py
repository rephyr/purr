"""Slash commands, shared by the plain mode and the TUI.

Your own commands: a markdown file per command in ~/.config/purr/commands/ (every project) or
<project>/.purr/commands/ (just that one). The file name is the command, the text is what gets
sent to the model, and $ARGUMENTS is replaced by whatever you type after the command.
An optional first line "description: ..." is shown in the / menu.
"""

from pathlib import Path

from . import ui
from .settings import CONFIG_DIR
from .agent import list_sessions

# name: (argument hint, what it does). The TUI's / menu is built from this.
COMMANDS = {
    "help": ("", "show the commands"),
    "models": ("", "pick a model from a list"),
    "model": ("[name]", "switch to a model by name"),
    "clear": ("", "forget the chat, start fresh"),
    "compact": ("", "summarise the chat to free up room"),
    "resume": ("", "carry on an earlier chat"),
    "undo": ("", "put back the files the last answer changed"),
    "cost": ("", "money spent this session"),
    "private": ("", "fresh chat, local models only, no web, nothing saved (again to leave)"),
    "free": ("[check|reset]", "the free models /model free picks from; check tests them, reset forgets resting"),
    "stats": ("", "fun numbers about you and purr"),
    "files": ("", "browse the project and what changed this session, edit files (ctrl+t)"),
    "trust": ("", "allow edits and commands without asking (toggle)"),
    "theme": ("[name]", "change purr's colours"),
    "cat": ("[name <new name>]", "your cat: her stats, or give her a new name"),
    "mode": ("[name]", "what purr may do: code, ask (look only), learn, pair, plan, chat, create, or one of your agents"),
    "agent": ("[name | new <what it does> | reload]", "your own agents: list them, switch to one, or have one written"),
    "plan": ("[task | run [N]]", "a big model writes tickets, a small one does them one at a time"),
    "refine": ("[auto|on|off]", "rewrite your message into a clear task first (you approve it)"),
    "pr": ("", "commit the changes and open a GitHub pull request (you check it first)"),
    "setup": ("", "set purr up again: local models, API keys, your model, the cat"),
    "quit": ("", "leave"),
}
ALIASES = {"agents": "agent", "changes": "files", "browse": "files", "new": "clear", "exit": "quit", "q": "quit", "continue": "resume"}

def agent_command(agent, arg):
    """/agent: list your agents, switch to one, have a new one written (new ...), or read them again."""
    from . import agents as agent_files
    word, _, rest = arg.partition(" ")
    if word == "new":
        if not rest.strip():
            return [("error", "say what it should do: /agent new reviews my code and points out bugs")]
        return {"agent_new": rest.strip()}
    if word == "reload":
        agent.reload_agents()
        return [("info", f"read the agent files again: {len(agent.agents)} agent{'s' * (len(agent.agents) != 1)}")]
    if arg:
        try:
            moved = agent.switch_mode(arg)
        except KeyError as e:
            return [("error", e.args[0])]
        return [("info", f"agent: {arg}, {agent.agents[arg]['description']}" if arg in agent.agents
                 else f"mode: {arg}")] + ([("info", moved)] if moved else [])
    if not agent.agents:
        where = agent_files.settings.CONFIG_DIR / "agents"
        return [("info", "no agents of your own yet. /agent new <what it should do> has one written, or put a"),
                ("info", f"Markdown file in {where} (or .purr/agents/ in a project): see harness/agents.py")]
    lines = []
    for n, a in agent.agents.items():
        tools = a["tools"] if isinstance(a["tools"], str) else ", ".join(sorted(a["tools"]))
        origin = "" if a["origin"] in ("purr", "project") else f"  (from {a['origin']})"
        lines.append(("info", f"{'♡ ' if n == agent.mode else '  '}{n:<16} {a['description'][:70]}  [{tools}]{origin}"))
    return lines + [("info", "  /agent <name> switches · /agent new <what it does> · /agent reload")]


INIT = """Look through this project and write (or update) an AGENTS.md file in its root folder. \
It is read by coding agents at the start of every chat, so it should help them work here well.
Find out and write down, briefly:
- what the project is and the main folders and files
- how to build, run and test it (exact commands)
- code style and conventions you can see (naming, indentation, patterns)
- anything surprising a newcomer would trip over
Keep it short (under about 80 lines) and only write what you actually found. \
If AGENTS.md already exists, improve it instead of starting over.
$ARGUMENTS"""

GLOBAL_DIR = CONFIG_DIR / "commands"


def custom(root):
    """{name: (description, template)} from the command folders, plus the built-in /init."""
    found = {"init": ("write an AGENTS.md for this project", INIT)}
    for folder in (GLOBAL_DIR, Path(root) / ".purr/commands"):
        for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            text = p.read_text(errors="replace")
            desc = "your command"
            first, _, rest = text.partition("\n")
            if first.lower().startswith("description:"):
                desc, text = first.split(":", 1)[1].strip(), rest
            found[p.stem] = (desc, text.strip())
    return found


def parse(text):
    name, _, arg = text.strip().lstrip("/").partition(" ")
    return ALIASES.get(name, name), arg.strip()


def run(agent, text):
    """Run a /command. Returns one of:
    None                        quit
    [(kind, line), ...]         lines to show (kind: info, warn, error, dim)
    {"send": prompt}            send this prompt to the model as your message
    {"plan_run": n}             run the plan tickets from ticket n
    """
    name, arg = parse(text)
    templates = custom(agent.root)
    if name not in COMMANDS and name in templates:
        return {"send": templates[name][1].replace("$ARGUMENTS", arg).strip()}
    if name == "quit":
        return None
    if name == "help":
        lines = [("info", f"/{n} {a}".ljust(16) + d) for n, (a, d) in COMMANDS.items()]
        lines += [("info", f"/{n}".ljust(16) + d) for n, (d, _) in templates.items()]
        lines.append(("dim", "@file attaches a file   !command runs it yourself"))
        return lines
    if name == "private":
        try:
            pick = agent.set_private(not agent.private)
        except KeyError as e:
            return [("error", e.args[0])]
        if pick:
            return [("info", f"🔒 private: {pick} on your own GPU, no web, and this chat won't be saved. "
                             "Nothing leaves this computer (commands the model asks to run still need your yes). "
                             "/private again to leave")]
        return [("info", "left private mode: the private chat is gone, fresh start")]
    if name in ("model", "models"):
        if not arg:
            return [("info", f"{'♡' if m == agent.model_name else ' '} {m:<18} {spec['id']:<22} "
                     f"{ui.cost_kind(spec)}")
                    for m, spec in agent.config["models"].items()
                    if not agent.private or m in agent.local_models()]
        try:
            agent.set_model(arg)
            return [("info", f"now using {arg}")]
        except KeyError as e:
            return [("error", e.args[0])]
    if name == "clear":
        agent.new()
        return [("info", "fresh start")]
    if name == "compact":
        agent.compact()
        return []
    if name == "undo":
        paths = agent.undo()
        if not paths:
            return [("info", "nothing to undo (commands the model ran can't be undone)")]
        return [("info", "put back: " + ", ".join(paths))]
    if name == "resume":
        sessions = list_sessions(agent.root)
        if not arg:
            if not sessions:
                return [("info", "no earlier chats in this folder")]
            return [("info", f"{i:>2}  {s['when']:%d.%m %H:%M}  {s['title']}")
                    for i, s in enumerate(sessions[:20], 1)] + [("dim", "/resume <number> to carry one on")]
        try:
            chosen = sessions[int(arg) - 1]
        except (ValueError, IndexError):
            return [("error", f"no chat number {arg}")]
        agent.load(chosen["path"])
        return [("info", f"carrying on: {chosen['title']}")]
    if name == "cost":
        return [("info", f"session: ${agent.session_cost:.4f}, "
                 f"{ui.short(agent.session_out)} tokens written")]
    if name == "free" and agent.private:
        return [("error", "private mode: the free models are API models, so they're off (/private to leave)")]
    if name == "pr" and agent.private:
        return [("error", "private mode: /pr would push your code to GitHub (/private to leave first)")]
    if name == "free":
        from .free import FreeRouter
        router = agent.router or FreeRouter(agent.config)
        if arg == "reset":
            router.reset()
            return [("info", "every free model is ready again")]
        if arg == "check":
            from .free import check
            lines = []
            check(agent.config, router, lambda kind, line: lines.append((kind, line)))
            return lines
        lines = [("info", f"free models for {agent.mode} mode, best first"
                  + ("" if agent.router else "   (not on: /model free turns it on)"))]
        for n, state in router.status(agent.mode, agent.model_name if agent.router else None):
            lines.append(("warn" if state.startswith("resting") else "info",
                          f"{'♡' if state == 'in use' else ' '} {n:<20} {state}"))
        return lines
    if name == "stats":
        from . import stats
        return stats.run()
    if name == "trust":
        agent.tools.trust_all = not agent.tools.trust_all
        return [("warn", "trusting everything (no questions)") if agent.tools.trust_all
                else ("info", "asking before edits and commands again")]
    if name == "mode":
        if arg:
            try:
                moved = agent.switch_mode(arg)
            except KeyError as e:
                return [("error", e.args[0])]
            return [("info", f"mode: {arg}, {agent.modes()[arg]}")] + ([("info", moved)] if moved else [])
        return [("info", ("♡ " if m == agent.mode else "  ") + f"{m:<7} {what}") for m, what in agent.modes().items()]
    if name == "agent":
        return agent_command(agent, arg)
    if name == "plan":
        from .agent import MODES
        moved = agent.switch_mode("plan") if agent.mode != "plan" else None
        if arg.startswith("run"):
            rest = arg[3:].strip()
            try:
                start = int(rest) if rest else 1
            except ValueError:
                return [("error", f"'{rest}' is not a ticket number")]
            if start < 1:
                return [("error", "ticket numbers start at 1")]
            return {"plan_run": start}
        if arg:
            return {"send": arg}
        return [("info", "plan mode: " + MODES["plan"][1]
                 + "   (/plan run [N] runs the tickets already written)")] + ([("info", moved)] if moved else [])
    if name == "refine":
        from .agent import REFINE_MODES
        if arg in REFINE_MODES:
            agent.refine_mode, agent.refine_pinned = arg, True
        what = {"auto": "refining short first messages (a new task said briefly); you see it before it's sent",
                "on": "refining every message first; you see it before it's sent",
                "off": "sending your messages as they are"}[agent.refine_mode]
        return [("info", f"refine {agent.refine_mode}: {what}" + ("" if arg else "   (/refine auto|on|off)"))]
    if name == "pr":
        return {"pr": True}
    if name in ("theme", "cat", "files"):
        return [("info", "that's for the full-screen mode")]
    if name == "setup":
        return [("info", "run `purr setup` in your terminal: it opens the setup window")]
    return [("error", f"unknown command /{name}, try /help")]
