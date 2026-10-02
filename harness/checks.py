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
    py_tests = list(root.glob("test_*.py")) + list(root.glob("tests/test_*.py")) + list(root.glob("*_test.py"))
    if py_tests:
        python = "python3"
        if _has_pytest():
            return f"{python} -m pytest -q -x --tb=short"
        return f"{python} -m unittest discover -s tests" if (root / "tests").is_dir() and not \
            list(root.glob("test_*.py")) else f"{python} -m unittest discover"
    package = root / "package.json"
    if package.is_file():
        try:
            if (json.loads(package.read_text()).get("scripts") or {}).get("test"):
                return "npm test --silent"
        except ValueError:
            pass
    return None
