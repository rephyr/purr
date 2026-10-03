#!/bin/sh
# purr on Terminal-Bench (Harbor's runner), with purr's own key for the model's provider.
#
#   tbench/run.sh -l 10                       the first 10 tasks
#   tbench/run.sh -i <task-name>              one task (repeat -i for more)
#   tbench/run.sh -l 10 --ak mcp=false        purr without its MCP servers
#   tbench/run.sh -n 4 ...                    4 tasks at a time (API models only)
#   PURR_MODEL=<provider>/<id> tbench/run.sh  another model (default: DeepSeek V4.1 Flash on OpenRouter)
#   PURR_DATASET=terminal-bench@2.0 ...       another dataset (default: Terminal-Bench 4.0)
#   tbench/fair.sh                            the published, fair run (see benchmarks/terminal-bench/)
#   tbench/run.sh resume <job folder>         run a finished job's trials that broke on Harbor/Docker
#                                             (RuntimeError: a container that wouldn't start, an image
#                                             pull refused) again, in the same job folder
#
# Everything after run.sh goes straight to `harbor run`. Results land in
# ~/.local/state/purr/tbench/<date>/ (each trial's agent/purr.txt and purr-state/ hold purr's own log).
set -eu
PURR=$(cd "$(dirname "$0")/.." && pwd)
MODEL="${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}"
PROVIDER="${MODEL%%/*}"
# the provider's key, the way purr finds it (environment, purr --key, opencode auth)
# (in a variable first: `eval "$(...)"` returns 0 even when the snippet fails, so set -e
# would never stop a run that has no key)
KEY_LINE=$("$PURR/.venv/bin/python" -c "
import shlex, sys, tomllib; sys.path.insert(0, '$PURR')
from harness.agent import provider_key
from harness.settings import load
p = load(discover=False)['providers'].get('$PROVIDER')
if not p: sys.exit('purr has no provider $PROVIDER')
env, key = p.get('api_key_env'), provider_key(p)
if env and not key: sys.exit(f'no key for $PROVIDER: purr --key $PROVIDER')
print(f'export {env}={shlex.quote(key)}' if env else '')") || exit 1
eval "$KEY_LINE"
export PYTHONPATH="$PURR${PYTHONPATH:+:$PYTHONPATH}"
if [ "${1:-}" = "resume" ]; then
    exec harbor jobs resume -p "$2" -f RuntimeError
fi
# PURR_HARBOR_AGENT: another harness instead of purr (tbench/compare.sh), with the same key and model
exec harbor run -d "${PURR_DATASET:-terminal-bench/terminal-bench@4.0.0}" -e docker \
    -a "${PURR_HARBOR_AGENT:-tbench.purr_agent:PurrAgent}" -m "$MODEL" -o "$HOME/.local/state/purr/tbench" "$@"
