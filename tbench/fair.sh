#!/bin/sh
# The published Terminal-Bench run: as close as we can get to the settings DeepSeek used to put
# every harness through Terminal-Bench 2.1 with DeepSeek V4.1 Flash (their model card): 3 attempts
# per task, temperature 1.0, top_p 0.95, a 1M context, 500 steps, Linux containers. The model is
# served by DeepSeek itself (OpenRouter pinned to the "deepseek" host, no fallback to other hosts,
# some of which run it at fp4/fp8), on Terminal-Bench 2.1 like theirs (PURR_DATASET=terminal-bench@2.0
# for the older 2.0, which gets its own tables). DeepSeek's host trains on prompts, so OpenRouter has
# to allow "paid model training" (openrouter.ai/settings/privacy). Then: tbench/publish.py <job folder>.
#
#   tbench/fair.sh              the full run: all 89 tasks x 3
#   tbench/fair.sh --one        all 89 tasks x 1: the same score, a wider margin
#   tbench/fair.sh --quick      the quick set: the same 20 tasks x 1, for comparing purr versions with
#                               each other (tbench/quick-tasks.txt)
#   (time and cost of each: `purr bench --real` shows the current estimates, harness/realbench.py)
#   tbench/fair.sh --submit     for the Terminal-Bench 2.0 leaderboard (Harbor Hub): every task x 5,
#                               purr's web tool off (no peeking at the benchmark); then tbench/submit.py
#   tbench/fair.sh --deepswe [--one|--quick]
#                               DeepSWE 1.1 instead: 113 tasks in real projects, no network in the task
#                               container (purr only reaches the model), its quick set in
#                               tbench/quick-tasks-deepswe.txt
set -eu
cd "$(dirname "$0")/.."
QUICK_FILE=tbench/quick-tasks.txt
OFFLINE=""
SUBMIT=""
SIZE=""
DEEPSWE=""
# the leading options, in any order: one size (--quick, --one or --submit) and maybe --deepswe
while :; do
    case "${1:-}" in
        --quick|--one|--submit)
            [ -z "$SIZE" ] || { echo "fair.sh: pick one of --quick, --one and --submit" >&2; exit 1; }
            SIZE="$1"
            shift ;;
        --deepswe)
            DEEPSWE=1
            shift ;;
        *) break ;;
    esac
done
if [ "$SIZE" = "--submit" ]; then
    [ -z "$DEEPSWE" ] || { echo "fair.sh: --submit is for the Terminal-Bench 2.0 leaderboard, not DeepSWE" >&2; exit 1; }
    # the leaderboard's own dataset (Harbor Hub package), 5 tries a task, the rest as always
    PURR_DATASET="${PURR_DATASET:-terminal-bench/terminal-bench-2}"
    SUBMIT="--ak web=false"
fi
if [ -n "$DEEPSWE" ]; then
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
ATTEMPTS=3
TASKS=""
case "$SIZE" in
    --submit) ATTEMPTS=5 ;;
    --one) ATTEMPTS=1 ;;
    --quick)
        ATTEMPTS=1
        TASKS=$(grep -v '^#' "$QUICK_FILE" | awk 'NF {printf "-i %s ", $1}') ;;
esac
# the quick set's tasks join the ones picked by hand; common.sh gives them all the dataset's org/
# shellcheck disable=SC2086  # $TASKS is a list of -i options
set -- $TASKS "$@"
. tbench/common.sh
export PURR_MODEL="${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}"
case "$PURR_MODEL" in
    openrouter/*) ;;
    *) echo "fair.sh runs the fair settings on OpenRouter (pinned to the deepseek host); other models:" \
            "tbench/run.sh, or tbench/local.sh for Ollama" >&2
       exit 1 ;;
esac
# one tiny request to the pinned host first: a refused host (OpenRouter privacy settings, an
# outage) would otherwise turn the whole run into errors (PURR_SKIP_PREFLIGHT=1: the tests skip it)
[ "${PURR_SKIP_PREFLIGHT:-}" = 1 ] || .venv/bin/python - <<'PY' || exit 1
import json, os, sys, tomllib, urllib.error, urllib.request
sys.path.insert(0, ".")
from harness.agent import provider_key
from harness.settings import load
p = load(discover=False)["providers"]["openrouter"]
model = os.environ["PURR_MODEL"].split("/", 1)[1]
body = {"model": model, "messages": [{"role": "user", "content": "Reply: ok"}],
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
except (urllib.error.URLError, OSError) as e:
    sys.exit(f"can't reach OpenRouter for the test request: {getattr(e, 'reason', e)}")
PY
# prebuilt task images (DeepSWE: ~2.7 GB each) stay after their trial: remove each once it's graded,
# or a whole run fills the disk. It watches this process ($$, Harbor after the exec) and stops with it.
JOB_NAME="${PURR_JOB:-$(date +%Y-%m-%d__%H-%M-%S)}"  # a known name: the cleaner and the window find it
python3 tbench/clean_images.py "$$" "$HOME/.local/state/purr/tbench/$JOB_NAME" "$ATTEMPTS" >/dev/null 2>&1 &
# a trial whose container never started (RuntimeError: an image pull refused by a rate limit,
# a compose hiccup) is tried again, up to 3 times with growing waits: it's not purr's answer
# shellcheck disable=SC2086  # $OFFLINE and $SUBMIT are options
exec tbench/run.sh --job-name "$JOB_NAME" -k "$ATTEMPTS" -n "${PURR_JOBS:-6}" $OFFLINE $SUBMIT --max-retries 3 --retry-include RuntimeError \
    --ak hosts=deepseek --ak temperature=1.0 --ak top_p=0.95 --ak max_tokens=65536 --ak max_steps=500 "$@"
