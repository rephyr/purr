#!/usr/bin/env python3
"""purr: a tiny coding agent for the terminal. Run `purr` in a project folder."""

import argparse
import os
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PYTHON = HERE / ".venv/bin/python"
sys.path.insert(0, str(HERE))

from harness import commands, ui  # noqa: E402
from harness.agent import STATE_DIR, Agent, list_sessions  # noqa: E402

HISTORY = STATE_DIR / "history"

PLAIN_HELP = """  ctrl+c         stop the current answer
  end a line with \\ to keep typing on the next line"""


def send(agent, view, text):
    try:
        agent.turn(text)
    except KeyboardInterrupt:
        view.end_reply()
        agent.save_log()
        ui.say(ui.ROSE, "\n  stopped")


def plain_pr(agent):
    """/pr in plain mode: draft, show, ask, then branch + commit + push + gh pr create."""
    from harness import pr
    files = pr.changed_files(agent.root)
    if not files:
        ui.say(ui.DIM, "  no changes to make a pull request from" if files is not None else "  not a git repo")
        return
    ui.say(ui.DIM, "  writing the pull request…")
    title, body = pr.draft(agent)
    ui.say(ui.LILAC, f"\n  {title}\n")
    ui.say(ui.TEXT if hasattr(ui, "TEXT") else ui.RESET, "\n".join("  " + l for l in body.splitlines()))
    ui.say(ui.DIM, "\n  files: " + ", ".join(f[3:] for f in files))
    ui.say(ui.DIM, f"  co-author: purr-{agent.model_name}")
    ans = input(f"{ui.YELLOW}  push and open the pull request?  [y]es  [t]itle  [n]o {ui.RESET}").strip().lower()
    if ans.startswith("t"):
        title = input("  title: ").strip() or title
        ans = "y"
    if ans not in ("y", "yes"):
        ui.say(ui.DIM, "  okay, nothing was pushed")
        return
    try:
        ui.say(ui.MINT, f"  ♡ {pr.create(agent, title, body)}")
    except RuntimeError as e:
        ui.say(ui.ROSE, f"  {e}")


def refined(agent, text):
    """/refine is on: show the rewritten task, then send it, your original, or your edit."""
    ui.say(ui.DIM, "  refining your message…")
    try:
        better = agent.refine(text)
    except Exception as e:  # noqa: BLE001 - fall back to what you wrote
        ui.say(ui.ROSE, f"  couldn't refine it ({e}), sending yours")
        return text
    ui.say(ui.LILAC, "\n" + "\n".join("  " + line for line in better.splitlines()) + "\n")
    ans = input(f"{ui.YELLOW}  send this?  [y]es  [o]riginal  [e]dit  [n]o {ui.RESET}").strip().lower()
    if ans.startswith("o"):
        return text
    if ans.startswith("e"):
        return input("  your version: ").strip() or better
    return better if ans in ("", "y", "yes") else ""


def read_message():
    lines = []
    while True:
        line = input(ui.prompt_text() if not lines else "  ")
        if line.endswith("\\"):
            lines.append(line[:-1])
            continue
        lines.append(line)
        return "\n".join(lines).strip()


def plain(agent, view):
    import readline

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    try:
        readline.read_history_file(HISTORY)
    except OSError:
        pass

    folder = str(agent.root).replace(str(Path.home()), "~", 1)
    ui.say(ui.PINK, f"{ui.BOLD}purr ♡{ui.RESET}{ui.LILAC}  {agent.model_name}  {folder}   /help")
    try:
        while True:
            try:
                text = read_message()
            except KeyboardInterrupt:
                ui.out()
                continue
            if not text:
                continue
            if text.startswith("!"):
                output, code = agent.shell(text[1:].strip())
                ui.say(ui.DIM, output or "(no output)")
                continue
            if text.startswith("/"):
                result = commands.run(agent, text)
                if result is None:
                    break
                if isinstance(result, dict) and result.get("pr"):
                    plain_pr(agent)
                    continue
                if isinstance(result, dict) and result.get("plan_run"):
                    try:
                        agent.run_plan(result["plan_run"])
                    except KeyboardInterrupt:
                        view.end_reply()
                        agent.save_log()
                        ui.say(ui.ROSE, "\n  stopped")
                    continue
                if isinstance(result, dict):
                    send(agent, view, result["send"])
                    continue
                for kind, line in result:
                    view.note(line, kind if kind != "dim" else "info")
                if commands.parse(text)[0] == "help":
                    ui.say(ui.LILAC, PLAIN_HELP)
                continue
            if agent.should_refine(text):
                text = refined(agent, text)
                if not text:
                    continue
            send(agent, view, text)
    except EOFError:
        ui.out()
    finally:
        readline.write_history_file(HISTORY)


def main():
    if sys.argv[1:2] == ["bench"]:  # purr bench: purr vs OpenCode on the same tasks (harness/bench.py)
        from harness import bench
        window = sys.stdout.isatty() and sys.stdin.isatty() and "--plain" not in sys.argv
        if window:
            try:
                import textual  # noqa: F401
            except ImportError:  # the window needs the project's own Python
                if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
                    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
                window = False
        return bench.main(sys.argv[2:], tomllib.loads((HERE / "config.toml").read_text()), window=window)
    ap = argparse.ArgumentParser(prog="purr", description="tiny coding agent")
    ap.add_argument("folder", nargs="?", default=".", help="project folder (default: here)")
    ap.add_argument("-m", "--model", help="model name from config.toml")
    ap.add_argument("-p", "--prompt", help="do one task and exit (plain mode)")
    ap.add_argument("--plain", action="store_true", help="simple scrolling mode instead of the full-screen one")
    ap.add_argument("-c", "--continue", dest="resume", action="store_true",
                    help="carry on the last chat in this folder")
    ap.add_argument("--yes", action="store_true", help="allow edits and commands without asking")
    args = ap.parse_args()

    try:
        args.folder = str(Path(args.folder).resolve(strict=True))
    except (FileNotFoundError, OSError):
        ui.say(ui.ROSE, f"can't find the folder {args.folder!r}"
               + (" (the one this terminal is in was deleted; cd somewhere else)" if args.folder == "." else ""))
        return 1

    use_tui = not (args.plain or args.prompt) and sys.stdin.isatty()
    if use_tui:
        try:
            import textual  # noqa: F401
        except ImportError:
            # the full-screen mode lives in the project's own Python (uv sync made it)
            if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
                os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
            ui.say(ui.ROSE, "textual isn't installed: run `uv sync` in ~/projects/purr, or use --plain")
            return 1

    config = tomllib.loads((HERE / "config.toml").read_text())
    model_name = args.model or config["default_model"]

    if use_tui:
        from tui.app import PurrApp
        app = PurrApp(config, args.folder, model_name, trust=args.yes, resume=args.resume)
        app.run()
        return app.return_code or 0

    view = ui.PlainView()
    try:
        agent = Agent(config, args.folder, model_name, view)
    except KeyError as e:
        ui.say(ui.ROSE, e.args[0])
        return 1
    agent.tools.trust_all = args.yes
    if args.resume:
        sessions = list_sessions(agent.root)
        if sessions:
            agent.load(sessions[0]["path"])
            ui.say(ui.LILAC, f"  carrying on: {sessions[0]['title']}")

    if args.prompt:
        send(agent, view, args.prompt)
        return 0
    plain(agent, view)
    return 0


if __name__ == "__main__":
    sys.exit(main())
