"""purr as a Harbor agent, so it can run real benchmarks (Terminal-Bench) next to OpenCode,
Claude Code, Aider and the rest.

    tbench/run.sh -t <task>                      one task (see run.sh for the rest)

Each task's container gets uv and Python 3.12 (task images often have an older Python, and
purr needs 3.11+), purr's own files (plain mode only: no TUI), and a config.toml made for the
model Harbor picks: `-m openrouter/deepseek/deepseek-v4.1-flash` becomes purr's provider
"openrouter", model id "deepseek/deepseek-v4.1-flash", with the provider settings from purr's
own config.toml. Then purr does the task in one go (`purr -p ... --yes`, plain mode).

Agent kwargs (harbor run --ak key=value), all optional; tbench/fair.sh sets the fair ones:
    mcp=false             leave purr's MCP servers (servers/) out, to measure what they're worth
    hosts=deepseek        OpenRouter: only these hosts serve the model, no fallback to others
    temperature=1.0       sampling, sent with every request
    top_p=0.95
    max_tokens=65536      the most a reply may write
    max_steps=500         model calls per turn before purr would stop
    edge_cases=true       the final check also tries edge cases (purr leaves it off for API models)
    time_limit=900        seconds purr is told it has (default: the task's own limit, less 10%)
"""


import asyncio
import json
import shlex
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # purr's own code, for its version
from harness import full_version as purr_version  # noqa: E402

