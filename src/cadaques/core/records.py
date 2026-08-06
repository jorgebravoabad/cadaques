"""Typed request/response records of the discovery loop.

:class:`Query` and :class:`Result` are the evaluation-specialized
envelopes of the campaign loop (see ADR-0002): a Query is what a
Driver proposes, a Result is what an Oracle settles. The general
``Action``/``Observation`` envelopes of the campaign architecture
arrive with the Resource protocol and specialize back to these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping


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
    """An Oracle's answer, carrying its *settled* (actual) cost.

    ``status``/``failure`` (0.2, ADR-0006) default to a successful
    completion, so every 0.1 constructor call is unchanged. ``value``
    of a FAILED result is NaN by convention; the settled ``cost`` is
    real either way — failures consume budget.
    """

    query: Query
    value: float
    cost: "Cost"
    info: Mapping[str, Any] = field(default_factory=dict)
    status: str = "completed"
    failure: "FailureRecord | None" = None

    @property
    def ok(self) -> bool:
        return self.status in ("completed", "partially_completed")

    @classmethod
    def failed(cls, query: Query, cost: "Cost", failure: "FailureRecord",
               info: Mapping[str, Any] | None = None) -> "Result":
        return cls(query=query, value=float("nan"), cost=cost,
                   info=dict(info or {}), status="failed", failure=failure)


from .cost import Cost  # noqa: E402  (kept at bottom to avoid cycle in doc order)

if TYPE_CHECKING:  # pragma: no cover
    from .observation import FailureRecord

