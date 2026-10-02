"""checks.test_command: purr runs a project's own tests at the final check, in every language a
benchmark brings (DeepSWE: TypeScript, Go, Python, Rust, JavaScript).

Run: python3 -m unittest discover tests
Nothing is run: these only check which command purr would choose.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import checks  # noqa: E402


def project(files):
    root = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)
    return root


class TestCommandTest(unittest.TestCase):
    def test_go_rust_and_make(self):
        self.assertEqual(checks.test_command(project({"go.mod": "module x\n"})), "go test ./...")
        self.assertEqual(checks.test_command(project({"Cargo.toml": "[package]\n"})), "cargo test --quiet")
        self.assertEqual(checks.test_command(project({"Makefile": "build:\n\tcc x.c\ntest: build\n\t./t\n"})), "make test")
        self.assertIsNone(checks.test_command(project({"Makefile": "build:\n\tcc x.c\n"})))

    def test_javascript_runners(self):
        script = json.dumps({"scripts": {"test": "vitest run"}})
        self.assertEqual(checks.test_command(project({"package.json": script})), "npm test --silent")
        self.assertEqual(checks.test_command(project({"package.json": script, "pnpm-lock.yaml": ""})), "pnpm test")
        self.assertEqual(checks.test_command(project({"package.json": script, "yarn.lock": ""})), "yarn test")
        no_script = json.dumps({"devDependencies": {"vitest": "^3"}})
        self.assertEqual(checks.test_command(project({"package.json": no_script})), "npx vitest run")
        self.assertEqual(checks.test_command(project({"package.json": json.dumps({"devDependencies": {"jest": "1"}})})),
                         "npx jest")

    def test_python_tests_in_subfolders_count(self):
        root = project({"tests/unit/test_x.py": "def test_x(): pass\n", "pkg/__init__.py": ""})
        with mock.patch.object(checks, "_has_pytest", return_value=True):
            self.assertIn("pytest", checks.test_command(root))

    def test_a_project_setting_still_wins(self):
        root = project({"go.mod": "module x\n", ".purr/test_command": "go test ./pkg/...\n"})
        self.assertEqual(checks.test_command(root), "go test ./pkg/...")


if __name__ == "__main__":
    unittest.main()
