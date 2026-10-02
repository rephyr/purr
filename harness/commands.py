"""Slash commands, shared by the plain mode and the TUI.

Your own commands: a markdown file per command in ~/.config/purr/commands/ (every project) or
<project>/.purr/commands/ (just that one). The file name is the command, the text is what gets
sent to the model, and $ARGUMENTS is replaced by whatever you type after the command.
An optional first line "description: ..." is shown in the / menu.
"""

from pathlib import Path

from . import ui
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
    "trust": ("", "allow edits and commands without asking (toggle)"),
    "theme": ("[name]", "change purr's colours"),
    "cat": ("[name <new name>]", "your cat: her stats, or give her a new name"),
    "mode": ("[code|ask|plan|chat|create]", "what purr may do: code, ask (look only), plan, chat, create"),
    "plan": ("[task | run [N]]", "a big model writes tickets, a small one does them one at a time"),
    "refine": ("[auto|on|off]", "rewrite your message into a clear task first (you approve it)"),
    "pr": ("", "commit the changes and open a GitHub pull request (you check it first)"),
    "quit": ("", "leave"),
}
ALIASES = {"new": "clear", "exit": "quit", "q": "quit", "continue": "resume"}

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

GLOBAL_DIR = Path.home() / ".config/purr/commands"


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
    if name in ("model", "models"):
        if not arg:
            return [("info", f"{'♡' if m == agent.model_name else ' '} {m:<18} {spec['id']:<22} "
                     f"{'paid' if spec.get('price') else 'local'}")
                    for m, spec in agent.config["models"].items()]
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
    if name == "trust":
        agent.tools.trust_all = not agent.tools.trust_all
        return [("warn", "trusting everything (no questions)") if agent.tools.trust_all
                else ("info", "asking before edits and commands again")]
    if name == "mode":
        from .agent import MODES
        if arg:
            try:
                agent.set_mode(arg)
            except KeyError as e:
                return [("error", e.args[0])]
            return [("info", f"mode: {arg}, {MODES[arg][1]}")]
        return [("info", ("♡ " if m == agent.mode else "  ") + f"{m:<7} {what}") for m, (_, what) in MODES.items()]
    if name == "plan":
        from .agent import MODES
        agent.set_mode("plan")
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
                 + "   (/plan run [N] runs the tickets already written)")]
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
    if name in ("theme", "cat"):
        return [("info", "that's for the full-screen mode")]
    return [("error", f"unknown command /{name}, try /help")]
