"""Checks purr runs on the model's work by itself, without asking the model.

- code_problems: after an edit, a syntax check and ruff's bug checks (undefined names,
  functions defined twice, ...) on the new code; only problems the edit added are reported
- placeholder: "# ... rest of the code unchanged" written into a file, which deletes real code
- test_command: how to run this project's tests, for the final check
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

# ruff rules that are almost always real bugs (not style): undefined names, redefinitions,
# names used before they're set, misplaced return/yield/break, comparisons that are always wrong
RUFF_RULES = "F821,F822,F823,F811,F402,F631,F632,F701,F702,F704,F706,F707,PLE"

# "...rest of the code unchanged" and friends: a comment standing in for code. Kept narrow on purpose:
# ordinary comments like "# add to an existing stack of the same item" must not match.
PLACEHOLDER = re.compile(
    r"^\s*(#|//|/\*|<!--)\s*(?:"
    r"(?:\.\.\.|…).{0,40}\b(rest|remaining|existing|unchanged|same|previous|omitted|other)\b"  # "... rest unchanged"
    r"|\b(rest|remainder) of (the )?(code|file|function|class|method|module|implementation)\b"
    r"|\b(existing|previous|remaining|other) (code|methods?|functions?|logic|implementation)"
    r"\s+(here|unchanged|remains?|stays?|goes here|as before|omitted)\b"
    r"|\b(code|implementation) (unchanged|omitted|as before)\b"
    r").*$", re.I | re.M)


def _ruff(path, text):
    """[(line, code, message)] from ruff for text (as if it were saved at path)."""
    ruff = shutil.which("ruff")
    if not ruff:
        return []
    try:
        r = subprocess.run([ruff, "check", "--no-cache", "--quiet", "--output-format", "json",
                            "--select", RUFF_RULES, "--stdin-filename", str(path), "-"],
                           input=text, capture_output=True, text=True, timeout=20)
        found = json.loads(r.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [((f.get("location") or {}).get("row", 0), f.get("code") or "syntax", f.get("message", ""))
            for f in found]


def code_problems(path, before, after, limit=5):
    """Problems the change introduced, as short lines for the model. [] when all is well."""
    if not str(path).endswith(".py"):
        return []
    try:
        compile(after, str(path), "exec")
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    except ValueError:
        return []
    old = {(code, msg) for _, code, msg in _ruff(path, before)} if before else set()
    new = [(line, code, msg) for line, code, msg in _ruff(path, after) if (code, msg) not in old]
    return [f"line {line}: {msg} ({code})" for line, code, msg in sorted(new)[:limit]]


TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[jt]sx?|tests?/[^/]*)$")
CHECK_LINE = re.compile(r"\bassert|\bexpect\(|\.toBe|\.toEqual|assert_eq!")


def weakened_tests(path, before, after, limit=3):
    """Checks (assert lines) an edit to a test file removed or changed: the model may be making
    a failing test pass by changing what it expects. New tests (only added lines) are fine."""
    if not before or not TEST_FILE.search(str(path)):
        return []
    import difflib
    gone = []
    for op in difflib.ndiff(before.splitlines(), after.splitlines()):
        if op.startswith("- ") and CHECK_LINE.search(op) and op[2:].strip():
            gone.append(op[2:].strip()[:120])
    return gone[:limit]


def placeholder(before, after):
    """The first line that looks like "...rest of the code unchanged" and wasn't there before."""
    for m in PLACEHOLDER.finditer(after):
        line = m.group(0).strip()
        if line not in (before or ""):
            return line
    return None


_PYTEST = []


