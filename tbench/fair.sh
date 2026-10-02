#!/bin/sh
# The published Terminal-Bench run: as close as we can get to the settings DeepSeek used to put
# every harness through Terminal-Bench 2.1 with DeepSeek V4.1 Flash (their model card): 3 attempts
# per task, temperature 1.0, top_p 0.95, a 1M context, 500 steps, Linux containers. The model is
# served by DeepSeek itself (OpenRouter pinned to the "deepseek" host, no fallback to other hosts,
# some of which run it at fp4/fp8). Terminal-Bench 2.1 isn't in Harbor's registry: 2.0 has the same
# 89 tasks, which the results say. Then: tbench/publish.py <job folder>.
#
#   tbench/fair.sh              everything (89 tasks x 3)
#   tbench/fair.sh -l 5 -k 1    a quick check that it all works (not for publishing)
set -eu
cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "purr has uncommitted changes: commit first, so the results name the exact version" >&2
    [ "${PURR_ALLOW_DIRTY:-}" = 1 ] || exit 1
fi
PURR_DATASET="${PURR_DATASET:-terminal-bench@2.0}" PURR_MODEL="${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}" \
exec tbench/run.sh -k 3 -n "${PURR_JOBS:-6}" \
    --ak hosts=deepseek --ak temperature=1.0 --ak top_p=0.95 --ak max_tokens=65536 --ak max_steps=500 "$@"
