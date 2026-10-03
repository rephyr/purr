"""What purr setup does, without the window (tui/setup.py shows it): what's running on this machine,
which API providers have a key already, checking and saving a key, adding your own provider, making
roomier copies of Ollama models, and saving your choices. Plain Python, no packages.
"""

import json
import os
import re
import urllib.error
import urllib.request

from harness import agent, discover, settings

AGENT_CONTEXT = 32768  # less and an agent's chat (instructions, tools, files) barely fits


def scan():
    """purr's settings with what's running right now: (config, {provider: [model names found]})."""
    config = settings.load(discover=False)
    found = discover.find(config.get("providers", {}))
    discover.add_found(config, found)
    by_server = {}
    for name, spec in config["models"].items():
        if spec.get("found"):
            by_server.setdefault(spec["provider"], []).append(name)
    return config, by_server


def too_small(config):
    """Ollama models found with a context too small for an agent (a model without its own num_ctx
    gets Ollama's default, often 4096): they can get a roomier copy."""
    return [n for n, m in config["models"].items()
            if m.get("found") and m["provider"] == "ollama" and m.get("context", 0) < AGENT_CONTEXT]


def make_roomy(config, name, context=AGENT_CONTEXT):
    """A copy of an Ollama model with a bigger context: only its settings, no weights copied.
    Returns the new model's Ollama name; raises OSError when Ollama says no."""
    spec = config["models"][name]
    base = discover.root_of(config["providers"]["ollama"]["base_url"])
    new = f"{re.sub(r'[^a-z0-9._-]+', '-', spec['id'].split('/')[-1].lower()).strip('-')}-{context // 1024}k"
    discover._get(f"{base}/api/create", {"model": new, "from": spec["id"], "parameters": {"num_ctx": context},
                                         "stream": False}, timeout=120)
    return new


# ---- API providers ----

def key_source(provider):
    """Where this provider's key comes from: "environment", "saved", "OpenCode", or None."""
    env = provider.get("api_key_env")
    if not env:
        return None
    if os.environ.get(env):
        return "environment"
    if agent.saved_key(env):
        return "saved"
    if provider.get("opencode_auth") and agent.opencode_key(provider["opencode_auth"]):
        return "OpenCode"
    return None


def api_providers(config):
    """[(name, provider)] that need a key, the paid ones first, then the free tiers."""
    items = [(n, p) for n, p in config.get("providers", {}).items() if p.get("api_key_env")]
    return sorted(items, key=lambda item: bool(item[1].get("key_page")))


def check_key(provider, key, timeout=15):
    """(ok, what the provider said) for one cheap request with the key."""
    base = provider["base_url"].rstrip("/")
    # OpenRouter lists its models without a key: its /key page is the one that checks it
    url = f"{base}/key" if "openrouter.ai" in base else f"{base}/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True, "works ♡"
    except urllib.error.HTTPError as e:
        return False, {401: "the key was refused (401)", 403: "the key isn't allowed (403)"}.get(e.code, f"error {e.code}")
    except (OSError, ValueError) as e:
        return False, f"can't reach it: {getattr(e, 'reason', e)}"


def store_key(env_name, key):
    """Into ~/.config/purr/keys.toml, readable only by you (in the chat it would end up in the logs)."""
    import tomllib
    path = agent.KEYS_FILE
    try:
        keys = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        keys = {}
    keys[env_name] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.chmod(0o600)
    # quoted the TOML way (json.dumps escapes " and \\ the same): one odd character used to make the
    # whole file unreadable, and every saved key silently vanished with it
    path.write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in keys.items()))


def env_name(name):
    """"my work" -> "MY_WORK_API_KEY"."""
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_") + "_API_KEY"


def provider_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "mine"


def list_models(base_url, key=None, timeout=15):
    """[(model id, context or None)] from an OpenAI-compatible /models. Raises OSError/ValueError."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read()).get("data") or []
    out = []
    for m in data:
        if m.get("id") and "embed" not in m["id"].lower():
            ctx = m.get("context_length") or m.get("max_model_len") or (m.get("top_provider") or {}).get("context_length")
            out.append((m["id"], int(ctx) if ctx else None))
    return out


def custom_provider(name, base_url, key=None, models=(), context=131072):
    """The settings for your own OpenAI-compatible provider and the models you picked from it:
    {"providers": {...}, "models": {...}}, ready for settings.save_user (the key goes to keys.toml)."""
    slug = provider_slug(name)
    provider = {"base_url": base_url.rstrip("/")}
    if key:
        provider["api_key_env"] = env_name(slug)
        store_key(provider["api_key_env"], key)
    picked = {}
    for model_id, ctx in models:
        short = model_id.split("/")[-1].split(":")[0]
        picked[short if short not in picked else model_id] = {"provider": slug, "id": model_id,
                                                             "context": ctx or context}
    return {"providers": {slug: provider}, "models": picked}


# ---- the model to start with ----

def recommend(config):
    """The model to start on: a local one with room for an agent (free, private), else the first API
    model you have a key for, else None."""
    models = config["models"]
    local = sorted((n for n, m in models.items() if m.get("found") and m.get("context", 0) >= AGENT_CONTEXT),
                   key=lambda n: -models[n]["context"])
    if local:
        return local[0]
    return next((n for n, m in models.items() if not m.get("router") and not m.get("found")
                 and settings.usable(config, n)), None)


def usable_models(config):
    """[(name, spec)] that can run now: found locally (roomy enough: the roomiest first), then the
    API models with a key."""
    out = []
    for name, spec in config["models"].items():
        if spec.get("router"):
            continue
        if spec.get("found"):
            if spec.get("context", 0) >= AGENT_CONTEXT:
                out.append((name, spec))
        elif settings.usable(config, name):
            out.append((name, spec))
    return sorted(out, key=lambda item: (not item[1].get("found"), -item[1].get("context", 0) if item[1].get("found") else 0))


def finish(default_model=None, cat_name=None, theme=None, extra=None):
    """Save your choices: the model to start on and anything else in your config, the cat's name
    (where /cat name keeps it too) and the colours."""
    changes = dict(extra or {})
    if default_model:
        changes["default_model"] = default_model
    if theme:
        changes["theme"] = theme
    settings.save_user(changes)
    if cat_name:
        pet_file = agent.STATE_DIR / "cat.json"
        try:
            pet = json.loads(pet_file.read_text())
        except (OSError, ValueError):
            pet = {}
        pet["name"] = cat_name
        pet_file.parent.mkdir(parents=True, exist_ok=True)
        pet_file.write_text(json.dumps(pet, indent=1))
    if theme:
        (agent.STATE_DIR / "theme").write_text(theme)
