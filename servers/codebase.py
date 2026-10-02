"""MCP server "codebase": helps a (small) model understand a project without reading every file.

    project_overview   tech stack, how to run and test it, folder layout, entry points
                       (purr puts this in the system prompt instead of offering the tool)
    outline            a file: its symbols with line ranges, and who uses the file;
                       a folder: the code map (every file's classes and functions)
    find_symbol        where a function, class or variable is defined, and where it's used

Few tools with short descriptions on purpose: every tool is sent with every request.

Start it in the project folder: python3 servers/codebase.py   (only the standard library)
"""

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcpserver import Server  # noqa: E402

ROOT = Path(os.environ.get("PROJECT_ROOT") or os.getcwd()).resolve()

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".purr", ".godot", ".import", "dist", "build", "target", ".next", ".cache",
             ".idea", ".vscode", "coverage", ".tox", "out", "bin", "obj"}
MAX_FILES = 4000
MAX_SIZE = 400_000  # bigger files are skipped when mapping

LANG = {".py": "python", ".gd": "gdscript", ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
        ".ts": "js", ".tsx": "js", ".rs": "rust", ".go": "go", ".java": "java", ".cs": "csharp",
        ".kt": "java", ".c": "c", ".h": "c", ".cpp": "c", ".hpp": "c", ".cc": "c",
        ".gdshader": "shader", ".lua": "lua", ".rb": "ruby", ".php": "php", ".swift": "java"}
LANG_NAME = {"python": "Python", "gdscript": "GDScript", "js": "JavaScript/TypeScript", "rust": "Rust",
             "go": "Go", "java": "Java/Kotlin", "csharp": "C#", "c": "C/C++", "shader": "Godot shaders",
             "lua": "Lua", "ruby": "Ruby", "php": "PHP"}

server = Server("codebase", "Understand the project: outline before reading whole files, find_symbol "
                            "before changing what others use.")


# ---- files ----

def project_files():
    """Relative paths of the project's files (git's list when there is one: it skips ignored files)."""
    try:
        res = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            files = [f for f in res.stdout.splitlines() if not set(Path(f).parts) & SKIP_DIRS]
            return sorted(f for f in files if (ROOT / f).is_file())[:MAX_FILES]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(folder, n), ROOT))
            if len(out) >= MAX_FILES:
                return out
    return out


def read(rel):
    p = ROOT / rel
    try:
        if p.stat().st_size > MAX_SIZE:
            return None
        return p.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def source_files(under="."):
    base = Path(under).as_posix().strip("./")
    return [f for f in project_files() if Path(f).suffix.lower() in LANG
            and (not base or f == base or f.startswith(base + "/"))]


# ---- symbols ----

class Sym:
    __slots__ = ("kind", "name", "sig", "line", "end", "depth")

    def __init__(self, kind, name, sig, line, end=None, depth=0):
        self.kind, self.name, self.sig, self.line, self.end, self.depth = kind, name, sig, line, end, depth


def _py_args(node):
    try:
        return ast.unparse(node.args)
    except Exception:
        return "..."


