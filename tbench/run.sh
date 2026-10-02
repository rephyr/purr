#!/bin/sh
# purr on Terminal-Bench (Harbor's runner), with purr's own OpenRouter key.
#
#   tbench/run.sh -l 10                       the first 10 tasks
#   tbench/run.sh -i <task-name>              one task (repeat -i for more)
#   tbench/run.sh -l 10 --ak mcp=false        purr without its MCP servers
#   tbench/run.sh -n 4 ...                    4 tasks at a time (API models only)
#   PURR_MODEL=openrouter/<id> tbench/run.sh  another model (default: DeepSeek V4.1 Flash)
#
# Everything after run.sh goes straight to `harbor run`. Results land in
# ~/.local/state/purr/tbench/<date>/ (each trial's purr.txt and purr-state/ hold purr's own log).
set -eu
PURR=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    OPENROUTER_API_KEY=$("$PURR/.venv/bin/python" -c "
import sys, tomllib; sys.path.insert(0, '$PURR')
from harness.agent import provider_key
print(provider_key(tomllib.load(open('$PURR/config.toml', 'rb'))['providers']['openrouter']) or '')")
fi
[ -n "$OPENROUTER_API_KEY" ] || { echo "no OpenRouter key: purr --key openrouter" >&2; exit 1; }
export OPENROUTER_API_KEY
export PYTHONPATH="$PURR${PYTHONPATH:+:$PYTHONPATH}"
exec harbor run -d "${PURR_DATASET:-terminal-bench/terminal-bench@4.0.0}" -e docker \
    -a tbench.purr_agent:PurrAgent -m "${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}" \
    -o "$HOME/.local/state/purr/tbench" "$@"