PURR = Path(__file__).resolve().parent.parent
REMOTE = "/opt/purr"
# a model on this machine (Ollama): from inside the container it's this machine's address on the
# container's network (its gateway), through tbench/ollama_bridge.py; filled in when installing
GATEWAY = "HOST_GATEWAY"
BRIDGE_PORT = 11435
LOCAL_HOSTS = ("127.0.0.1", "localhost")
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

    def __init__(self, *args, mcp: str | bool = True, hosts: str | None = None, temperature=None,
                 top_p=None, max_tokens=None, max_steps=200, edge_cases=None, time_limit=None, **kwargs):
        kwargs.setdefault("version", purr_version())
        super().__init__(*args, **kwargs)
        self.use_mcp = str(mcp).lower() not in ("false", "0", "no", "off")
        self.hosts = [h.strip() for h in str(hosts).split(",") if h.strip()] if hosts else []
        self.sampling = {k: float(v) for k, v in (("temperature", temperature), ("top_p", top_p)) if v is not None}
        self.max_tokens = int(max_tokens) if max_tokens else None
        self.max_steps = int(max_steps)
        self.edge_cases = None if edge_cases is None else str(edge_cases).lower() in ("true", "1", "yes", "on")
        self.time_limit = float(time_limit) if time_limit else None

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
        body = dict(spec.get("body") or {}, **self.sampling)
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        if body:
            spec["body"] = body
        prov = {k: v for k, v in mine["providers"][provider].items() if k != "opencode_auth"}
        if any(f"//{h}:" in prov.get("base_url", "") for h in LOCAL_HOSTS):
            prov["base_url"] = f"http://{GATEWAY}:{BRIDGE_PORT}/v1"
        if self.hosts:  # pinned hosts replace purr's usual "cheapest of these" routing, with no fallback
            prov["body"] = {"provider": {"only": self.hosts, "allow_fallbacks": False}}
        config = {k: v for k, v in mine.items()
                  if k not in ("models", "providers", "mode_models", "plan", "free", "mcp", "default_model")}
        if self.edge_cases is not None:
            config["edge_cases"] = self.edge_cases
        config.update({"default_model": "bench", "max_steps": self.max_steps, "providers": {provider: prov},
                       "models": {"bench": spec}})
        if self.use_mcp:
            config["mcp"] = {name: {"command": [PY, f"{{purr}}/servers/{name}.py"]}
                             for name in ("codebase", "stack")}
        return config

    # ---- install and run ----

    async def install_packages(self, environment, packages, tries=3, pause=20):
        """ensure_system_dependencies, tried again after a pause: a package mirror hiccup once
        failed a whole task before purr even started (qemu-alpine-ssh, apt exit 100)."""
        for attempt in range(tries):
            try:
                return await self.ensure_system_dependencies(environment, packages)
            except Exception:  # noqa: BLE001 - Harbor raises its own error types for a failed command
                if attempt == tries - 1:
                    raise
                self.logger.warning("installing %s failed, trying again in %ss", ", ".join(packages), pause)
                await asyncio.sleep(pause)

    async def missing_packages(self, environment):
        """Only what the container lacks. Harbor's helper otherwise reinstalls everything, which
        upgrades the image's own curl: on an old Debian image the mirror no longer had those
        versions (404) and the task failed before purr started (qemu-alpine-ssh, three times)."""
        checks = {"curl": "command -v curl || /opt/uv/uv --version", "ripgrep": "command -v rg",
                  "tmux": "command -v tmux", "ca_certificates": "[ -s /etc/ssl/certs/ca-certificates.crt ]"}
        missing = []
        for package, check in checks.items():
            result = await self.exec_as_root(environment, command=f"{check} >/dev/null 2>&1 && echo yes || echo no")
            if "yes" not in (getattr(result, "stdout", None) or ""):
                missing.append(package)
        return tuple(missing)

    async def upload_uv(self, environment):
        """This machine's uv, copied in: then the container needs no curl to fetch it. curl was the
        package that failed (qemu-alpine-ssh's old Debian mirror no longer has its version). uv is
        a glibc build, so on a musl image (Alpine) it doesn't run and curl fetches one as before."""
        uv = shutil.which("uv")
        if not uv:
            return
        try:
            await self.exec_as_root(environment, command="mkdir -p /opt/uv")
            # the file itself: uv is often a symlink (pipx, ~/.local/bin), and docker cp copies the link
            await environment.upload_file(Path(uv).resolve(), "/opt/uv/uv")
            await self.exec_as_root(environment, command="chmod 755 /opt/uv/uv; /opt/uv/uv --version >/dev/null 2>&1 "
                                                         "|| rm -f /opt/uv/uv")
        except Exception:  # noqa: BLE001 - then the curl way below
            self.logger.warning("copying uv into the container failed: fetching it with curl")

    async def install(self, environment: BaseEnvironment) -> None:
        await self.upload_uv(environment)
        missing = await self.missing_packages(environment)
        try:
            await self.install_packages(environment, missing)
        except Exception:  # noqa: BLE001 - Harbor raises its own error types for a failed command
            # tmux is only nice to have (purr's terminal falls back to its own sessions without it)
            needed = tuple(p for p in missing if p != "tmux")
            if needed == missing:
                raise
            self.logger.warning("installing tmux failed: purr's terminal sessions run without it")
            await self.install_packages(environment, needed)
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "purr"
            pack.mkdir()
            for item in SHIPPED:
                src = PURR / item
                if src.is_dir():
                    shutil.copytree(src, pack / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(src, pack / item)
            config = to_toml(self.purr_config())
            (pack / "config.toml").write_text(config)
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "purr-config.toml").write_text(config)  # what this trial ran with, for the record
            await self.exec_as_root(environment, command=f"mkdir -p {REMOTE}")
            await environment.upload_dir(pack, REMOTE)
        await self.exec_as_root(environment, command=(
            "set -euo pipefail; export UV_INSTALL_DIR=/opt/uv UV_PYTHON_INSTALL_DIR=/opt/uv/python; "
            "[ -x /opt/uv/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh; "
            "/opt/uv/uv python install 3.12 >/dev/null; "
            f"ln -sf \"$(/opt/uv/uv python find 3.12)\" {PY}; chmod -R a+rX {REMOTE} /opt/uv; "
            f"{PY} -c 'import sys, tomllib; print(sys.version)'; "
            # the gateway of the container's network is this machine (for a local model's bridge)
            f"GW=$({PY} -c 'import socket, struct; print(next(socket.inet_ntoa(struct.pack(\"<L\", int(f[2], 16))) "
            f"for f in (l.split() for l in open(\"/proc/net/route\")) if f[1] == \"00000000\"))'); "
            f"sed -i \"s/{GATEWAY}/$GW/\" {REMOTE}/config.toml"))

    def task_time_limit(self):
        """The task's own agent time limit in seconds (its task.toml, times the job's multiplier), less
        10% so purr wraps up before it's stopped. None if it can't be found: then no reminders."""
        if self.time_limit:
            return self.time_limit
        try:
            trial = json.loads((self.logs_dir.parent / "config.json").read_text())
            job = json.loads((self.logs_dir.parent.parent / "config.json").read_text())
            task = trial["task"]
            if task.get("path"):  # a registry dataset (terminal-bench@2.0): tasks/<id>/<name>/
                found = Path.home().glob(f".cache/harbor/tasks/*/{Path(task['path']).name}/task.toml")
            else:  # a package (Terminal-Bench 2.1): tasks/packages/<org>/<name>/<sha>/
                sha = str(task.get("ref") or "").split(":")[-1] or "*"
                found = Path.home().glob(f".cache/harbor/tasks/packages/{task['name']}/{sha}/task.toml")
            found = sorted(found, key=lambda p: p.stat().st_mtime)
            seconds = tomllib.loads(found[-1].read_text())["agent"]["timeout_sec"]
            return 0.9 * seconds * float(job.get("agent_timeout_multiplier") or 1.0)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return None

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        access = self.model_connection
        env = dict(access.env)
        provider = self.model_name.split("/", 1)[0]
        key_env = tomllib.loads((PURR / "config.toml").read_text())["providers"][provider].get("api_key_env")
        if key_env and access.api_key:
            env[key_env] = access.api_key
        env["PURR_STATE"] = "/logs/agent/purr-state"  # its chats land in the trial's logs
        limit = self.task_time_limit()
        timed = f"--time-limit {int(limit)} " if limit else ""
        await self.exec_as_agent(environment, command=(
            f"{PY} {REMOTE}/purr.py \"$PWD\" --plain --yes {timed}-p {shlex.quote(instruction)} "
            "2>&1 </dev/null | stdbuf -oL tee /logs/agent/purr.txt"), env=env)

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Tokens and cost from purr's saved chat (in the trial's logs)."""
        cost = out = tokens_in = cached = 0
        for f in (self.logs_dir / "purr-state" / "sessions").glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            cost += data.get("cost") or 0
            out += data.get("out") or 0
            tokens_in += data.get("in") or 0
            cached += data.get("cached") or 0
        context.cost_usd = round(cost, 6) if cost else None
        context.n_output_tokens = out or None
        context.n_input_tokens = tokens_in or None
        context.n_cache_tokens = cached or None
