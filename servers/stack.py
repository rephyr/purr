"""MCP server "stack": real API docs for the versions this project actually uses, so a (small)
model stops guessing method names from memory.

    godot_class   a Godot class from the installed Godot (4.x): methods, properties, signals, docs
    python_api    a Python package's function, class or module, from the project's own environment

Start it in the project folder: python3 servers/stack.py   (only the standard library)
Godot docs come from `godot --doctool` (signatures, offline) plus that exact version's doc file
from GitHub (descriptions), cached in ~/.cache/purr/godot/<version>/.
"""

import difflib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcpserver import Server  # noqa: E402

ROOT = Path(os.environ.get("PROJECT_ROOT") or os.getcwd()).resolve()


def _has(*patterns):
    """Whether the project has files like these (near the top: deep searches would be slow)."""
    return any(next(ROOT.glob(p), None) for p in patterns)


def is_godot():
    return _has("project.godot", "*/project.godot")


def is_python():
    return _has("pyproject.toml", "requirements.txt", "setup.py", "*.py", "*/*.py")
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "purr" / "godot"
MAX_OUT = 6000

server = Server("stack", "Look up real APIs before using them: godot_class for Godot, python_api for "
                         "Python packages. They show this project's actual versions.")

# Godot 3 names models still reach for -> what Godot 4 calls them
GODOT3 = {
    "KinematicBody2D": "CharacterBody2D", "KinematicBody": "CharacterBody3D", "Spatial": "Node3D",
    "Sprite": "Sprite2D", "Position2D": "Marker2D", "Position3D": "Marker3D", "Particles2D": "GPUParticles2D",
    "Particles": "GPUParticles3D", "VisibilityNotifier2D": "VisibleOnScreenNotifier2D",
    "VisibilityNotifier": "VisibleOnScreenNotifier3D", "File": "FileAccess", "Directory": "DirAccess",
    "PoolStringArray": "PackedStringArray", "PoolIntArray": "PackedInt32Array", "PoolVector2Array": "PackedVector2Array",
    "PoolByteArray": "PackedByteArray", "PoolRealArray": "PackedFloat32Array", "RigidBody": "RigidBody3D",
    "StaticBody": "StaticBody3D", "Area": "Area3D", "Camera": "Camera3D", "MeshInstance": "MeshInstance3D",
    "CollisionShape": "CollisionShape3D", "RayCast": "RayCast3D", "YSort": "Node2D (y_sort_enabled = true)",
    "Navigation2D": "NavigationRegion2D", "NavigationPolygonInstance": "NavigationRegion2D",
    "Light2D": "PointLight2D", "TextureProgress": "TextureProgressBar", "ToolButton": "Button (flat = true)",
    "Tween": "Tween (made with create_tween(), not a node any more)", "GIProbe": "VoxelGI",
    "ARVRCamera": "XRCamera3D", "Reference": "RefCounted", "PopupDialog": "Popup", "WindowDialog": "Window",
}


# ---- Godot ----

def godot_bin():
    return os.environ.get("GODOT") or shutil.which("godot") or shutil.which("godot4")


def godot_version():
    exe = godot_bin()
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return exe, None
    m = re.match(r"(\d+\.\d+(?:\.\d+)?)\.(\w+)", out)
    return exe, (f"{m.group(1)}-{m.group(2)}" if m else None)


def local_docs(exe, tag):
    """Signatures for every class, dumped once by the installed Godot (offline, no descriptions)."""
    folder = CACHE / tag / "local"
    marker = folder / ".done"
    if not marker.exists():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run([exe, "--headless", "--doctool", str(folder)], cwd=folder,
                       capture_output=True, text=True, timeout=300)
        marker.write_text("ok")
    return {p.stem: p for p in folder.rglob("*.xml")}


def full_doc(name, tag):
    """The class's doc file from Godot's GitHub for exactly this version (with descriptions), cached."""
    path = CACHE / tag / "github" / f"{name}.xml"
    if path.exists():
        return path if path.stat().st_size else None
    if os.environ.get("PURR_OFFLINE"):  # purr's private mode: only what's already cached
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://raw.githubusercontent.com/godotengine/godot/{tag}/doc/classes/{name}.xml"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            path.write_bytes(r.read())
        return path
    except Exception:
        path.write_bytes(b"")  # not there (a module class) or offline: don't ask again
        return None