def python_symbols(text):
    tree = ast.parse(text)
    out = []

    def walk(body, depth):
        for node in body:
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                out.append(Sym("class", node.name, f"class {node.name}" + (f"({bases})" if bases else ""),
                               node.lineno, node.end_lineno, depth))
                walk(node.body, depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                pre = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                out.append(Sym("function", node.name, f"{pre}{node.name}({_py_args(node)}){ret}",
                               node.lineno, node.end_lineno, depth))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and depth == 0:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        out.append(Sym("constant", t.id, t.id, node.lineno, node.end_lineno, depth))
    walk(tree.body, 0)
    return out


# (regex, kind, name group, how to show it) per language; depth comes from indentation
PATTERNS = {
    "gdscript": [
        (r"^class_name\s+(\w+)", "class", 1, "class_name {1}"),
        (r"^extends\s+(\S+)", "extends", 1, "extends {1}"),
        (r"^(\s*)class\s+(\w+)", "class", 2, "class {2}"),
        (r"^(\s*)signal\s+(\w+)(\(.*\))?", "signal", 2, "signal {2}{3}"),
        (r"^(\s*)(?:static\s+)?func\s+(\w+)\s*(\(.*?\))\s*(->\s*[\w\[\], ]+)?", "function", 2, "func {2}{3} {4}"),
        (r"^(\s*)enum\s+(\w+)", "enum", 2, "enum {2}"),
        (r"^(\s*)const\s+(\w+)", "constant", 2, "const {2}"),
        (r"^(\s*)@export\S*\s+var\s+(\w+)(\s*:\s*[\w\[\]]+)?", "export", 2, "@export var {2}{3}"),
        (r"^(\s*)@onready\s+var\s+(\w+)", "var", 2, "@onready var {2}"),
    ],
    "js": [
        (r"^(\s*)(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)([^{]*)", "class", 2, "class {2}{3}"),
        (r"^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)\s*(<[^>]*>)?\s*(\([^)]*\))", "function", 2, "function {2}{4}"),
        (r"^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(\([^)]*\)|\w+)\s*(?::[^=]+)?=>", "function", 2, "{2} = {3} =>"),
        (r"^(\s*)(?:export\s+)?(?:interface|type)\s+(\w+)", "type", 2, "type {2}"),
        (r"^(\s+)(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*(?!if\b|for\b|while\b|switch\b|catch\b|return\b|function\b)(\w+)\s*(\([^)]*\))\s*(?::\s*[^{]+)?\{", "method", 2, "{2}{3}"),
    ],
    "rust": [
        (r"^(\s*)(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*(<[^>]*>)?\s*(\([^)]*\))\s*(->\s*[^{;]+)?", "function", 2, "fn {2}{4} {5}"),
        (r"^(\s*)(?:pub(?:\([\w:]+\))?\s+)?(struct|enum|trait)\s+(\w+)", "class", 3, "{2} {3}"),
        (r"^(\s*)impl(?:<[^>]*>)?\s+([\w:<>, ]+?)(?:\s+for\s+([\w:<>]+))?\s*\{", "impl", 2, "impl {2} {3}"),
    ],
    "go": [
        (r"^func\s+(\([^)]*\)\s*)?(\w+)\s*(\([^)]*\))\s*([^{]*)", "function", 2, "func {1}{2}{3} {4}"),
        (r"^type\s+(\w+)\s+(struct|interface)", "class", 1, "type {1} {2}"),
    ],
    "java": [
        (r"^(\s*)(?:public|private|protected|internal|abstract|final|static|open|data|sealed|\s)*(class|interface|enum|object|record)\s+(\w+)", "class", 3, "{2} {3}"),
        (r"^(\s+)(?:public|private|protected|static|final|abstract|override|suspend|synchronized|\s)*(?:fun\s+)?[\w<>\[\], ?]*\s+(\w+)\s*(\([^)]*\))\s*(?:[:\w<>?, ]*)\{", "method", 2, "{2}{3}"),
    ],
    "csharp": [
        (r"^(\s*)(?:public|private|protected|internal|abstract|sealed|static|partial|\s)*(class|interface|struct|enum|record)\s+(\w+)", "class", 3, "{2} {3}"),
        (r"^(\s+)(?:public|private|protected|internal|static|virtual|override|async|abstract|\s)+[\w<>\[\], ?]+\s+(\w+)\s*(\([^)]*\))", "method", 2, "{2}{3}"),
    ],
    "c": [
        (r"^(?:static\s+|inline\s+|extern\s+)*[\w:<>\*&]+[\s\*&]+(\w+(?:::\w+)?)\s*(\([^;{]*\))\s*(?:const\s*)?\{?$", "function", 1, "{1}{2}"),
        (r"^(\s*)(class|struct|enum)\s+(\w+)\s*[:{]", "class", 3, "{2} {3}"),
    ],
    "shader": [
        (r"^shader_type\s+(\w+)", "type", 1, "shader_type {1}"),
        (r"^uniform\s+(\w+)\s+(\w+)", "uniform", 2, "uniform {1} {2}"),
        (r"^(\w+)\s+(\w+)\s*(\([^)]*\))\s*\{", "function", 2, "{1} {2}{3}"),
    ],
    "lua": [(r"^(\s*)(?:local\s+)?function\s+([\w.:]+)\s*(\([^)]*\))", "function", 2, "function {2}{3}")],
    "ruby": [(r"^(\s*)(class|module)\s+([\w:]+)", "class", 3, "{2} {3}"),
             (r"^(\s*)def\s+([\w.?!]+)(\(.*\))?", "function", 2, "def {2}{3}")],
    "php": [(r"^(\s*)(?:abstract\s+|final\s+)?class\s+(\w+)", "class", 2, "class {2}"),
            (r"^(\s*)(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)\s*(\([^)]*\))", "function", 2, "function {2}{3}")],
}
COMPILED = {lang: [(re.compile(rx), kind, g, show) for rx, kind, g, show in pats] for lang, pats in PATTERNS.items()}
NOT_NAMES = {"if", "for", "while", "switch", "catch", "return", "else", "new", "do", "try", "with", "using", "lock"}


def regex_symbols(text, lang):
    out = []
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        for rx, kind, g, show in COMPILED[lang]:
            m = rx.match(line)
            if not m:
                continue
            name = m.group(g)
            if not name or name in NOT_NAMES:
                continue
            groups = [m.group(0)] + [(x or "").strip() for x in m.groups()]
            sig = re.sub(r"\{(\d)\}", lambda k: groups[int(k.group(1))] if int(k.group(1)) < len(groups) else "", show)
            indent = len(line) - len(line.lstrip())
            out.append(Sym(kind, name, " ".join(sig.split()), n, None, 1 if indent else 0))
            break
    # a symbol ends where the next one at the same or a lower depth starts
    for i, s in enumerate(out):
        nxt = next((o.line - 1 for o in out[i + 1:] if o.depth <= s.depth), len(lines))
        s.end = max(s.line, nxt)
    return out


def symbols(rel):
    text = read(rel)
    lang = LANG.get(Path(rel).suffix.lower())
    if text is None or not lang:
        return []
    if lang == "python":
        try:
            return python_symbols(text)
        except SyntaxError:
            return []
    return regex_symbols(text, lang)


# ---- the tools ----

@server.tool("The project's stack, how to run/test it, folders and entry points.")
def project_overview():
    return overview()


def overview(limit=3000):
    files = project_files()
    langs = Counter(LANG[Path(f).suffix.lower()] for f in files if Path(f).suffix.lower() in LANG)
    out = [f"Project: {ROOT.name}  ({len(files)} files)"]
    if langs:
        out.append("Languages: " + ", ".join(f"{LANG_NAME[k]} ({n} files)" for k, n in langs.most_common(5)))
    stack, run = [], []
    names = set(files)

    def has(name):
        return name in names or (ROOT / name).is_file()

    if has("pyproject.toml"):
        try:
            data = tomllib.loads((ROOT / "pyproject.toml").read_text())
            proj = data.get("project", {})
            deps = proj.get("dependencies", []) + [d for g in data.get("dependency-groups", {}).values() for d in g if isinstance(d, str)]
            stack.append(f"Python {proj.get('requires-python', '')} project {proj.get('name', '')}".replace("  ", " ").strip()
                         + (f", depends on: {', '.join(deps[:15])}" if deps else ""))
            if "uv" in data.get("tool", {}) or has("uv.lock"):
                stack.append("uv manages the environment (uv sync, uv run ...)")
        except (tomllib.TOMLDecodeError, OSError):
            stack.append("Python (pyproject.toml)")
    if has("requirements.txt"):
        reqs = [r.strip() for r in (ROOT / "requirements.txt").read_text(errors="replace").splitlines()
                if r.strip() and not r.startswith("#")]
        stack.append("Python requirements: " + ", ".join(reqs[:15]))
    if has("package.json"):
        try:
            pkg = json.loads((ROOT / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            known = [f"{d} {v}" for d, v in deps.items() if d in (
                "react", "vue", "svelte", "next", "nuxt", "vite", "express", "typescript", "electron", "tailwindcss",
                "jest", "vitest", "mocha", "playwright", "astro", "solid-js", "three", "phaser", "pixi.js",
                "@sveltejs/kit", "fastify", "prisma", "eslint")]
            stack.append(f"Node project {pkg.get('name', '')}" + (f": {', '.join(known)}" if known else "")
                         + (f" (+{len(deps) - len(known)} more packages)" if len(deps) > len(known) else ""))
            for name, cmd in list(pkg.get("scripts", {}).items())[:8]:
                run.append(f"npm run {name}  ({cmd})")
        except (ValueError, OSError):
            stack.append("Node (package.json)")
    if has("tsconfig.json"):
        stack.append("TypeScript (tsconfig.json)")
    if has("Cargo.toml"):
        try:
            data = tomllib.loads((ROOT / "Cargo.toml").read_text())
            stack.append(f"Rust crate {data.get('package', {}).get('name', '')}, deps: "
                         + ", ".join(list(data.get("dependencies", {}))[:15]))
        except (tomllib.TOMLDecodeError, OSError):
            stack.append("Rust (Cargo.toml)")
        run += ["cargo run", "cargo test"]
    if has("go.mod"):
        mod = (ROOT / "go.mod").read_text(errors="replace").splitlines()
        stack.append("Go module " + " ".join(mod[0].split()[1:2]) + "; " + next((m for m in mod if m.startswith("go ")), ""))
        run += ["go run .", "go test ./..."]
    if has("project.godot"):
        stack.append(godot_summary())
        run.append("godot --path .   (runs the main scene)")
        for f in files:
            if f.endswith(".gd") and Path(f).stem in ("run_tests", "test_runner", "run_all_tests", "tests"):
                run.append(f"godot --headless --path . --script {f}   (the test runner)")
        if has("addons/gut/gut_cmdln.gd"):
            run.append("godot --headless --path . -s addons/gut/gut_cmdln.gd   (GUT tests)")
    if any(f.endswith(".csproj") for f in files):
        stack.append("C#/.NET (" + ", ".join(f for f in files if f.endswith(".csproj"))[:200] + ")")
    if has("Dockerfile") or has("docker-compose.yml") or has("compose.yaml"):
        stack.append("Docker" + (" compose" if has("docker-compose.yml") or has("compose.yaml") else ""))
    if any(f.startswith(".github/workflows/") for f in files):
        stack.append("GitHub Actions CI (.github/workflows)")
    for f in files:  # CI knows the real commands: take the ones that test, build or lint
        if f.startswith(".github/workflows/") or f in (".gitlab-ci.yml", ".woodpecker.yml"):
            for cmd in re.findall(r"^\s*-?\s*run:\s*(.+)$", read(f) or "", re.M):
                cmd = cmd.strip().strip("|").strip()
                if cmd and re.search(r"test|pytest|lint|check|build|vitest|jest", cmd, re.I) and len(cmd) < 160:
                    run.append(f"{cmd.removeprefix('./')}   (from CI)")
    if has("Makefile"):
        targets = re.findall(r"^([\w-]+):", (ROOT / "Makefile").read_text(errors="replace"), re.M)
        run.append("make " + " | ".join(targets[:8]))
    if langs.get("python"):
        if any(Path(f).name.startswith("test_") or "/tests/" in f"/{f}" for f in files):
            pytest = "pytest" in json.dumps(stack) or has("pytest.ini") or has("conftest.py")
            run.append("python3 -m pytest" if pytest else "python3 -m unittest discover -s tests (or pytest)")
        for entry in ("main.py", "app.py", "manage.py", "__main__.py", "run.py", "cli.py"):
            if has(entry):
                run.append(f"python3 {entry}")
    if stack:
        out.append("Stack:\n" + "\n".join(f"- {s}" for s in stack))
    if run:
        seen, unique = set(), []
        for r in run:  # the same command found twice (a runner and CI): keep the first
            cmd = r.split("   (")[0].strip()
            if cmd not in seen:
                seen.add(cmd)
                unique.append(r)
        out.append("Run / test:\n" + "\n".join(f"- {r}" for r in unique))
    # the layout: top-level folders with what's in them
    folders = defaultdict(Counter)
    top_files = []
    for f in files:
        parts = Path(f).parts
        if len(parts) == 1:
            top_files.append(f)
        else:
            if Path(f).suffix.lower() not in (".uid", ".import"):  # Godot's bookkeeping files
                folders[parts[0]][LANG_NAME.get(LANG.get(Path(f).suffix.lower()), Path(f).suffix or "other")] += 1
    if folders:
        lines = []
        for d, kinds in sorted(folders.items(), key=lambda kv: -sum(kv[1].values()))[:14]:
            what = ", ".join(f"{n} {k}" for k, n in kinds.most_common(3))
            lines.append(f"- {d}/  ({what})")
        out.append("Folders:\n" + "\n".join(lines))
    if top_files:
        out.append("Top-level files: " + ", ".join(top_files[:25]))
    notes = [n for n in ("AGENTS.md", "README.md", "CLAUDE.md", "CONTRIBUTING.md") if has(n)]
    if notes:
        out.append("Read for more: " + ", ".join(notes))
    text = "\n\n".join(out)
    return text if len(text) <= limit else text[:limit] + "\n[overview cut short]"


def godot_autoloads():
    """{name: script path} for a Godot project's autoloads (global singletons), {} otherwise."""
    try:
        text = (ROOT / "project.godot").read_text(errors="replace")
    except OSError:
        return {}
    if "[autoload]" not in text:
        return {}
    section = text.split("[autoload]")[1].split("\n[")[0]
    return {n: p for n, p in re.findall(r'^(\w+)="\*?res://([^"]+)"', section, re.M)}


def godot_summary():
    text = (ROOT / "project.godot").read_text(errors="replace")

    def setting(key):
        m = re.search(rf"^{re.escape(key)}=(.+)$", text, re.M)
        return m.group(1).strip().strip('"') if m else ""
    feats = setting("config/features")
    version = re.search(r'"(\d+\.\d+)"', feats)
    autoloads = re.findall(r"^(\w+)=\"\*?(res://[^\"]+)\"", text.split("[autoload]")[1].split("\n[")[0], re.M) \
        if "[autoload]" in text else []
    parts = [f"Godot {version.group(1) if version else '4'} project \"{setting('config/name')}\""]
    if setting("run/main_scene"):
        parts.append(f"main scene {setting('run/main_scene')}")
    if autoloads:
        parts.append("autoloads (global singletons): " + ", ".join(f"{n} ({p})" for n, p in autoloads[:12]))
    if any(ROOT.glob("*.csproj")):
        parts.append("uses C#")
    parts.append("scripts are GDScript 4: use @export, @onready, signal.emit(), await, not Godot 3 syntax")
    return "; ".join(parts)


def code_map(path=".", max_chars=6000):
    files = source_files(path)
    if not files:
        return f"no source files under {path}"
    maps = []
    for f in files:
        syms = symbols(f)
        maps.append((f, syms))
    for level in ("full", "top", "names"):  # shrink until it fits
        out = []
        for f, syms in maps:
            out.append(f)
            if level == "names":
                continue
            for s in syms:
                if s.kind in ("var",) or (level == "top" and s.depth > 0):
                    continue
                out.append(f"{'  ' * (s.depth + 1)}{s.sig}  :{s.line}")
        text = "\n".join(out)
        if len(text) <= max_chars:
            note = {"full": "", "top": "\n(top-level only: map a folder or file to see methods)",
                    "names": "\n(file names only: outline a folder or file to see what's inside)"}[level]
            return text + note
    return text[:max_chars] + f"\n[cut short: {len(files)} files, map a smaller folder]"


@server.tool("A file's classes and functions with line ranges (then read only those lines), and who uses "
             "it. A folder: every file's functions.",
             {"path": {"type": "string", "description": "file or folder (. = whole project)"}}, required=["path"])
def outline(path="."):
    rel = os.path.relpath((ROOT / path).resolve(), ROOT)
    if (ROOT / rel).is_dir():
        return code_map(rel)
    text = read(rel)
    if text is None:
        return f"can't read {path} (missing, not text, or too big)"
    syms = symbols(rel)
    total = len(text.splitlines())
    if not syms:
        return f"{rel}: {total} lines, no classes or functions found"
    lines = [f"{rel}  ({total} lines)"]
    for s in syms:
        lines.append(f"{'  ' * (s.depth + 1)}{s.sig}  lines {s.line}-{s.end}")
    lines.append(_used_by(rel))
    return "\n".join(lines)


def _definitions(name):
    found = []
    for f in source_files():
        for s in symbols(f):
            if s.name == name or s.name.endswith("." + name) or s.name.endswith("::" + name):
                found.append((f, s))
    return found


def _usages(name, skip):
    try:
        res = subprocess.run(["rg", "-n", "-w", "--no-heading", "--max-count", "20", "--max-columns", "200",
                              "--glob", "!*.lock", name, "."], cwd=ROOT, capture_output=True, text=True, timeout=20)
        lines = res.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        lines = []
        rx = re.compile(rf"\b{re.escape(name)}\b")
        for f in project_files():
            text = read(f) or ""
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    lines.append(f"{f}:{n}:{line}")
    out = []
    for line in lines:
        f, _, rest = line.removeprefix("./").partition(":")
        num, _, code = rest.partition(":")
        if (f, num) not in skip:
            out.append(f"{f}:{num}: {code.strip()[:150]}")
    return out


@server.tool("Where a function, class or variable is defined and every place it's used.",
             {"name": {"type": "string", "description": "exact name, like add_item"}}, required=["name"])
def find_symbol(name):
    name = name.strip().split("(")[0].split(".")[-1]
    defs = _definitions(name)
    out = []
    autoload = godot_autoloads().get(name)
    if autoload:
        out.append(f"{name} is a Godot autoload (a global singleton any script can use): {autoload}")
    if defs:
        out.append("defined:")
        out += [f"  {f}:{s.line}  {s.sig}" for f, s in defs[:10]]
    elif not autoload:
        out.append(f"no definition of {name} found (it may come from a library or the engine)")
    uses = _usages(name, {(f, str(s.line)) for f, s in defs})
    if uses:
        out.append(f"used in {len(uses)} place{'s' * (len(uses) != 1)}:")
        out += [f"  {u}" for u in uses[:40]]
        if len(uses) > 40:
            out.append(f"  … {len(uses) - 40} more")
    else:
        out.append("not used anywhere else")
    return "\n".join(out)


# ---- related files ----

def _python_imports(rel, text, files):
    found = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    pkg = Path(rel).parent
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg
                for _ in range(node.level - 1):
                    up = up.parent
                base = ".".join([*up.parts, *([base] if base else [])])
            mods = [base] + [f"{base}.{a.name}" if base else a.name for a in node.names]
        for m in mods:
            for cand in (m.replace(".", "/") + ".py", m.replace(".", "/") + "/__init__.py"):
                if cand in files:
                    found.add(cand)
    return found


def _js_imports(rel, text, files):
    found = set()
    for spec in re.findall(r"""(?:from\s+|import\s*\(\s*|require\s*\(\s*|import\s+)['"](\.[^'"]+)['"]""", text):
        base = os.path.normpath(os.path.join(os.path.dirname(rel), spec))
        for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", "/index.ts", "/index.js", "/index.tsx"):
            if base + ext in files:
                found.add(base + ext)
                break
    return found


def _res_paths(text, files):
    """res:// paths in Godot files (scripts, scenes, resources) that exist in the project."""
    return {p for p in re.findall(r'res://([^"\')\s]+)', text) if p in files}


def _imports(rel, files):
    text = read(rel) or ""
    ext = Path(rel).suffix.lower()
    if ext == ".py":
        return _python_imports(rel, text, files)
    if LANG.get(ext) == "js":
        return _js_imports(rel, text, files)
    if ext in (".gd", ".tscn", ".tres", ".godot", ".gdshader"):
        found = _res_paths(text, files)
        if ext == ".gd":  # class_name types used here, defined in other scripts
            names = {s.name: f for f in files if f.endswith(".gd") for s in symbols(f) if s.sig.startswith("class_name")}
            for word in set(re.findall(r"\b[A-Z]\w+\b", text)):
                if word in names and names[word] != rel:
                    found.add(names[word])
        return found
    return set()


def _used_by(rel):
    """One line for outline: the autoload name if it is one, and the files that import or load it."""
    files = set(project_files())
    autoload = next((n for n, script in godot_autoloads().items() if script == rel), None)
    if autoload:
        return f"autoload {autoload}: any script can use it"
    candidates = [f for f in files if Path(f).suffix.lower() in (".py", ".gd", ".tscn", ".tres", ".godot")
                  or LANG.get(Path(f).suffix.lower()) == "js"]
    users = sorted(f for f in candidates if f != rel and rel in _imports(f, files))
    if not users:
        return "used by: nothing (an entry point, or unused)"
    return "used by: " + ", ".join(users[:8]) + (f" +{len(users) - 8} more" if len(users) > 8 else "")


if __name__ == "__main__":
    server.run()
