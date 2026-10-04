"""/model free: the best free API model for what you're doing, and the next one when it's maxed out.

[free] in config.toml ranks the free models per kind of work (code, ask, talk). purr uses the
first one that's resting-free and big enough for the chat. When a model is rate-limited, out of
free requests for the day, or has no host right now, it rests for a while (remembered in
~/.local/state/purr/free.json) and the same request goes to the next one.
"""

import datetime
import json
import os
import time
from pathlib import Path

# the same folder as agent.STATE_DIR (PURR_STATE moves it, for tests)
FREE_FILE = Path(os.environ.get("PURR_STATE") or Path.home() / ".local/state/purr") / "free.json"
KIND = {"code": "code", "learn": "code", "pair": "code", "plan": "code", "ask": "ask", "chat": "talk", "create": "talk"}


def _to_midnight():
    now = datetime.datetime.now(datetime.timezone.utc)
    midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds()


def rest_for(err):
    """(seconds, why, scope) to rest after this error, scope "model" or "provider" (the provider's
    whole free allowance is used up, or its key doesn't work), or None if the error isn't about
    being maxed out or unavailable."""
    found = _rest(err)
    if not found:
        return None
    seconds, why = found
    text = str(err).lower()
    shared = "free-models-per-day" in text or why == "the key doesn't work"  # OpenRouter's daily cap is per account
    return seconds, why, "provider" if shared else "model"


def _rest(err):
    text, status = str(err).lower(), getattr(err, "status", None)
    if "agentic harness" in text or (status == 403 and "only available" in text):
        return 7 * 24 * 3600, "only for apps on OpenRouter's list"  # free, but not for purr
    if status == 401 or "invalid api key" in text or "incorrect api key" in text or "api key not valid" in text:
        return 24 * 3600, "the key doesn't work"
    if "per-day" in text or "per day" in text or "daily" in text or "perday" in text:
        return _to_midnight(), "out of free requests today"
    if "quota" in text or "resource_exhausted" in text or "resource exhausted" in text:
        return 3600, "out of free quota for now"
    if "model" in text and any(w in text for w in ("not found", "does not exist", "not exist", "unknown model",
                                                     "invalid model", "not supported", "no longer available",
                                                     "decommissioned", "deprecated")):
        return 3 * 24 * 3600, "isn't available (renamed or gone?)"
    if status == 429 or "rate limit" in text or "rate-limit" in text or "too many requests" in text:
        return 180, "rate-limited"
    if status in (404, 410) or "no endpoints" in text or "not a valid model" in text or "no allowed providers" in text:
        return 6 * 3600, "no host for it right now"
    if status == 402 or "credits" in text:
        return 1800, "wants credits"
    if status in (408, 502, 503, 504) or "overloaded" in text or "unavailable" in text:
        return 180, "busy"
    return None


