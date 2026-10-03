"""Local model servers running on this machine, found without any setup: Ollama, llama.cpp's
llama-server, LM Studio and vLLM, at the address their provider has in the settings (their usual
port unless you changed it). Each model they serve becomes a purr model, unless you configured the
same one yourself.

Ollama tells what each model can do (/api/show): one that can't call tools (an embedding model, a
base model) can't work as an agent, so it's left out. Those answers are cached by the model's
digest, so starting purr stays quick with dozens of models. Plain Python, no packages.
"""

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "purr" / "ollama-models.json"
TIMEOUT = 0.6  # seconds: a server that isn't running refuses at once; this only bounds a hung one
DEFAULT_CONTEXT = 8192  # when a server doesn't say: small, so purr's limits stay safe


def _get(url, data=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def root_of(base_url):
    """http://127.0.0.1:11434/v1 -> http://127.0.0.1:11434"""
    url = base_url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


# ---- Ollama ----

def _ollama_default_context():
    """What Ollama gives a model without its own num_ctx: OLLAMA_CONTEXT_LENGTH, else 4096 (older)
    or 16384 (since 0.12)."""
    env = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
    return int(env) if env.isdigit() else 4096


def _ollama_show(root, name):
    """{"tools": bool, "vision": bool, "context": int} for one model, from /api/show."""
    info = _get(f"{root}/api/show", {"model": name}, timeout=5)
    caps = info.get("capabilities") or []
    context = None
    for line in (info.get("parameters") or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            context = int(parts[1])
    if context is None:  # no num_ctx of its own: what Ollama runs it with, capped by what it supports
        trained = next((v for k, v in (info.get("model_info") or {}).items() if k.endswith(".context_length")), None)
        context = min(_ollama_default_context(), trained) if isinstance(trained, int) else _ollama_default_context()
    # older Ollama versions don't list capabilities: assume it can (the chat says so if not)
    return {"tools": "tools" in caps if caps else True, "vision": "vision" in caps, "context": context}


def _load_cache():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def ollama(base_url):
    """[(model id, {"context", "vision"})] for Ollama's models that can call tools."""
    root = root_of(base_url)
    tags = _get(f"{root}/api/tags").get("models") or []
    cache, changed = _load_cache(), False
    todo = [m for m in tags if m.get("digest") not in cache]
    if todo:
        def show(m):
            try:
                return m["digest"], _ollama_show(root, m["name"])
            except (OSError, ValueError, KeyError):
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            for got in pool.map(show, todo):
                if got:
                    cache[got[0]] = got[1]
                    changed = True
    if changed:
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache))
        except OSError:
            pass
    found = []
    for m in tags:
        info = cache.get(m.get("digest"))
        if info and info["tools"]:
            found.append((m["name"].removesuffix(":latest"), {"context": info["context"], "vision": info["vision"]}))
    return found


# ---- OpenAI-compatible servers: llama.cpp, LM Studio, vLLM ----

def openai_style(base_url):
    """[(model id, {"context"})] from /v1/models, with the context where the server tells it."""
    root = root_of(base_url)
    data = _get(f"{root}/v1/models").get("data") or []
    n_ctx = None
    try:  # llama.cpp's server: the context it was started with
        n_ctx = (_get(f"{root}/props").get("default_generation_settings") or {}).get("n_ctx")
    except (OSError, ValueError):
        pass
    found = []
    for m in data:
        if not m.get("id") or "embed" in m["id"].lower():
            continue
        context = m.get("max_model_len") or m.get("context_length") or n_ctx or DEFAULT_CONTEXT  # vLLM, LM Studio
        found.append((m["id"], {"context": int(context)}))
    return found


def find(providers):
    """{provider name: [(model id, info)]} for every provider with `discover` set that answers."""
    wanted = {name: p for name, p in providers.items() if p.get("discover") and p.get("base_url")}

    def ask(item):
        name, p = item
        try:
            return name, (ollama if p["discover"] == "ollama" else openai_style)(p["base_url"])
        except (OSError, ValueError, KeyError, TypeError):
            return name, None
    with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as pool:
        return {name: models for name, models in pool.map(ask, wanted.items()) if models}


def add_found(config, found=None):
    """Put the found models into config["models"], skipping ones you configured yourself (same
    provider and id). Returns the names added."""
    found = find(config.get("providers", {})) if found is None else found
    models = config.setdefault("models", {})
    taken = {(spec.get("provider"), str(spec.get("id", "")).removesuffix(":latest")) for spec in models.values()}
    added = []
    for provider, items in found.items():
        for model_id, info in items:
            if (provider, model_id.removesuffix(":latest")) in taken:
                continue
            name = model_id if model_id not in models else f"{model_id}@{provider}"
            models[name] = {"provider": provider, "id": model_id, "context": info["context"], "found": True,
                            **({"vision": True} if info.get("vision") else {})}
            added.append(name)
    return added
