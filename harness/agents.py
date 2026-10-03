"""Your own agents: one Markdown file each, a short header and then its instructions.

    ---
    description: reviews code for bugs and says what to fix, never edits
    tools: read            # all (default) | read | none | a list: [read_file, grep, run]
    model: ds-flash-or     # optional: the model it switches to (any name /models shows)
    temperature: 0.3       # optional
    colour: "#8fd8e8"      # optional: its colour in purr (a hex colour, or pink/lilac/mint/peach/rose/cyan)
    icon: ◎                # optional: one character
    ---
    You are a careful code reviewer. ...

Where purr finds them, each a mode of its own (shift+tab, /agent <name>):
  ~/.config/purr/agents/*.md     yours, everywhere
  <project>/.purr/agents/*.md    this project's (a project's agent wins over yours by the same name)
Agents you already have for other tools work too (/agent <name>; they aren't in the shift+tab
cycle): Claude Code's (~/.claude/agents, <project>/.claude/agents) and OpenCode's
(~/.config/opencode/agent(s), <project>/.opencode/agent(s)), their tool names translated.
Plain Python, no packages.
"""

import re
from pathlib import Path

from harness import settings

PURR_DIRS = (lambda root: settings.CONFIG_DIR / "agents", lambda root: Path(root) / ".purr" / "agents")
OTHER_DIRS = (("claude code", lambda root: Path.home() / ".claude" / "agents"),
              ("claude code", lambda root: Path(root) / ".claude" / "agents"),
              ("opencode", lambda root: settings.CONFIG_DIR.parent / "opencode" / "agent"),
              ("opencode", lambda root: settings.CONFIG_DIR.parent / "opencode" / "agents"),
              ("opencode", lambda root: Path(root) / ".opencode" / "agent"),
              ("opencode", lambda root: Path(root) / ".opencode" / "agents"))
BUILT_IN = ("code", "ask", "learn", "pair", "plan", "chat", "create")  # purr's own modes keep their names

# other tools' names for purr's tools
CLAUDE_TOOLS = {"read": "read_file", "grep": "grep", "glob": "list_files", "ls": "list_files", "bash": "run",
                "edit": "edit_file", "multiedit": "edit_file", "write": "write_file", "notebookedit": "edit_file",
                "webfetch": "fetch_url", "websearch": "fetch_url", "todowrite": "todo", "task": "task"}
OPENCODE_TOOLS = {"read": ["read_file"], "grep": ["grep"], "glob": ["list_files"], "list": ["list_files"],
                  "bash": ["run", "terminal"], "edit": ["edit_file"], "write": ["write_file"], "patch": ["edit_file"],
                  "webfetch": ["fetch_url"], "todowrite": ["todo"], "todoread": [], "task": ["task"]}
CHANGES = {"write_file", "edit_file", "run", "terminal"}  # without these an agent only looks
COLOURS = {"pink": "#f5a9d0", "lilac": "#c8a2f0", "mint": "#96dcaf", "peach": "#ffb8c8", "rose": "#f0829b",
           "cyan": "#8fd8e8", "blue": "#a8b8ff", "purple": "#c8a2f0", "green": "#96dcaf", "red": "#f0829b",
           "orange": "#ffb8c8", "yellow": "#ffe4a8"}


def parse_header(text):
    """(header dict, body) from a "---" header. Understands the simple YAML these files use:
    key: value, [inline, lists], "- item" lists and one level of "key:\\n  sub: value"."""
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---\s*$", text[3:], re.M)
    if not end:
        return {}, text
    head, body = text[3:3 + end.start()], text[3 + end.end():]
    out, key = {}, None
    for raw in head.splitlines():
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith(('"', "'")) else raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if raw[:1] in (" ", "\t") and key is not None:  # under the last key: a list item or a sub-key
            item = line.strip()
            if item.startswith("- "):
                out[key] = (out[key] if isinstance(out[key], list) else []) + [_scalar(item[2:])]
            elif ":" in item:
                sub, value = item.split(":", 1)
                out[key] = out[key] if isinstance(out[key], dict) else {}
                out[key][sub.strip()] = _scalar(value)
            continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        out[key] = _scalar(value) if value else ""
    return out, body.strip()


