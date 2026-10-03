#!/bin/sh
# Another harness on purr's fair settings, to set next to a purr run on the very same tasks: DeepSeek
# V4.1 Flash on OpenRouter pinned to DeepSeek's own host (no fallback), temperature 1.0, top_p 0.95,
# 64k output, 500 steps, the same dataset and quick set as tbench/fair.sh. Harbor runs the harness
# itself (its built-in agent); only the harness differs. Then: tbench/versus.py <purr job> <this job>.
# These runs are never published as purr's (publish.py refuses them).
#
#   tbench/compare.sh opencode --quick       the quick 20, once each (about $0.60-1)
#   tbench/compare.sh opencode --one         every task once
#   tbench/compare.sh opencode -i chess-best-move
set -eu
cd "$(dirname "$0")/.."
AGENT="${1:-}"
[ $# -gt 0 ] && shift
SIZE=""
case "${1:-}" in --quick|--one) SIZE="$1"; shift ;; esac
case "$AGENT" in
    opencode)
        # OpenCode's config (~/.config/opencode/opencode.json, merged by Harbor): the build agent's
        # sampling and step limit, and the model's output limit and OpenRouter routing
        CONFIG='{"agent": {"build": {"temperature": 1.0, "top_p": 0.95, "steps": 500}},
                 "provider": {"openrouter": {"models": {"deepseek/deepseek-v4.1-flash": {
                     "limit": {"context": 1000000, "output": 65536},
                     "options": {"provider": {"only": ["deepseek"], "allow_fallbacks": false}}}}}}}'
        SETTINGS="opencode_config=$(printf '%s' "$CONFIG" | tr -s ' \n' ' ')" ;;
    *) echo "compare.sh: which harness? opencode (Harbor's other agents need their own settings here)" >&2
       exit 1 ;;
esac
export PURR_DATASET="${PURR_DATASET:-terminal-bench/terminal-bench-2-1}"
export PURR_MODEL="openrouter/deepseek/deepseek-v4.1-flash"
export PURR_HARBOR_AGENT="$AGENT"
TASKS=""
[ "$SIZE" = "--quick" ] && TASKS=$(grep -v '^#' tbench/quick-tasks.txt | awk 'NF {printf "-i %s ", $1}')
# shellcheck disable=SC2086  # $TASKS is a list of -i options
set -- $TASKS "$@"
. tbench/common.sh
JOB_NAME="${PURR_JOB:-$(date +%Y-%m-%d__%H-%M-%S)__$AGENT}"
python3 tbench/clean_images.py "$$" "$HOME/.local/state/purr/tbench/$JOB_NAME" 1 >/dev/null 2>&1 &
exec tbench/run.sh --job-name "$JOB_NAME" -k 1 -n "${PURR_JOBS:-6}" --max-retries 3 --retry-include RuntimeError \
    --ak "$SETTINGS" "$@"
