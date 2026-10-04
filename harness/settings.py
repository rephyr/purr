"""purr's settings, in layers, each winning over the one before (tables merge key by key):

1. harness/defaults.toml: what purr ships (the API providers and their models, how purr behaves)
2. ~/.config/purr/config.toml: yours (`purr setup` writes it: your default model, providers you
   added, models you tuned)
3. the local models running right now (Ollama, llama.cpp, LM Studio, vLLM: harness/discover.py),
   unless you configured the same model yourself

PURR_CONFIG=<file> uses exactly that file instead (the benchmark containers, which get a config
made for the task). Plain Python, no packages: the benchmark adapter reads settings too.
"""

import json
import os
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULTS = HERE / "defaults.toml"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "purr"
USER_CONFIG = CONFIG_DIR / "config.toml"


def merge(base, over):
    """over into base, in place: tables merge key by key, anything else is replaced."""
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def user_config():
    """Your own settings, {} when there are none yet (or the file can't be read)."""
    try:
        return tomllib.loads(USER_CONFIG.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SystemExit(f"purr: can't read {USER_CONFIG}: {e}") from None


def is_set_up():
    """purr setup has run (or you wrote your own config): no first-start setup then."""
    return USER_CONFIG.exists() or bool(os.environ.get("PURR_CONFIG"))


def load(discover=True):
    """The settings purr runs with. discover: also ask the local servers for their models."""
    path = os.environ.get("PURR_CONFIG")
    if path:
        return tomllib.loads(Path(path).read_text())
    mine = user_config()
    config = merge(tomllib.loads(DEFAULTS.read_text()), mine)
    host = ollama_host(os.environ.get("OLLAMA_HOST", ""))
    if host and "base_url" not in (mine.get("providers") or {}).get("ollama", {}):
        config["providers"]["ollama"]["base_url"] = host + "/v1"  # Ollama elsewhere: WSL, another machine
    if discover:
        from harness.discover import add_found
        add_found(config)
    if config.get("default_model") not in config.get("models", {}):
        config["default_model"] = pick_default(config)
    return config


def ollama_host(value):
    """OLLAMA_HOST as Ollama's own CLI reads it ("host", "host:port", "http://host:port") -> its URL,
    or "" when unset. 0.0.0.0 (what a server listens on) means this machine."""
    from urllib.parse import urlsplit
    value = value.strip().rstrip("/")
    if not value:
        return ""
    scheme = value.split("://")[0] if "://" in value else ""
    rest = value.split("://", 1)[-1]
    if rest.count(":") > 1 and not rest.startswith("["):
        rest = f"[{rest}]"  # a bare IPv6 address
    parts = urlsplit("//" + rest)
    host = parts.hostname or ""
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else f"[{host}]" if ":" in host else host
    port = parts.port or {"http": 80, "https": 443}.get(scheme, 11434)  # a scheme given: its usual port, as Ollama does
    return f"{scheme or 'http'}://{host}:{port}{parts.path}"


def usable(config, name):
    """The model can run now: a local one, or one whose provider has its key."""
    from harness.agent import provider_key
    spec = config["models"].get(name) or {}
    provider = config.get("providers", {}).get(spec.get("provider"), {})
    return bool(spec) and (not provider.get("api_key_env") or bool(provider_key(provider)))


def pick_default(config):
    """A model to start with when none is set (or the set one is gone): the roomiest local model
    found, else the first API model with a key, else purr's first model (it then says what's missing)."""
    models = config.get("models", {})
    found = sorted((n for n, m in models.items() if m.get("found")), key=lambda n: -models[n].get("context", 0))
    if found:
        return found[0]
    for name, spec in models.items():
        if not spec.get("router") and usable(config, name):
            return name
    return next(iter(models), "")


# ---- writing your config (purr setup, /provider add) ----

def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)  # TOML basic strings escape the same way
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def _key(k):
    return k if k.replace("_", "").replace("-", "").isalnum() else json.dumps(k)


def to_toml(config, head=""):
    """A dict as TOML: plain values first, then a [table] for each table (two levels: [models.x])."""
    out = [head] if head else []
    plain = {k: v for k, v in config.items() if not isinstance(v, dict)}
    out += [f"{_key(k)} = {_toml_value(v)}" for k, v in plain.items()]
    for k, v in config.items():
        if not isinstance(v, dict):
            continue
        flat = {kk: vv for kk, vv in v.items() if not isinstance(vv, dict)}
        if flat or not v:
            out += ["", f"[{_key(k)}]"] + [f"{_key(kk)} = {_toml_value(vv)}" for kk, vv in flat.items()]
        for kk, vv in v.items():
            if isinstance(vv, dict):
                out += ["", f"[{_key(k)}.{_key(kk)}]"] + [f"{_key(a)} = {_toml_value(b)}" for a, b in vv.items()]
    return "\n".join(out).strip() + "\n"


def save_user(changes):
    """Merge changes into your config.toml and write it. A file you wrote by hand keeps its comments
    as long as nothing in it changes; otherwise it's rewritten (the old one is kept as config.toml.bak)."""
    current = user_config()
    merged = merge(json.loads(json.dumps(current)), changes)
    if merged == current and USER_CONFIG.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if USER_CONFIG.exists():
        USER_CONFIG.with_suffix(".toml.bak").write_text(USER_CONFIG.read_text())
    USER_CONFIG.write_text(to_toml(merged, "# Your purr settings: anything here wins over purr's own "
                                            "(harness/defaults.toml).\n# `purr setup` writes this file; "
                                            "change it by hand as much as you like.\n"))
