#!/bin/sh
# The published Terminal-Bench run: as close as we can get to the settings DeepSeek used to put
# every harness through Terminal-Bench 2.1 with DeepSeek V4.1 Flash (their model card): 3 attempts
# per task, temperature 1.0, top_p 0.95, a 1M context, 500 steps, Linux containers. The model is
# served by DeepSeek itself (OpenRouter pinned to the "deepseek" host, no fallback to other hosts,
# some of which run it at fp4/fp8), on Terminal-Bench 2.1 like theirs (PURR_DATASET=terminal-bench@2.0
# for the older 2.0, which gets its own tables). DeepSeek's host trains on prompts, so OpenRouter has
# to allow "paid model training" (openrouter.ai/settings/privacy). Then: tbench/publish.py <job folder>.
#
#   tbench/fair.sh              the full run: all 89 tasks x 3 (~$6, 4-5 hours)
#   tbench/fair.sh --one        all 89 tasks x 1 (~$2, about 2 hours): the same score, a wider margin
#   tbench/fair.sh --quick      the quick set: the same 20 tasks x 1 (under $0.50, about an hour), for
#                               comparing purr versions with each other (tbench/quick-tasks.txt)
#   tbench/fair.sh --deepswe [--one|--quick]
#                               DeepSWE 1.1 instead: 113 tasks in real projects, no network in the task
#                               container (purr only reaches the model), its quick set in
#                               tbench/quick-tasks-deepswe.txt
set -eu
cd "$(dirname "$0")/.."
QUICK_FILE=tbench/quick-tasks.txt
OFFLINE=""
if [ "${1:-}" = "--deepswe" ]; then
    shift
    PURR_DATASET="${PURR_DATASET:-datacurve/deep-swe-1-1}"
    QUICK_FILE=tbench/quick-tasks-deepswe.txt
    # its tasks run with no network; the agent may still reach the model, like in DeepSeek's runs
    OFFLINE="--allow-agent-host openrouter.ai"
    # Harbor builds the network filter with buildx: without it every trial fails in a second
    if ! docker buildx version >/dev/null 2>&1; then
        echo "DeepSWE needs docker buildx (Harbor's network filter): sudo pacman -S docker-buildx" >&2
        exit 1
    fi
fi
export PURR_DATASET="${PURR_DATASET:-terminal-bench/terminal-bench-2-1}"
# a package dataset (org/name, like 2.1) names its tasks org/task: mteb-retrieve -> terminal-bench/mteb-retrieve
case "$PURR_DATASET" in */*) ORG="${PURR_DATASET%%/*}/" ;; *) ORG="" ;; esac
ATTEMPTS=3
TASKS=""
if [ "${1:-}" = "--one" ]; then
    shift
    ATTEMPTS=1
elif [ "${1:-}" = "--quick" ]; then
    shift
    ATTEMPTS=1
    TASKS=$(grep -v '^#' "$QUICK_FILE" | awk -v org="$ORG" 'NF {printf "-i %s%s ", org, $1}')
fi
# the same for tasks picked by hand (-i name)
n=$#
while [ "$n" -gt 0 ]; do
    a="$1"
    shift
    n=$((n - 1))
    if [ "$a" = "-i" ] && [ "$n" -gt 0 ]; then
        t="$1"
        shift
        n=$((n - 1))
        case "$t" in */*) ;; *) t="$ORG$t" ;; esac
        set -- "$@" -i "$t"
    else
        set -- "$@" "$a"
    fi
done
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "purr has uncommitted changes: commit first, so the results name the exact version" >&2
    [ "${PURR_ALLOW_DIRTY:-}" = 1 ] || exit 1
fi
# one tiny request to the pinned host first: a refused host (OpenRouter privacy settings, an
# outage) would otherwise turn the whole run into errors
.venv/bin/python - <<'PY' || exit 1
import json, sys, tomllib, urllib.error, urllib.request
sys.path.insert(0, ".")
from harness.agent import provider_key
p = tomllib.load(open("config.toml", "rb"))["providers"]["openrouter"]
body = {"model": "deepseek/deepseek-v4.1-flash", "messages": [{"role": "user", "content": "Reply: ok"}],
        "max_tokens": 5, "provider": {"only": ["deepseek"], "allow_fallbacks": False}}
req = urllib.request.Request(p["base_url"] + "/chat/completions", data=json.dumps(body).encode(),
                             headers={"Authorization": "Bearer " + (provider_key(p) or ""), "Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=60).read()
except urllib.error.HTTPError as e:
    detail = e.read().decode(errors="replace")[:300]
    hint = ("\nDeepSeek's host uses prompts for training: allow 'paid model training' at "
            "https://openrouter.ai/settings/privacy") if "training" in detail else ""
    sys.exit(f"DeepSeek's host on OpenRouter refused a test request ({e.code}): {detail}{hint}")
PY
export PURR_MODEL="${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}"
# shellcheck disable=SC2086  # $TASKS is a list of -i options, $OFFLINE one option
exec tbench/run.sh -k "$ATTEMPTS" -n "${PURR_JOBS:-6}" $TASKS $OFFLINE \
    --ak hosts=deepseek --ak temperature=1.0 --ak top_p=0.95 --ak max_tokens=65536 --ak max_steps=500 "$@"
