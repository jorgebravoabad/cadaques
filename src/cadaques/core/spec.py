"""The :class:`CampaignSpec`: the declarative definition of a campaign.

The event log records what *happened*; the spec declares what was
*asked* (kernel object twelve, Amendment A1 of the three-year
strategy). A spec is what benchmarks cite, what replay will consume,
and what a construction agent will one day compile to.

Serialization strategy: shipped participants are plain dataclasses
with JSON-able fields, so a component spec is ``{"class": dotted
path, "params": asdict(obj)}``. Participants that hold callables
(e.g. :class:`~cadaques.oracles.analytic.AnalyticOracle`) are not
spec-serializable, and :func:`component_spec` says so loudly rather
than pickling code — a spec must stay a *declaration*, portable and
auditable, never a binary blob.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .cost import Budget, Cost
from .task import SearchSpace, Task

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime.campaign import Campaign

#: Schema identifier carried by every serialized spec.
SPEC_SCHEMA: str = "cadaques.spec/1"


class SpecError(ValueError):
    """A campaign (or one of its participants) is not spec-representable."""


# ---------------------------------------------------------------- components
def component_spec(obj: Any) -> dict[str, Any]:
    """Describe a participant as ``{"class": ..., "params": ...}``.

    Requires a dataclass whose fields are JSON-serializable; anything
    else (callables, open handles) raises :class:`SpecError` with the
    offending field named.
    """
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise SpecError(
            f"{type(obj).__name__} is not a dataclass instance; "
            "implement a dataclass participant or construct it in code "
            "instead of from a spec."
        )
    params = dataclasses.asdict(obj)
    for name, value in params.items():
        try:
            json.dumps(value)
        except TypeError as exc:
            raise SpecError(
                f"{type(obj).__name__}.{name} is not JSON-serializable "
                f"({type(value).__name__}); this participant cannot be "
                "declared in a spec."
            ) from exc
    # Canonicalize to pure JSON types (tuples -> lists, etc.) so a spec
    # is identical before and after serialization.
    params = json.loads(json.dumps(params))
    cls = type(obj)
    return {"class": f"{cls.__module__}.{cls.__qualname__}", "params": params}


def build_component(spec: Mapping[str, Any]) -> Any:
    """Instantiate a participant from its component spec."""
    dotted = spec["class"]
    module_name, _, cls_name = dotted.rpartition(".")
    cls = getattr(importlib.import_module(module_name), cls_name)
    return cls(**spec["params"])


# ---------------------------------------------------------------- task <-> dict
def task_to_dict(task: Task) -> dict[str, Any]:
    if task.constraints:
        raise SpecError(
            "Task constraints are not yet spec-serializable; keep the "
            "constrained Task in code, or drop constraints from the "
            "spec'd task (declarative constraint vocabulary is a "
            "roadmap item)."
        )
    return {
        "bounds": {k: list(v) for k, v in task.space.bounds.items()},
        "direction": task.direction,
        "fidelities": dict(task.fidelities),
        "success_value": task.success_value,
        "name": task.name,
    }


def task_from_dict(data: Mapping[str, Any]) -> Task:
    return Task(
        space=SearchSpace({k: (float(v[0]), float(v[1])) for k, v in data["bounds"].items()}),
        direction=data.get("direction", "maximize"),
        fidelities=dict(data.get("fidelities", {})),
        success_value=data.get("success_value"),
        name=data.get("name", ""),
    )


# ---------------------------------------------------------------- the spec
@dataclass(frozen=True)
class CampaignSpec:
    """Everything needed to reconstruct a campaign, declaratively."""

    oracle: Mapping[str, Any]
    driver: Mapping[str, Any]
    budget_total: Mapping[str, float]
    task: Mapping[str, Any] | None = None
    maximize: bool = True
    meter_driver: bool = True
    max_consecutive_rejections: int = 100
    max_queries: int | None = None
    seed: int | None = None  # reserved: campaign-level seed streams (ADR-0012)
    schema: str = SPEC_SCHEMA

    # -- serialization ------------------------------------------------
    def dumps(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)

    @classmethod
    def loads(cls, text: str) -> "CampaignSpec":
        raw = json.loads(text)
        schema = raw.get("schema")
        if schema != SPEC_SCHEMA:
            raise SpecError(f"Unsupported spec schema {schema!r} (expected {SPEC_SCHEMA!r})")
        return cls(**raw)

    def dump(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(self.dumps(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "CampaignSpec":
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    # -- construction --------------------------------------------------
    def build(self) -> "Campaign":
        from ..runtime.campaign import Campaign

        task = task_from_dict(self.task) if self.task is not None else None
        return Campaign(
            build_component(self.oracle),
            build_component(self.driver),
            Budget(total=Cost.from_dict(dict(self.budget_total))),
            task=task,
            maximize=None if task is not None else self.maximize,
            meter_driver=self.meter_driver,
            max_consecutive_rejections=self.max_consecutive_rejections,
        )
