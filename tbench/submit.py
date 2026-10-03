"""Check a finished run against the Terminal-Bench 2.0 leaderboard's rules and get it ready to submit.

    tbench/submit.py ~/.local/state/purr/tbench/<job folder>

The leaderboard (Harbor Hub: terminal-bench/terminal-bench-2/2-0) takes a run only if a bot and then
a maintainer can verify it, so this checks what they check, before you upload anything:

- every one of the 89 tasks, at least 5 tries each (tbench/fair.sh --submit)
- time limits as the tasks set them: timeout multiplier 1.0, no timeout or resource overrides
- every trial finished with a result.json, and has purr's own log (the trajectory reviewers read)
- purr's web tool was off (the rules: no looking at the benchmark's site or repo)
- the exact purr commit, with no uncommitted changes
- no API key anywhere in the folder (keys that look like keys, and the real ones purr knows)

If it all passes, it writes a submission folder (metadata.yaml + the job) next to the jobs and prints
the two steps that need your accounts: the public upload to Harbor Hub and the submission itself.
Plain Python (no packages).
"""

import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402 - the benchmarks' names and sizes live there, once

OUT = Path.home() / ".local" / "state" / "purr" / "tbench" / "submissions"
BENCH = "Terminal-Bench 2.0"
TASKS = publish.BENCHES[BENCH]["tasks"]
TRIES = 5
OVERRIDES = ("override_timeout_sec", "max_timeout_sec", "override_cpus", "override_memory_mb",
             "override_storage_mb", "verifier_override_timeout_sec", "verifier_max_timeout_sec",
             "override_verifier_timeout_sec")
KEY_SHAPES = re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{32,}|nvapi-[A-Za-z0-9_-]{20,}|"
                        r"gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|csk-[A-Za-z0-9]{20,}")
METADATA = """agent_url: https://github.com/rephyr/purr
agent_display_name: "purr"
agent_org_display_name: "rephyr"

models:
  - model_name: {model_name}
    model_provider: {provider}
    model_display_name: "{model_display}"
    model_org_display_name: "{model_org}"
"""
MODELS = {"deepseek/deepseek-v4.1-flash": ("deepseek-v4.1-flash", "deepseek", "DeepSeek V4.1 Flash", "DeepSeek")}


def overrides_in(node, path=""):
    """Every override setting with a value, anywhere in the job's config."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in OVERRIDES and v not in (None, False, 0):
                found.append(f"{path}{k}={v}")
            found += overrides_in(v, f"{path}{k}.")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += overrides_in(v, f"{path}{i}.")
    return found


def known_keys():
    """The real API keys purr can find, so a leaked one is caught even if it doesn't look like a key."""
    try:
        sys.path.insert(0, str(ROOT))
        from harness.agent import provider_key
        from harness import settings
        providers = settings.load(discover=False)["providers"]
    except Exception:  # noqa: BLE001 - then only the shapes are checked
        return []
    keys = []
    for p in providers.values():
        try:
            key = provider_key(p)
        except Exception:  # noqa: BLE001
            key = None
        if key and len(key) >= 16:
            keys.append(key)
    return keys


