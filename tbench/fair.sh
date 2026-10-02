#!/bin/sh
# The published Terminal-Bench run: as close as we can get to the settings DeepSeek used to put
# every harness through Terminal-Bench 2.1 with DeepSeek V4.1 Flash (their model card): 3 attempts
# per task, temperature 1.0, top_p 0.95, a 1M context, 500 steps, Linux containers. The model is
# served by DeepSeek itself (OpenRouter pinned to the "deepseek" host, no fallback to other hosts,
# some of which run it at fp4/fp8). Terminal-Bench 2.1 isn't in Harbor's registry: 2.0 has the same
# 89 tasks, which the results say. DeepSeek's host trains on prompts, so OpenRouter has to allow
# "paid model training" (openrouter.ai/settings/privacy). Then: tbench/publish.py <job folder>.
#
#   tbench/fair.sh              everything (89 tasks x 3)
#   tbench/fair.sh -l 5 -k 1    a quick check that it all works (not for publishing)
set -eu
cd "$(dirname "$0")/.."
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
PURR_DATASET="${PURR_DATASET:-terminal-bench@2.0}" PURR_MODEL="${PURR_MODEL:-openrouter/deepseek/deepseek-v4.1-flash}" \
exec tbench/run.sh -k 3 -n "${PURR_JOBS:-6}" \
    --ak hosts=deepseek --ak temperature=1.0 --ak top_p=0.95 --ak max_tokens=65536 --ak max_steps=500 "$@"
