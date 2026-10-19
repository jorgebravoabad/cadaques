"""Which participants a tool call may name, and how to describe them.

``CampaignSpec.build`` imports a dotted path and instantiates it. That
is the right power for a Python user and the wrong one for a tool
interface: a spec arriving through a tool call could otherwise name any
importable class, ``subprocess.Popen`` included. The agent layer
therefore admits only an explicit allowlist — the shipped spec-able
participants, plugins registered through the ``cadaques.drivers`` /
``cadaques.oracles`` entry points (ADR-0009), and classes the operator
of the server allows by name — and refuses everything else with
``spec_invalid`` before any import happens.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from importlib.metadata import entry_points
from typing import Any, Iterable, Mapping

#: Shipped participants that can be declared in a spec. AnalyticOracle
#: holds a callable and is deliberately absent (spec.py refuses it anyway).
BUILTIN_COMPONENTS: dict[str, tuple[str, ...]] = {
    "driver": (
        "cadaques.drivers.reference.RandomDriver",
        "cadaques.drivers.reference.AnnealedLocalDriver",
        "cadaques.drivers.bo.BayesianDriver",
        "cadaques.drivers.external.ExternalDriver",
    ),
    "oracle": (
        "cadaques.oracles.ising.Ising2DOracle",
        "cadaques.oracles.dataset.DatasetOracle",
        "cadaques.oracles.external.ExternalOracle",
    ),
}

ENTRY_POINT_GROUPS: dict[str, str] = {"driver": "cadaques.drivers", "oracle": "cadaques.oracles"}


def _entry_point_paths(role: str) -> list[str]:
    paths: list[str] = []
    for ep in entry_points(group=ENTRY_POINT_GROUPS[role]):
        module, _, attr = ep.value.partition(":")
        if attr:
            paths.append(f"{module}.{attr}")
    return paths


class ComponentRegistry:
    """The allowlist of spec-declarable participants, by role."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        self._extra = tuple(extra)

    def allowed(self, role: str) -> list[str]:
        return list(dict.fromkeys(
            list(BUILTIN_COMPONENTS[role]) + _entry_point_paths(role) + list(self._extra)
        ))

    def check(self, role: str, component: Any) -> list[str]:
        """Problems with a component spec; empty if it may be built."""
        if not isinstance(component, Mapping) or "class" not in component:
            return [f"/{role}: must be an object with 'class' and 'params'"]
        if component["class"] not in self.allowed(role):
            return [(
                f"/{role}/class: {component['class']!r} is not an allowed {role}; "
                "see describe_capabilities for the allowed classes"
            )]
        if not isinstance(component.get("params", {}), Mapping):
            return [f"/{role}/params: must be an object"]
        return []

    def describe(self, role: str) -> list[dict[str, Any]]:
        return [describe_component(path, role) for path in self.allowed(role)]


def _resolve(path: str) -> type:
    module, _, name = path.rpartition(".")
    return getattr(importlib.import_module(module), name)


def describe_component(path: str, role: str) -> dict[str, Any]:
    """A machine-readable constructor signature for one participant."""
    entry: dict[str, Any] = {"class": path, "role": role}
    try:
        cls = _resolve(path)
    except Exception as exc:  # noqa: BLE001 — an unavailable extra is a capability fact
        entry.update(available=False, reason=f"{type(exc).__name__}: {exc}")
        return entry
    doc = (cls.__doc__ or "").strip().splitlines()
    entry.update(available=True, summary=doc[0] if doc else "")
    if dataclasses.is_dataclass(cls):
        params = []
        for f in dataclasses.fields(cls):
            if not f.init:
                continue
            item: dict[str, Any] = {"name": f.name, "type": str(f.type)}
            if f.default is not dataclasses.MISSING:
                default = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                default = f.default_factory()  # type: ignore[misc]
            else:
                item["required"] = True
                params.append(item)
                continue
            try:
                item["default"] = json.loads(json.dumps(default))
            except TypeError:
                item["default"] = repr(default)
            params.append(item)
        entry["params"] = params
    return entry
