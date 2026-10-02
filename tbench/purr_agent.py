"""purr as a Harbor agent, so it can run real benchmarks (Terminal-Bench) next to OpenCode,
Claude Code, Aider and the rest.

    tbench/run.sh -t <task>                      one task (see run.sh for the rest)

Each task's container gets uv and Python 3.12 (task images often have an older Python, and
purr needs 3.11+), purr's own files (plain mode only: no TUI), and a config.toml made for the
model Harbor picks: `-m openrouter/deepseek/deepseek-v4.1-flash` becomes purr's provider
"openrouter", model id "deepseek/deepseek-v4.1-flash", with the provider settings from purr's
own config.toml. Then purr does the task in one go (`purr -p ... --yes`, plain mode).

Agent kwargs (harbor run --ak key=value):
    mcp=false      leave purr's MCP servers (servers/) out, to measure what they're worth
"""

import json
import shlex
import shutil
import tempfile
import tomllib
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

PURR = Path(__file__).resolve().parent.parent
REMOTE = "/opt/purr"
PY = f"{REMOTE}/py"  # uv's Python 3.12, linked here when installing
SHIPPED = ("purr.py", "harness", "servers")  # all plain mode needs


def _key(k):
    return k if k.replace("_", "").replace("-", "").isalnum() else json.dumps(k)


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):  # only inside lists: tables get their own [header]
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def to_toml(data):
    """purr's config as TOML: every nested dict as its own [a.b.c] table, its plain values first."""
    out = []

    def emit(name, table):
        if name:
            out.append(f"\n[{'.'.join(_key(p) for p in name)}]")
        out.extend(f"{_key(k)} = {_value(v)}" for k, v in table.items() if not isinstance(v, dict))
        for k, v in table.items():
            if isinstance(v, dict):
                emit(name + (k,), v)
    emit((), data)
    return "\n".join(out).strip() + "\n"


class PurrAgent(BaseInstalledAgent):
    """purr, installed into the task's container and run once in plain mode."""

    MODEL_CONNECTION = ModelConnectionSpec(passthrough=True)

    def __init__(self, *args, mcp: str | bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_mcp = str(mcp).lower() not in ("false", "0", "no", "off")

    @staticmethod
    def name() -> str:
        return "purr"

    def get_version_command(self) -> str | None:
        return None

    # ---- the config purr gets in the container ----

    def purr_config(self):
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("model must be provider/model, like openrouter/deepseek/deepseek-v4.1-flash")
        provider, model_id = self.model_name.split("/", 1)
        mine = tomllib.loads((PURR / "config.toml").read_text())
        if provider not in mine["providers"]:
            raise ValueError(f"purr has no provider {provider!r} (has: {', '.join(mine['providers'])})")
        # the same model's settings from purr's config if it has it (context, prices, body)
        known = next((dict(spec) for spec in mine["models"].values()
                      if spec.get("provider") == provider and spec.get("id") == model_id), {})
        known.pop("router", None)
        spec = {"context": 131072, **known, "provider": provider, "id": model_id}
        prov = {k: v for k, v in mine["providers"][provider].items() if k != "opencode_auth"}
        config = {k: v for k, v in mine.items()
                  if k not in ("models", "providers", "mode_models", "plan", "free", "mcp", "default_model")}
        config.update({"default_model": "bench", "max_steps": 200, "providers": {provider: prov},
                       "models": {"bench": spec}})
        if self.use_mcp:
            config["mcp"] = {name: {"command": [PY, f"{{purr}}/servers/{name}.py"]}
                             for name in ("codebase", "stack")}
        return config

    # ---- install and run ----

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "ca_certificates", "ripgrep"))
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "purr"
            pack.mkdir()
            for item in SHIPPED:
                src = PURR / item
                if src.is_dir():
                    shutil.copytree(src, pack / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(src, pack / item)
            (pack / "config.toml").write_text(to_toml(self.purr_config()))
            await self.exec_as_root(environment, command=f"mkdir -p {REMOTE}")
            await environment.upload_dir(pack, REMOTE)
        await self.exec_as_root(environment, command=(
            "set -euo pipefail; export UV_INSTALL_DIR=/opt/uv UV_PYTHON_INSTALL_DIR=/opt/uv/python; "
            "[ -x /opt/uv/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh; "
            "/opt/uv/uv python install 3.12 >/dev/null; "
            f"ln -sf \"$(/opt/uv/uv python find 3.12)\" {PY}; chmod -R a+rX {REMOTE} /opt/uv; "
            f"{PY} -c 'import sys, tomllib; print(sys.version)'"))

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        access = self.model_connection
        env = dict(access.env)
        provider = self.model_name.split("/", 1)[0]
        key_env = tomllib.loads((PURR / "config.toml").read_text())["providers"][provider].get("api_key_env")
        if key_env and access.api_key:
            env[key_env] = access.api_key
        env["PURR_STATE"] = "/logs/agent/purr-state"  # its chats land in the trial's logs
        await self.exec_as_agent(environment, command=(
            f"{PY} {REMOTE}/purr.py \"$PWD\" --plain --yes -p {shlex.quote(instruction)} "
            "2>&1 </dev/null | stdbuf -oL tee /logs/agent/purr.txt"), env=env)

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Tokens and cost from purr's saved chat (in the trial's logs)."""
        cost = out = 0
        for f in (self.logs_dir / "purr-state" / "sessions").glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            cost += data.get("cost") or 0
            out += data.get("out") or 0
        context.cost_usd = round(cost, 6) if cost else None
        context.n_output_tokens = out or None