class FreeRouter:
    """Picks among the free models whose provider has a key; rests the ones that are maxed out."""

    def __init__(self, config):
        self.config = config
        spec = config.get("free") or {}
        self.lists = {k: [n for n in spec.get(k, []) if n in config["models"]] for k in ("code", "ask", "talk")}
        try:
            self.resting = json.loads(FREE_FILE.read_text())
        except (OSError, ValueError):
            self.resting = {}
        self._keys = {}  # model -> whether its provider has a key (asked once)

    def candidates(self, mode):
        return self.lists.get(KIND.get(mode, "code")) or self.lists["code"]

    def pick(self, mode, need=0, exclude=()):
        """The best model for this mode that isn't resting and fits `need` tokens of chat, or None."""
        now = time.time()
        for name in self.candidates(mode):
            if name in exclude or self.is_resting(name, now) or not self.has_key(name):
                continue
            if need and self.config["models"][name].get("context", 0) < need * 1.15:
                continue
            return name
        return None

    def has_key(self, name):
        from .agent import provider_key  # here: agent imports this module
        provider = self.config["providers"][self.config["models"][name]["provider"]]
        if not provider.get("api_key_env"):
            return True
        if name not in self._keys:
            self._keys[name] = bool(provider_key(provider))
        return self._keys[name]

    def _rest_of(self, name):
        """The longer of the model's own rest and its provider's: {"until": ..., "why": ...} or {}."""
        provider = self.config["models"][name]["provider"]
        return max((self.resting.get(k, {}) for k in (name, f"provider:{provider}")), key=lambda r: r.get("until", 0))

    def is_resting(self, name, now=None):
        return self._rest_of(name).get("until", 0) > (now or time.time())

    def rest(self, name, seconds, why, scope="model"):
        key = f"provider:{self.config['models'][name]['provider']}" if scope == "provider" else name
        self.resting[key] = {"until": time.time() + seconds, "why": why}
        self._save()

    def reset(self):
        self.resting = {}
        self._save()

    def _save(self):
        try:
            FREE_FILE.parent.mkdir(parents=True, exist_ok=True)
            FREE_FILE.write_text(json.dumps(self.resting, indent=1))
        except OSError:
            pass

    def status(self, mode, current=None):
        """[(name, "ready" / "resting until 14:05: rate-limited")] in this mode's order."""
        now, out = time.time(), []
        for name in self.candidates(mode):
            provider, r = self.config["models"][name]["provider"], self._rest_of(name)
            if not self.has_key(name):
                out.append((name, f"no key (purr --key {provider})"))
            elif r.get("until", 0) > now:
                until = datetime.datetime.fromtimestamp(r["until"]).strftime("%a %H:%M" if r["until"] - now > 86400 else "%H:%M")
                out.append((name, f"resting until {until}: {r.get('why', '')}"))
            else:
                out.append((name, "in use" if name == current else "ready"))
        return out

    def next_free_at(self, mode):
        """When the first model for this mode is back, as HH:MM."""
        times = [self._rest_of(n).get("until", 0) for n in self.candidates(mode) if self.has_key(n)]
        return datetime.datetime.fromtimestamp(min(times)).strftime("%H:%M") if times else "?"


def check(config, router, say):
    """/free check: one tiny request (with purr's tools, like a real turn) to every ranked model whose
    provider has a key. What fails rests, so the router never spends a turn on it; what works is
    cleared. say(kind, line) shows each result as it comes."""
    from .agent import provider_key  # here: agent imports this module
    from .api import ApiError, stream_chat
    from .tools import schemas
    names = list(dict.fromkeys(n for kind in ("code", "ask", "talk") for n in router.lists[kind]))
    works = 0
    for name in names:
        model = config["models"][name]
        provider = config["providers"][model["provider"]]
        if not router.has_key(name):
            say("dim", f"  {name:<20} no key (purr --key {model['provider']})")
            continue
        shared = router.resting.get(f"provider:{model['provider']}", {})
        if shared.get("until", 0) > time.time():
            say("dim", f"  {name:<20} skipped: {model['provider']} is resting ({shared.get('why')})")
            continue
        body = {"model": model["id"], "messages": [{"role": "user", "content": "Reply with just: ok"}],
                "stream": True, "stream_options": {"include_usage": True}, "tools": schemas()}
        body.update(provider.get("body", {}))
        body.update(model.get("body", {}))
        body["max_tokens"] = min(body.get("max_tokens") or 300, 300)
        start = time.time()
        try:
            stream_chat(provider["base_url"], provider_key(provider), body, lambda s: None, lambda s: None,
                        lambda: False)
        except ApiError as e:
            rest = rest_for(e) or (3600, f"refused: {str(e)[:80]}", "model")
            router.rest(name, *rest)
            say("warn", f"✗ {name:<20} {rest[1]}")
            continue
        router.resting.pop(name, None)
        router._save()
        works += 1
        say("info", f"♡ {name:<20} works  {time.time() - start:.1f}s")
    say("info", f"{works} free model{'s' * (works != 1)} ready")
