"""Typed request/response records of the discovery loop.

:class:`Query` and :class:`Result` are the evaluation-specialized
envelopes of the campaign loop (see ADR-0002): a Query is what a
Driver proposes, a Result is what an Oracle settles. The general
``Action``/``Observation`` envelopes of the campaign architecture
arrive with the Resource protocol and specialize back to these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Query:
    """A single question to an Oracle.

    ``params`` are the design/search variables; ``fidelity`` are the
    knobs that trade accuracy for cost (lattice size, mesh density,
    integration time, ...). Keeping fidelity explicit is what makes
    multi-fidelity, cost-aware campaigns first-class citizens.
    """

    params: Mapping[str, Any]
    fidelity: Mapping[str, Any] = field(default_factory=dict)
    tag: str = ""


@dataclass(frozen=True)
class Result:
    """An Oracle's answer, carrying its *settled* (actual) cost."""

    query: Query
    value: float
    cost: "Cost"
    info: Mapping[str, Any] = field(default_factory=dict)


from .cost import Cost  # noqa: E402  (kept at bottom to avoid cycle in doc order)