def bbcode(text):
    """Godot's doc markup -> plain text."""
    text = re.sub(r"\[(?:method|func) ([\w.]+)\]", r"\1()", text or "")
    text = re.sub(r"\[(?:member|signal|constant|enum|param|theme_item|annotation) ([\w.@]+)\]", r"\1", text)
    text = re.sub(r"\[codeblocks?\].*?\[/codeblocks?\]", "", text, flags=re.S)
    text = re.sub(r"\[/?(?:code|b|i|u|kbd|codeblock|gdscript|csharp|br|center|url[^\]]*)\]", "", text)
    text = re.sub(r"\[([A-Z]\w+)\]", r"\1", text)
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text))
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _sig(method):
    ret = method.find("return")
    params = []
    for p in method.findall("param"):
        s = f"{p.get('name')}: {p.get('type')}"
        if p.get("default") is not None:
            s += f" = {p.get('default')}"
        params.append(s)
    q = f"  ({method.get('qualifiers')})" if method.get("qualifiers") else ""
    return f"{method.get('name')}({', '.join(params)}) -> {ret.get('type') if ret is not None else 'void'}{q}"


def _load(name, tag, local):
    path = full_doc(name, tag) or local.get(name)
    return ET.parse(path).getroot() if path else None


@server.tool("A Godot class from the Godot installed here: what it inherits, its properties, methods with "
             "exact signatures, and signals. Give member to see one method/property/signal in detail "
             "(it also looks in the parent classes). Use it instead of guessing Godot APIs.",
             {"name": {"type": "string", "description": "the class, like CharacterBody2D or Tween"},
              "member": {"type": "string", "description": "optional: one method, property or signal"}},
             required=["name"], when=is_godot)
def godot_class(name, member=""):
    exe, tag = godot_version()
    if not exe:
        return "Godot isn't installed here (no godot on PATH; set GODOT=/path/to/godot)"
    if not tag:
        return "couldn't read Godot's version"
    local = local_docs(exe, tag)
    name = name.strip().removeprefix("@").split(".")[0]
    if name not in local:
        hint = ""
        if name in GODOT3:
            hint = f"{name} is Godot 3. In Godot 4 it's {GODOT3[name]}."
        close = difflib.get_close_matches(name, local, n=5, cutoff=0.6)
        return (hint + " " if hint else "") + f"no class {name} in Godot {tag}" + (
            f". Did you mean: {', '.join(close)}?" if close else "")
    root = _load(name, tag, local)
    chain, parent = [], root.get("inherits")
    while parent and len(chain) < 12:
        chain.append(parent)
        p = local.get(parent)
        parent = ET.parse(p).getroot().get("inherits") if p else None
    head = f"{name}  (Godot {tag})" + (f"  extends {' < '.join(chain)}" if chain else "")
    if member:
        return _member(root, name, member.strip().split("(")[0], chain, tag, local, head)
    out = [head]
    brief = bbcode(root.findtext("brief_description"))
    desc = bbcode(root.findtext("description"))
    if brief:
        out.append(brief)
    if desc and desc != brief:
        out.append(desc[:600] + ("…" if len(desc) > 600 else ""))
    props = root.findall("members/member")
    if props:
        out.append("properties:\n" + "\n".join(
            f"  {m.get('name')}: {m.get('type')}" + (f" = {m.get('default')}" if m.get("default") else "")
            for m in props))
    methods = root.findall("methods/method")
    if methods:
        out.append("methods:\n" + "\n".join(f"  {_sig(m)}" for m in methods))
    signals = root.findall("signals/signal")
    if signals:
        out.append("signals:\n" + "\n".join(
            f"  {s.get('name')}({', '.join(p.get('name') + ': ' + p.get('type') for p in s.findall('param'))})"
            for s in signals))
    enums = sorted({c.get("enum") for c in root.findall("constants/constant") if c.get("enum")})
    if enums:
        out.append("enums: " + ", ".join(enums))
    if chain:
        out.append(f"(inherited members: godot_class {chain[0]}, or give member= and it looks up the chain)")
    text = "\n".join(out)
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n[cut short: ask for one member]"


def _member(root, name, member, chain, tag, local, head):
    for cls_name in [name, *chain]:
        cls = root if cls_name == name else _load(cls_name, tag, local)
        if cls is None:
            continue
        for kind, path in (("method", "methods/method"), ("property", "members/member"),
                           ("signal", "signals/signal"), ("constant", "constants/constant")):
            for el in cls.findall(path):
                if el.get("name") != member:
                    continue
                if kind == "method":
                    sig = _sig(el)
                elif kind == "property":
                    sig = f"{member}: {el.get('type')}" + (f" = {el.get('default')}" if el.get("default") else "")
                    if el.get("setter") or el.get("getter"):
                        sig += f"   (set_/get_: {el.get('setter') or '-'} / {el.get('getter') or '-'})"
                elif kind == "signal":
                    sig = f"signal {member}({', '.join(p.get('name') + ': ' + p.get('type') for p in el.findall('param'))})"
                else:
                    sig = f"{member} = {el.get('value')}" + (f"  (enum {el.get('enum')})" if el.get("enum") else "")
                where = "" if cls_name == name else f"  (inherited from {cls_name})"
                text = bbcode(el.text if kind in ("property", "constant") else el.findtext("description"))
                return f"{head}\n{kind} {sig}{where}\n{text or '(no description in this Godot build)'}"
    names = [el.get("name") for el in root.iter() if el.tag in ("method", "member", "signal") and el.get("name")]
    close = difflib.get_close_matches(member, names, n=5, cutoff=0.5)
    return f"{name} has no {member} (looked in {', '.join([name, *chain])})" + (
        f". Close: {', '.join(close)}" if close else "")


