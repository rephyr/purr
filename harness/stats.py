"""/stats: fun numbers about you and purr, from the saved chats (~/.local/state/purr/sessions/)."""

import datetime
import json
import re
from collections import Counter
from pathlib import Path

from . import ui
from .agent import LOG_DIR, STATE_DIR

NICE = re.compile(r"\b(please|pls|plz|thanks?|thank you|thx|ty)\b", re.I)
OOPS = re.compile(r"\b(oops|why|wtf|what the|ugh|argh|broken|doesn'?t work)\b", re.I)
PURR_NOTE = "(purr"  # purr's own notes to the model ride along as user messages: not yours


def _started(path, data):
    """When a chat started: the file name is its start time (2026-10-02_011128...)."""
    try:
        return datetime.datetime.strptime(path.stem[:17], "%Y-%m-%d_%H%M%S")
    except ValueError:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def _guess_tokens(messages):
    """Tokens a chat's models wrote, guessed from the saved text (thinking isn't always saved,
    so it's a low guess)."""
    chars = 0
    for m in messages:
        if m.get("role") != "assistant":
            continue
        chars += len(str(m.get("content") or ""))
        chars += len(str(m.get("reasoning_content") or m.get("reasoning") or ""))
        chars += sum(len(c["function"].get("arguments") or "") for c in m.get("tool_calls") or [])
    return chars // 4


def _lines(text):
    return len(text.splitlines()) if text else 0


def gather(log_dir=LOG_DIR, today=None):
    """Everything /stats shows, counted over every saved chat. Test runs in /tmp don't count."""
    s = {"chats": 0, "said": 0, "words": 0, "nice": 0, "oops": 0, "cost": 0.0, "out": 0, "guessed": False,
         "added": 0, "removed": 0, "tools": Counter(), "models": Counter(), "modes": Counter(),
         "projects": Counter(), "files": Counter(), "hours": Counter(), "days": set(),
         "longest": None, "first": None}
    for path in sorted(Path(log_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        folder = data.get("folder") or ""
        users = [m for m in data.get("messages", []) if m.get("role") == "user"]
        if not users or folder.startswith("/tmp/"):
            continue
        # a compacted chat starts with purr's summary instead of your messages: still a chat
        mine = [m for m in users if not str(m.get("content") or "").startswith(PURR_NOTE)]
        when = _started(path, data)
        s["chats"] += 1
        s["first"] = min(s["first"] or when, when)
        s["days"].add(when.date())
        s["hours"][when.hour] += 1
        s["models"][data.get("model") or "?"] += 1
        s["modes"][data.get("mode") or "code"] += 1
        s["projects"][Path(folder).name or "~"] += 1
        s["cost"] += data.get("cost") or 0.0
        if "out" in data:
            s["out"] += data["out"] or 0
        else:  # chats from before purr saved the count: about 4 characters a token
            s["out"] += _guess_tokens(data.get("messages", []))
            s["guessed"] = True
        for m in mine:
            text = str(m.get("content") or "").split("\n\n(purr:")[0]  # without purr's add-ons
            s["said"] += 1
            s["words"] += len(text.split())
            s["nice"] += len(NICE.findall(text))
            s["oops"] += len(OOPS.findall(text))
        if not s["longest"] or len(mine) > s["longest"][0]:
            s["longest"] = (len(mine), data.get("title") or "")
        for m in data.get("messages", []):
            for call in m.get("tool_calls") or []:
                name = call["function"]["name"]
                s["tools"][name] += 1
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except ValueError:
                    continue
                if not isinstance(args, dict):
                    continue
                if name in ("edit_file", "write_file") and args.get("path"):
                    s["files"][f"{Path(folder).name}/{args['path']}"] += 1
                if name == "edit_file":
                    s["added"] += _lines(args.get("new_text") or args.get("content") or "")
                    s["removed"] += _lines(args.get("old_text") or "")
                elif name == "write_file":
                    s["added"] += _lines(args.get("content") or "")
    s["streak"] = _streak(s["days"], today or datetime.date.today())
    return s


def _streak(days, today):
    """Days in a row with purr, ending today (or yesterday: today isn't over yet)."""
    day = today if today in days else today - datetime.timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= datetime.timedelta(days=1)
    return n


def _clock(hours):
    if not hours:
        return ""
    hour = hours.most_common(1)[0][0]
    kind = ("night owl 🦉" if hour < 5 or hour >= 22 else "early bird 🐤" if hour < 9
            else "daytime coder ☀" if hour < 18 else "evening coder 🌙")
    return f"{kind}: most chats start around {hour:02d}:00"


def load_cat():
    try:
        return json.loads((STATE_DIR / "cat.json").read_text())
    except (OSError, ValueError):
        return None


def report(s, cat=None):
    """The /stats lines: [(kind, text)]."""
    if not s["chats"]:
        return [("info", "no chats yet: say something and come back ♡")]
    t = s["tools"]
    out = [("info", f"₊˚✧ purr stats since {s['first']:%-d.%-m.%Y} ✧˚₊"),
           ("info", f"♡ {s['chats']} chats on {len(s['days'])} days"
                    + (f" · {s['streak']}-day streak 🔥" if s["streak"] > 1 else "")),
           ("info", f"✎ you sent {s['said']} messages ({s['words']:,} words)"
                    + (f", said please or thanks {s['nice']}×" if s["nice"] else "")),
           ("info", f"◆ the models read {t['read_file']} files, made {t['edit_file'] + t['write_file']} edits "
                    f"and ran {t['run']} commands"),
           ("info", f"＋ {s['added']:,} lines written, − {s['removed']:,} taken out")]
    out.append(("info", f"✧ {'≈' if s['guessed'] else ''}{ui.short(s['out'])} tokens generated"
                        + (f", ${s['cost']:.2f} spent" if s["cost"] >= 0.01 else ", all free")))
    model, n = s["models"].most_common(1)[0]
    out.append(("info", f"★ favourite model: {model} ({n} chats)"))
    if len(s["modes"]) > 1:
        out.append(("info", "◈ modes: " + ", ".join(f"{m} {n}" for m, n in s["modes"].most_common())))
    project, n = s["projects"].most_common(1)[0]
    out.append(("info", f"⌂ favourite project: {project} ({n} chats)"))
    if s["files"]:
        f, n = s["files"].most_common(1)[0]
        out.append(("info", f"✂ most edited file: {f} ({n} edits)"))
    if s["longest"] and s["longest"][0] > 1:
        out.append(("info", f"∞ longest chat: {s['longest'][0]} messages, \"{s['longest'][1][:50]}\""))
    out.append(("info", "☾ " + _clock(s["hours"])))
    if s["oops"]:
        out.append(("dim", f"  (you said \"why\", \"ugh\" or \"broken\" {s['oops']}× — it's okay, bugs happen)"))
    if cat:
        out.append(("info", f"₍^. .^₎ {cat.get('name', 'your cat')} got {cat.get('pets', 0)} pets "
                            f"and helped with {cat.get('tasks', 0)} tasks"))
    return out


def run():
    return report(gather(), load_cat())
