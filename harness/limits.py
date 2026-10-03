"""Per-model limits.

purr runs both a 32k local model and a 1M API model, and the same numbers can't
suit both: a tool result that is a few percent of a big model's context is most
of a small model's, and a small model has to compact sooner so a reply still
fits afterwards.

`Limits.for_model` turns the `context` in config.toml into a set of numbers.
Any model can override one with a `limits = { ... }` table in config.toml.
"""

import dataclasses
from dataclasses import dataclass


LOCAL = ("ollama", "llamacpp")  # providers that run on your own GPU


def _clamp(n, low, high):
    return max(low, min(high, int(n)))


def _compact_at(context):
    """Compact earlier on a small context: it needs room left for the reply."""
    if context <= 40_000:
        return 0.72
    if context <= 150_000:
        return 0.78
    if context <= 400_000:
        return 0.82
    return 0.85  # the old, model-independent default


@dataclass(frozen=True)
class Limits:
    context: int
    tool_output: int        # characters one tool result may send to the model
    read_lines: int         # lines read_file returns by default
    list_files: int         # most files list_files returns
    grep_matches: int       # most lines grep returns
    attach_max: int         # characters an @file attachment may add
    compact_at: float       # compact when this fraction of the context is used
    prune_at: float         # elide old tool output when this fraction is used
    keep_recent_tools: int  # this many recent tool results stay complete
    repeat_limit: int       # identical tool calls before the turn is stopped
    hidden_tools: tuple = ()  # tools this model doesn't get offered
    # purr's training wheels: "full" for local models (auto-refine, checkpoints, step backs, edge cases in the
    # final check), "light" for API models, which bench showed don't need them (just slower)
    helpers: str = "full"

    @classmethod
    def for_model(cls, model):
        context = _clamp(model.get("context") or 32768, 1000, 100_000_000)
        # Roughly 4 characters per token, and no single tool result should be a
        # large chunk of the context; a little under 5% is a good ceiling.
        base = cls(
            context=context,
            tool_output=_clamp(context * 0.2, 4000, 24000),
            read_lines=_clamp(context / 125, 150, 1000),
            list_files=_clamp(context / 333, 80, 400),
            grep_matches=_clamp(context / 375, 40, 250),
            attach_max=_clamp(context * 0.4, 8000, 40000),
            compact_at=_compact_at(context),
            prune_at=max(0.5, _compact_at(context) - 0.12),
            keep_recent_tools=_clamp(context / 12000, 3, 12),
            repeat_limit=3 if context <= 100_000 else 4,
            # a small local model does better with fewer choices: web pages eat its context,
            # and a helper is just the same small model with an empty memory
            hidden_tools=("fetch_url", "task") if context <= 65_536 else (),
            helpers="full" if model.get("provider") in LOCAL or context <= 65_536 else "light",
        )
        overrides = model.get("limits") or {}
        known = {f.name for f in dataclasses.fields(cls)}
        overrides = {k: v for k, v in overrides.items() if k in known}
        if "hidden_tools" in overrides:
            overrides["hidden_tools"] = tuple(overrides["hidden_tools"])
        return dataclasses.replace(base, **overrides)
