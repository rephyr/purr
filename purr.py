#!/usr/bin/env python3
"""purr: a tiny coding agent for the terminal. Run `purr` in a project folder."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PYTHON = HERE / ".venv/bin/python"
sys.path.insert(0, str(HERE))

if os.name == "nt":  # its shell and terminal tools need a POSIX system (pty, bash)
    sys.exit("purr runs on Linux and macOS. On Windows, run it inside WSL: `wsl --install`, then "
             "install purr in the WSL terminal (README: Windows).")

from harness import commands, settings, ui  # noqa: E402
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
    ui.say(ui.RESET, "\n".join("  " + l for l in body.splitlines()))
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
                if isinstance(result, dict) and result.get("agent_new"):
                    from harness import agents as agent_files
                    ui.say(ui.DIM, "  writing your agent…")
                    path = agent_files.save(agent_files.draft(agent, result["agent_new"]))
                    agent.reload_agents()
                    if path.stem in agent.agents:
                        agent.switch_mode(path.stem)
                    ui.say(ui.MINT, f"  ♡ agent {path.stem} is on · its file: {path}")
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


def save_key(name, config):
    """purr --key groq: asks for the key without showing it and keeps it in ~/.config/purr/keys.toml,
    readable only by you. Typed into the chat, it would end up in the saved chat logs."""
    import getpass
    from harness.agent import KEYS_FILE
    provider = config["providers"].get(name)
    if not provider or not provider.get("api_key_env"):
        have = ", ".join(n for n, p in config["providers"].items() if p.get("api_key_env"))
        ui.say(ui.ROSE, f"no provider called {name!r} that takes a key. Have: {have}")
        return 1
    if provider.get("key_page"):
        ui.say(ui.LILAC, f"get one at {provider['key_page']}")
    key = getpass.getpass(f"{name} key (hidden): ").strip()
    if not key:
        ui.say(ui.ROSE, "nothing saved")
        return 1
    try:
        keys = tomllib.loads(KEYS_FILE.read_text())
    except (OSError, ValueError):
        keys = {}
    keys[provider["api_key_env"]] = key
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.touch(mode=0o600)
    KEYS_FILE.chmod(0o600)
    # quoted the TOML way (json.dumps escapes " and \\ the same): one odd character used to make the
    # whole file unreadable, and every saved key silently vanished with it
    KEYS_FILE.write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in keys.items()))
    ui.say(ui.MINT, f"saved ♡ {name} models work now (/free check tests them)")
    return 0


def full_screen_python():
    """The full-screen windows need Textual: from a clone, run again in the project's own Python
    (uv sync made it). False when there's no Textual anywhere."""
    try:
        import textual  # noqa: F401
        return True
    except ImportError:
        if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
            os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
        return False


def run_setup():
    """The setup window (tui/setup.py). Returns the model you picked, or None if you left."""
    from tui.setup import SetupApp
    return SetupApp(settings.load(discover=False)).run()