def check(job):
    """[(ok, what)] for every rule, and the facts the metadata needs."""
    config = json.loads((job / "config.json").read_text())
    agent = (config.get("agents") or [{}])[0]
    dataset = (config.get("datasets") or [{}])[0]
    trials = [d for d in job.iterdir() if d.is_dir() and "__" in d.name]
    results, versions, per_task = [], set(), Counter()
    for d in trials:
        try:
            r = json.loads((d / "result.json").read_text())
        except (OSError, ValueError):
            continue
        results.append(d)
        per_task[str(r.get("task_name", "")).split("/")[-1]] += 1
        versions.add((r.get("agent_info") or {}).get("version"))
    # purr's log, or for a trial that broke while installing (before purr ran) Harbor's setup logs
    logs = [d for d in results if (d / "agent" / "purr.txt").exists()
            or any((d / "agent").glob("setup/*")) or any((d / "agent").glob("*.txt"))]
    name = str(dataset.get("name") or "")
    kwargs = agent.get("kwargs") or {}
    multiplier = config.get("agent_timeout_multiplier", config.get("timeout_multiplier", 1.0)) or 1.0
    leaks, keys = [], known_keys()
    for f in job.rglob("*"):
        if f.is_file() and f.stat().st_size < 50_000_000:
            text = f.read_text(errors="ignore")
            if KEY_SHAPES.search(text) or any(k in text for k in keys):
                leaks.append(str(f.relative_to(job)))
    short = [t for t, n in per_task.items() if n < TRIES]
    rules = [
        (publish.bench_name("@".join(str(x) for x in (name, dataset.get("version")) if x)) == BENCH,
         f"dataset is {BENCH} ({name or '?'})"),
        (len(per_task) == TASKS, f"all {TASKS} tasks ran ({len(per_task)})"),
        (not short and per_task, f"at least {TRIES} tries every task" + (f" (short: {', '.join(sorted(short)[:5])})" if short else "")),
        (len(results) == len(trials), f"every trial has a result.json ({len(results)}/{len(trials)})"),
        (len(logs) == len(results), f"every trial has its logs, the trajectory reviewers read ({len(logs)}/{len(results)})"),
        (float(multiplier) == 1.0, f"timeout multiplier 1.0 ({multiplier})"),
        (not overrides_in(config), "no timeout or resource overrides" + (f": {overrides_in(config)}" if overrides_in(config) else "")),
        (str(kwargs.get("web", "")).lower() in ("false", "0", "no", "off"), "purr's web tool was off (web=false)"),
        (len(versions) == 1 and None not in versions and "dirty" not in str(next(iter(versions), "")),
         f"one exact purr commit, nothing uncommitted ({', '.join(sorted(map(str, versions)))})"),
        (not leaks, "no API keys in the folder" + (f": {', '.join(leaks[:5])}" if leaks else "")),
    ]
    model = str(agent.get("model_name") or "")
    return rules, model, next(iter(versions), None)


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 1
    job = Path(argv[0]).expanduser().resolve()
    if not (job / "config.json").exists():
        print(f"no Harbor job in {job}")
        return 1
    rules, model, version = check(job)
    for ok, what in rules:
        print(f"  {'✓' if ok else '✗'} {what}")
    if not all(ok for ok, _ in rules):
        print("\nnot ready: fix what's marked ✗ (most need a new run: tbench/fair.sh --submit)")
        return 1
    hosted = model.split("/", 1)[1] if model.startswith("openrouter/") else model
    if hosted not in MODELS:
        print(f"\nno leaderboard metadata for {model}: add it to MODELS in tbench/submit.py")
        return 1
    folder = OUT / f"purr__{MODELS[hosted][0]}"
    folder.mkdir(parents=True, exist_ok=True)
    name, provider, display, org = MODELS[hosted]
    (folder / "metadata.yaml").write_text(METADATA.format(model_name=name, provider=provider,
                                                          model_display=display, model_org=org))
    for old in folder.iterdir():  # one job a submission: an earlier run's copy would go along too
        if old.is_dir() and old.name != job.name and (old / "config.json").exists():
            shutil.rmtree(old)
    if not (folder / job.name).exists():
        shutil.copytree(job, folder / job.name)
    print(f"\nready: purr {version}, {model}\n  {folder}\n")
    print("next, with your accounts (purr doesn't do these for you):")
    print(f"  1. harbor upload {job} --public        (Harbor Hub: reviewers read the trajectories there)")
    print("  2. the submission: Harbor Hub's new process when it opens (the same upload counts), or a pull")
    print("     request to huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard adding")
    print(f"     submissions/terminal-bench/2.0/purr__{name}/ from the folder above")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