# ---- Python ----

INSPECT = r'''
import importlib, inspect, sys, os
name, root = sys.argv[1], os.path.realpath(sys.argv[2])
parts = name.split(".")
obj = mod = None
for i in range(len(parts), 0, -1):
    try:
        mod = importlib.import_module(".".join(parts[:i]))
    except Exception:
        continue
    obj = mod
    try:
        for p in parts[i:]:
            obj = getattr(obj, p)
    except AttributeError as e:
        print(f"{'.'.join(parts[:i])} has no {'.'.join(parts[i:])}")
        close = [n for n in dir(mod) if not n.startswith("_") and parts[-1].lower()[:4] in n.lower()][:10]
        if close: print("close:", ", ".join(close))
        sys.exit(0)
    break
if mod is None:
    print(f"can't import {parts[0]} in this project's Python ({sys.executable})"); sys.exit(0)
src = getattr(mod, "__file__", "") or ""
if src and os.path.realpath(src).startswith(root + os.sep) and "site-packages" not in src:
    print(f"{name} is this project's own code ({os.path.relpath(src, root)}): use outline or find_symbol"); sys.exit(0)
try:
    from importlib.metadata import version
    ver = version(parts[0].replace("_", "-"))
except Exception:
    ver = getattr(sys.modules.get(parts[0]), "__version__", "?")
def sig(o):
    try: return str(inspect.signature(o))
    except (TypeError, ValueError): return "(...)"
def doc(o, n):
    d = inspect.getdoc(o) or ""
    lines = d.splitlines()
    return "\n".join(lines[:n]) + ("\n…" if len(lines) > n else "")
kind = "module" if inspect.ismodule(obj) else "class" if inspect.isclass(obj) else "function" if callable(obj) else type(obj).__name__
print(f"{name}  ({kind}, {parts[0]} {ver}, Python {sys.version.split()[0]})")
if kind == "function":
    print(f"{parts[-1]}{sig(obj)}"); print(doc(obj, 30))
elif kind == "class":
    print(f"class {parts[-1]}{sig(obj)}")
    bases = [b.__name__ for b in obj.__mro__[1:-1]]
    if bases: print("bases:", " < ".join(bases[:8]))
    print(doc(obj, 15))
    own = [n for n in obj.__dict__ if not n.startswith("_")]
    if own: print("members:")
    for n in own[:50]:
        m = getattr(obj, n, None)
        first = (inspect.getdoc(m) or "").split("\n")[0][:90] if callable(m) else ""
        print(f"  {n}{sig(m) if callable(m) else ''}" + (f"  # {first}" if first else ""))
    if len(own) > 50: print(f"  … {len(own) - 50} more")
elif kind == "module":
    print(doc(obj, 10))
    names = getattr(obj, "__all__", None) or [n for n in dir(obj) if not n.startswith("_")]
    print("contents:")
    for n in list(names)[:60]:
        m = getattr(obj, n, None)
        k = "class" if inspect.isclass(m) else "def" if callable(m) else "module" if inspect.ismodule(m) else ""
        print(f"  {k} {n}{sig(m) if k in ('def', 'class') else ''}".rstrip())
else:
    print(repr(obj)[:500])
'''


def project_python():
    for cand in (".venv/bin/python", "venv/bin/python", "env/bin/python"):
        if (ROOT / cand).exists():
            return str(ROOT / cand)
    return shutil.which("python3") or sys.executable


@server.tool("A Python library's function, class or module as installed in this project's environment "
             "(its .venv if there is one): exact signature, docs and members, plus the package version. "
             "Use it instead of guessing a library's API.",
             {"name": {"type": "string", "description": "dotted name, like textual.widgets.TextArea or requests.get"}},
             required=["name"], when=is_python)
def python_api(name):
    name = name.strip().removesuffix("()")
    if not re.fullmatch(r"[\w.]+", name):
        return "give a dotted name like package.module.function"
    py = project_python()
    try:
        res = subprocess.run([py, "-c", INSPECT, name, str(ROOT)], cwd=ROOT, capture_output=True,
                             text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return f"looking up {name} took too long (importing it may start something)"
    out = (res.stdout or res.stderr).strip()
    return (out[:MAX_OUT] + "\n[cut short]") if len(out) > MAX_OUT else out or f"nothing found for {name}"


if __name__ == "__main__":
    server.run()