def _scalar(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def tools_of(header, origin, all_tools):
    """"all", "read", "none", or the set of purr tool names this agent may use."""
    tools = header.get("tools")
    if tools in (None, "", "all", "*"):
        return "all"
    if tools in ("read", "look", "read-only", "readonly", "none", "no"):
        return {"look": "read", "read-only": "read", "readonly": "read", "no": "none"}.get(tools, tools)
    if isinstance(tools, dict):  # OpenCode: {write: false, bash: false}: everything but those
        allowed = set(all_tools)
        for name, on in tools.items():
            if on is False:
                allowed -= set(OPENCODE_TOOLS.get(str(name).lower(), [name]))
        return "all" if allowed == set(all_tools) else allowed
    names = tools if isinstance(tools, list) else [t for t in re.split(r"[,\s]+", str(tools)) if t]
    allowed = set()
    for name in names:
        name = str(name)
        mapped = CLAUDE_TOOLS.get(name.lower().replace("_", ""), name) if origin != "purr" or name not in all_tools \
            else name
        if mapped in all_tools:
            allowed.add(mapped)
    return allowed or "none"


def load_file(path, origin, all_tools, models=()):
    """One agent from its file, or None when it can't be one (no instructions, a reserved name)."""
    try:
        header, body = parse_header(Path(path).read_text(errors="replace"))
    except OSError:
        return None
    name = re.sub(r"[^a-z0-9_-]+", "-", str(header.get("name") or Path(path).stem).lower()).strip("-")
    if not body or not name or name in BUILT_IN:
        return None
    model = str(header.get("model") or "")
    colour = str(header.get("colour") or header.get("color") or "")
    colour = COLOURS.get(colour.lower(), colour if re.fullmatch(r"#[0-9a-fA-F]{6}", colour) else "")
    icon = str(header.get("icon") or "")[:1]
    temperature = header.get("temperature")
    return {"name": name, "description": str(header.get("description") or "your own agent")[:200],
            "tools": tools_of(header, origin, all_tools), "model": model if model in models else None,
            "temperature": float(temperature) if isinstance(temperature, (int, float)) else None,
            "colour": colour or None, "icon": icon or None, "prompt": body, "path": str(path), "origin": origin}


def load(root, all_tools, models=()):
    """{name: agent} from every place agents live. purr's own (yours, then the project's) come
    after the other tools' so they win a name clash."""
    found = {}
    for origin, where in OTHER_DIRS:
        for path in sorted(where(root).glob("*.md")) if where(root).is_dir() else ():
            agent = load_file(path, origin, all_tools, models)
            if agent:
                found[agent["name"]] = agent
    for origin, where in zip(("purr", "project"), PURR_DIRS):
        for path in sorted(where(root).glob("*.md")) if where(root).is_dir() else ():
            agent = load_file(path, "purr" if origin == "purr" else "project", all_tools, models)
            if agent:
                found[agent["name"]] = agent
    return found


def looks_only(agent):
    """It can't change anything: read-only, enforced like ask mode."""
    tools = agent["tools"]
    return tools == "read" or (isinstance(tools, set) and not tools & CHANGES)


def role(agent):
    """The agent's part of the system prompt."""
    return f"\n\nYou are working as the user's agent \"{agent['name']}\" ({agent['description']}). Its instructions:\n\n{agent['prompt']}"


# ---- /agent new: a draft from what you want it to do ----

DRAFT = """Write the file for a coding-agent role the user wants, in exactly this form and nothing else:

---
name: a short name, lowercase, letters and dashes (like test-writer)
description: one line: what it does
tools: all, read or none (read: it looks and explains but never changes files or runs commands)
colour: one of pink, lilac, mint, peach, rose, cyan, blue
icon: one character that fits it
---
The instructions, 4 to 12 short lines, written to the agent ("You are ..."): what it focuses on, how it
works step by step, what it must not do, and how it reports back. Concrete, no fluff.

What the user wants it to do: {want}"""


def draft(agent, want):
    """The file text for a new agent, written by the current model."""
    reply = agent._call([{"role": "user", "content": DRAFT.format(want=want)}], tools=False, quiet=True)
    agent._count(reply["usage"])
    text = (reply["text"] or "").strip()
    text = re.sub(r"^```\w*\n|\n```$", "", text).strip()  # a model that wraps it in a code block
    return text if text.startswith("---") else "---\nname: my-agent\ndescription: " + want[:80] + "\n---\n" + text


def save(text, folder=None):
    """Write an agent file (into ~/.config/purr/agents/ unless folder says otherwise). Returns its path."""
    header, _ = parse_header(text)
    name = re.sub(r"[^a-z0-9_-]+", "-", str(header.get("name") or "my-agent").lower()).strip("-") or "my-agent"
    if name in BUILT_IN:
        name += "-agent"
    folder = Path(folder) if folder else settings.CONFIG_DIR / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    final, n = name, 2
    while (folder / f"{final}.md").exists():
        final, n = f"{name}-{n}", n + 1
    if re.search(r"^name:.*$", text, re.M):  # the header names it as its file does
        text = re.sub(r"^name:.*$", f"name: {final}", text, count=1, flags=re.M)
    else:
        text = text.replace("---\n", f"---\nname: {final}\n", 1)
    path = folder / f"{final}.md"
    path.write_text(text.rstrip() + "\n")
    return path