def _has_pytest():
    """Whether python3 (what the tests run with) has pytest. Asked once."""
    if not _PYTEST:
        try:
            ok = subprocess.run(["python3", "-c", "import pytest"], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        _PYTEST.append(ok)
    return _PYTEST[0]


def test_command(root):
    """The command that runs this project's tests, or None if purr can't tell.
    <project>/.purr/test_command overrides the guess."""
    root = Path(root)
    custom = root / ".purr" / "test_command"
    if custom.is_file():
        return custom.read_text().strip() or None
    py_tests = (list(root.glob("test_*.py")) + list(root.glob("tests/**/test_*.py"))
                + list(root.glob("*_test.py")))
    if py_tests:
        python = "python3"
        if _has_pytest():
            # only tests/ when that's where the tests are: pytest at the root also collects test
            # files that aren't the project's (fixtures, examples, purr's own bench tasks)
            only = " tests" if (root / "tests").is_dir() and not list(root.glob("test_*.py")) \
                and not list(root.glob("*_test.py")) else ""
            return f"{python} -m pytest -q -x --tb=short{only}"
        return f"{python} -m unittest discover -s tests" if (root / "tests").is_dir() and not \
            list(root.glob("test_*.py")) else f"{python} -m unittest discover"
    package = root / "package.json"
    if package.is_file():
        try:
            info = json.loads(package.read_text())
        except ValueError:
            info = {}
        runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        if (info.get("scripts") or {}).get("test"):
            return f"{runner} test" + (" --silent" if runner == "npm" else "")
        deps = {**(info.get("devDependencies") or {}), **(info.get("dependencies") or {})}
        if "vitest" in deps:
            return "npx vitest run"
        if "jest" in deps:
            return "npx jest"
    # the other languages' own test commands (DeepSWE: a third of its projects are Go and Rust)
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test --quiet"
    makefile = next((root / n for n in ("Makefile", "makefile", "GNUmakefile") if (root / n).is_file()), None)
    if makefile and re.search(r"^test\s*:", makefile.read_text(errors="ignore"), re.M):
        return "make test"
    return None


# ---- limits: the numbers a request sets, and what the model measured (the margin check) ----

_NUM = r"([-+]?\d+(?:\.\d+)?)"
_AT_LEAST = r"(?:at least|no less than|not less than|minimum of|greater than or equal to|above|over|exceeds?|more than|>=|≥|>)"
_AT_MOST = (r"(?:at most|no more than|not more than|maximum of|less than or equal to|under|below|within|"
            r"less than|faster than|<=|≤|<)")
LIMIT = re.compile(rf"({_AT_LEAST}|{_AT_MOST})\s*{_NUM}\s*(%|x\b|×|times\b|ms\b|s\b|seconds?\b|minutes?\b|mb\b|gb\b|kb\b|bytes?\b)?",
                   re.I)
MEASURE = re.compile(r"^\s*MEASURE\s+(?P<name>[^|]+?)\s*\|\s*(?P<value>[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)[^|]*\|\s*"
                     r"(?P<op><=|>=|==|<|>|=)\s*(?P<target>[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)[^|]*\|\s*(?P<command>.+?)\s*$",
                     re.I | re.M)


def stated_limits(request):
    """[(the words, op, value)] for the numeric limits a request states ("accuracy at least 0.62",
    "no more than 1.05 times as slow"). Rough on purpose: only for saying which ones went unmeasured."""
    out = []
    for m in LIMIT.finditer(request):
        start = request.rfind("\n", 0, m.start()) + 1
        words = " ".join(request[max(start, m.start() - 60):m.end()].split())
        op = ">=" if re.fullmatch(_AT_LEAST, m.group(1), re.I) else "<="
        out.append((words, op, float(m.group(2))))
    return out[:12]


def measures(reply):
    """The MEASURE lines of a final reply: [{name, value, op, target, command}]."""
    return [{"name": m["name"].strip(), "value": float(m["value"]), "op": m["op"], "target": float(m["target"]),
             "command": m["command"].strip().strip("`")} for m in MEASURE.finditer(reply or "")]


def margin(value, op, target):
    """(meets it, relative room left): room is negative when it misses."""
    scale = abs(target) or 1.0
    if op in (">=", ">"):
        room = (value - target) / scale
        ok = value >= target if op == ">=" else value > target
    elif op in ("<=", "<"):
        room = (target - value) / scale
        ok = value <= target if op == "<=" else value < target
    else:
        room = -abs(value - target) / scale
        ok = abs(value - target) <= 1e-9 * scale
    return ok, room