def main():
    if sys.argv[1:2] == ["setup"]:  # purr setup: local models, API keys, your model, the cat
        if not full_screen_python():
            ui.say(ui.ROSE, "the setup window needs Textual (uv sync in purr's folder)")
            return 1
        picked = run_setup()
        ui.say(ui.MINT if picked else ui.DIM, f"  all set ♡ purr starts on {picked}" if picked else "  nothing changed")
        return 0
    if sys.argv[1:2] == ["bench"]:  # purr bench: purr vs OpenCode on the same tasks (harness/bench.py)
        if not (HERE / "bench" / "tasks").is_dir():  # its tasks live in the repo, not in the package
            ui.say(ui.ROSE, "purr bench runs from a clone: git clone https://github.com/rephyr/purr && cd purr "
                            "&& uv run purr bench")
            return 1
        from harness import bench
        window = sys.stdout.isatty() and sys.stdin.isatty() and "--plain" not in sys.argv
        if window:
            try:
                import textual  # noqa: F401
            except ImportError:  # the window needs the project's own Python
                if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
                    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])
                window = False
        return bench.main(sys.argv[2:], settings.load(), window=window)
    ap = argparse.ArgumentParser(prog="purr", description="tiny coding agent",
                                 epilog="purr setup: the setup window again · purr bench: purr vs OpenCode (from a clone)")
    ap.add_argument("folder", nargs="?", default=".", help="project folder (default: here)")
    ap.add_argument("-m", "--model", help="model name (/models lists them)")
    ap.add_argument("-p", "--prompt", help="do one task and exit (plain mode)")
    ap.add_argument("--prompt-file", metavar="FILE",
                    help="like -p, the task read from a file (it isn't on purr's command line then, where a "
                         "`pkill -f <a word from the task>` would stop purr itself)")
    ap.add_argument("--plain", action="store_true", help="simple scrolling mode instead of the full-screen one")
    ap.add_argument("-c", "--continue", dest="resume", action="store_true",
                    help="carry on the last chat in this folder")
    ap.add_argument("--yes", action="store_true", help="allow edits and commands without asking")
    ap.add_argument("--key", metavar="PROVIDER", help="save an API key for a provider (asks for it, hidden)")
    ap.add_argument("--version", action="version", version=f"purr {__import__('harness').VERSION}")
    ap.add_argument("--time-limit", type=float, metavar="SECONDS",
                    help="how long a task may take: purr says so, and reminds the model at half and 4/5 time")
    args = ap.parse_args()
    if args.prompt_file:
        try:
            args.prompt = Path(args.prompt_file).read_text()
        except OSError as e:
            ui.say(ui.ROSE, f"can't read the task: {e}")
            return 1
    if args.key:
        return save_key(args.key, settings.load(discover=False))

    try:
        args.folder = str(Path(args.folder).resolve(strict=True))
    except (FileNotFoundError, OSError):
        ui.say(ui.ROSE, f"can't find the folder {args.folder!r}"
               + (" (the one this terminal is in was deleted; cd somewhere else)" if args.folder == "." else ""))
        return 1

    use_tui = not (args.plain or args.prompt) and sys.stdin.isatty()
    if use_tui and not full_screen_python():
        ui.say(ui.ROSE, "textual isn't installed: run `uv sync` in purr's folder, or use --plain")
        return 1
    if use_tui and not settings.is_set_up() and not args.model:
        # the first start: find the models, add keys, pick one (purr setup does it again any time)
        if run_setup() is None:
            ui.say(ui.DIM, "  setup skipped: it opens again next time (or `purr setup`)")
            return 0

    config = settings.load()
    model_name = args.model or config["default_model"]

    if use_tui:
        from tui.app import PurrApp
        while True:
            app = PurrApp(config, args.folder, model_name, trust=args.yes, resume=args.resume)
            if app.run() != "setup":
                return app.return_code or 0
            run_setup()  # /setup inside purr: then purr starts again with what you picked
            config = settings.load()
            model_name = config["default_model"]
            args.resume = True  # carry on the chat you were in

    view = ui.PlainView()
    try:
        agent = Agent(config, args.folder, model_name, view)
    except KeyError as e:
        ui.say(ui.ROSE, e.args[0])
        if not args.model and not any(m.get("found") for m in config["models"].values()):
            ui.say(ui.DIM, f"  no local model found ({config['providers']['ollama']['base_url']}, llama.cpp, LM Studio, "
                           "vLLM) and no API key: `purr setup` walks you through it, or `purr --key groq` for a free key")
        return 1
    agent.tools.trust_all = args.yes
    agent.time_limit = args.time_limit
    if args.prompt:
        agent.set_one_shot()  # one task, then purr exits: there's nobody to answer a question
    if args.resume:
        sessions = list_sessions(agent.root)
        if sessions:
            agent.load(sessions[0]["path"])
            ui.say(ui.LILAC, f"  carrying on: {sessions[0]['title']}")

    if args.prompt:
        send(agent, view, args.prompt)
        # a one-shot run that ended on an API error failed: say so with the exit code (benchmarks
        # count it as an error to re-run, not as a task purr got wrong)
        return 3 if getattr(agent, "failed", None) else 0
    plain(agent, view)
    return 0


if __name__ == "__main__":
    sys.exit(main())
