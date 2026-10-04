"""Beyond coding (from Terminal-Bench): terminal sessions for interactive programs, servers that
keep running, and images for models that can see.
No model is called; the sessions and commands are real.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["PURR_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import agent as agent_module  # noqa: E402
from harness.terminal import Terminals  # noqa: E402
from harness.tools import run_shell, schemas  # noqa: E402
from tests.test_limits import CONFIG, FakeView, agent, reply, scripted  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


class TerminalTest(unittest.TestCase):
    def test_talking_to_an_interactive_program(self):
        t = Terminals(tempfile.mkdtemp())
        t.tmux = False  # the pty sessions (tmux isn't always there)
        out = t.start("py", "python3 -q -i", wait=10, wait_for=">>>")
        self.assertIn(">>>", out)
        out = t.send("py", "6 * 7", wait=10, wait_for="42")
        self.assertIn("42", out)
        self.assertIn("running", t.list())
        t.send("py", keys=["C-d"], wait=3)
        time.sleep(0.5)
        self.assertIn("finished", t.list())
        self.assertIn("stopped", t.stop("py"))
        self.assertIn("no terminal session called 'py'", str(self._error(lambda: t.read("py"))))

    def _error(self, fn):
        try:
            fn()
        except ValueError as e:
            return e
        return None

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_tmux_sessions_outlive_purr(self):
        t = Terminals(tempfile.mkdtemp())
        t.start("svc", "bash", wait=1)
        t.send("svc", "echo hello-from-tmux", wait=5, wait_for="hello-from-tmux")
        del t  # purr is gone; the session isn't
        from harness.terminal import TmuxSession
        s = TmuxSession.__new__(TmuxSession)
        s.target = "purr-svc"
        self.assertTrue(s.alive())
        s.stop()

    def test_the_tool_asks_first_and_offers_itself(self):
        a = agent()
        self.assertIn("terminal", [s["function"]["name"] for s in schemas()])
        a.tools.view.ask = lambda q, **k: ("n", "not now")
        self.assertIn("not now", a.tools.call("terminal", json.dumps({"action": "start", "command": "bash"})))


class BackgroundTest(unittest.TestCase):
    def test_a_background_server_doesnt_stall_run_and_keeps_going(self):
        start = time.monotonic()
        out, code = run_shell("sleep 30 & echo started $!", tempfile.mkdtemp(), timeout=20)
        self.assertLess(time.monotonic() - start, 5)  # it used to wait for the pipe until the timeout
        self.assertEqual(code, 0)
        pid = int(out.split()[-1])
        os.kill(pid, 0)  # still running after the command returned
        os.kill(pid, 9)

    def test_a_timeout_stops_the_whole_command(self):
        out, code = run_shell("sleep 30", tempfile.mkdtemp(), timeout=1)
        self.assertEqual(code, -1)
        self.assertIn("timed out after 1s", out)

    def test_the_final_check_asks_about_services_once_something_was_started(self):
        for start in ({"command": "sleep 3 &"}, None):
            a = agent_module.Agent(CONFIG, tempfile.mkdtemp(), "small", FakeView())
            a.tools.trust_all = True
            a.set_one_shot()
            first = reply("", tool=("run", start)) if start else reply("", tool=("write_file", {"path": "a.py", "content": "x = 1\n"}))
            scripted(a, [first, reply("done"), reply("really done")])
            a.turn("serve the folder")
            check = [m["content"] for m in a.messages if "before you finish" in str(m.get("content"))]
            self.assertEqual(len(check), 1)
            self.assertEqual("keep running after you finish" in check[0], bool(start))


class ImageTest(unittest.TestCase):
    def seeing_agent(self):
        config = {**CONFIG, "models": {**CONFIG["models"], "eyes": {"provider": "ollama", "id": "eyes",
                                                                    "context": 32768, "vision": True}}}
        a = agent_module.Agent(config, tempfile.mkdtemp(), "eyes", FakeView())
        a.tools.trust_all = True
        (Path(a.root) / "board.png").write_bytes(PNG)
        return a

    def test_only_models_that_can_see_get_the_tool(self):
        self.assertIn("look_at_image", agent().hidden_tools)
        self.assertNotIn("look_at_image", self.seeing_agent().hidden_tools)

    def test_the_image_is_shown_right_after(self):
        a = self.seeing_agent()
        scripted(a, [reply("", tool=("look_at_image", {"path": "board.png"})), reply("e2e4")])
        a.turn("what's the best move?")
        sent = a._body(None, True)["messages"]
        parts = next(m["content"] for m in sent if isinstance(m["content"], list))
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_only_the_latest_images_go_again(self):
        a = self.seeing_agent()
        for i in range(5):
            a.messages.append({"role": "user", "content": f"(purr: image {i})", "images": ["data:image/png;base64,AA"]})
        sent = a._body(None, True)["messages"]
        self.assertEqual(sum(1 for m in sent if isinstance(m["content"], list)), 3)
        self.assertLess(a.context_used(), 10_000)  # images count ~1k tokens each, not their base64

    def test_not_an_image(self):
        a = self.seeing_agent()
        (Path(a.root) / "notes.txt").write_text("hi")
        self.assertIn("isn't an image", a.tools.call("look_at_image", json.dumps({"path": "notes.txt"})))
