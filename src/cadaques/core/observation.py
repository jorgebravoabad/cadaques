"""General envelopes and failure semantics of the campaign loop.

:class:`Action` and :class:`Observation` are the general request and
response records of the architecture (ADR-0002). The shipped
:class:`~cadaques.core.records.Query` and
:class:`~cadaques.core.records.Result` are their evaluation
specializations, connected here by exact, lossless conversions rather
than inheritance — 0.1 code never changes, and the general layer is
ready for the Resource protocol (ADR-0003).

Failure is a scientific result (ADR-0006): a failed evaluation carries
a :class:`FailureRecord`, keeps its settled cost, and stays on the
ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .cost import Cost
from .records import Query, Result


class ObservationStatus(str, Enum):
    """Closed vocabulary of execution outcomes (ADR-0006)."""

    REJECTED = "rejected"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    PARTIALLY_COMPLETED = "partially_completed"

    @property
    def is_terminal(self) -> bool:
        return self not in (ObservationStatus.SUBMITTED, ObservationStatus.RUNNING)

    @property
    def is_success(self) -> bool:
        return self in (ObservationStatus.COMPLETED, ObservationStatus.PARTIALLY_COMPLETED)


@dataclass(frozen=True)
class FailureRecord:
    """Why an action failed, and whether trying again could help.

    ``kind`` is a short machine-readable cause (``"oracle_error"``,
    ``"dataset_miss"``, ``"timeout"``, ...); ``retryable`` guides
    campaign policy; ``detail`` is for humans and logs.
    """

    kind: str
    detail: str = ""
    retryable: bool = False
    exception: str | None = None


@dataclass(frozen=True)
class Action:
    """The general typed request: one consequential next step.

    ``operation`` names the kind of step (``"evaluate"`` today;
    ``"run_workflow"``, ``"measure"``, ``"request_human"`` in later
    rungs). Stable serializable envelope, deliberately not a universal
    ontology.
    """

    operation: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    fidelity: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_query(cls, query: Query) -> "Action":
        meta = {"tag": query.tag} if query.tag else {}
        return cls(operation="evaluate", parameters=dict(query.params),
                   fidelity=dict(query.fidelity), metadata=meta)

    def as_query(self) -> Query:
        if self.operation != "evaluate":
            raise ValueError(f"Action operation {self.operation!r} is not an evaluation")
        return Query(params=dict(self.parameters), fidelity=dict(self.fidelity),
                     tag=str(self.metadata.get("tag", "")))


@dataclass(frozen=True)
class Observation:
    """The general typed response, always carrying its settled cost.

    ``values`` is a mapping so multi-objective observations are
    representable; the single-objective Result maps onto
    ``values={"value": ...}`` exactly.
    """

    action: Action
    status: ObservationStatus
    cost: Cost
    values: Mapping[str, float] = field(default_factory=dict)
    uncertainty: Mapping[str, float] = field(default_factory=dict)
    failure: FailureRecord | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status == ObservationStatus.FAILED and self.failure is None:
            raise ValueError("FAILED observations must carry a FailureRecord")
        if self.failure is not None and self.status.is_success:
            raise ValueError("Successful observations must not carry a FailureRecord")

    @classmethod
    def from_result(cls, result: Result) -> "Observation":
        return cls(
            action=Action.from_query(result.query),
            status=ObservationStatus(result.status),
            cost=result.cost,
            values={"value": result.value} if result.value is not None else {},
            failure=result.failure,
            diagnostics=dict(result.info),
        )

    def as_result(self) -> Result:
        value = self.values.get("value")
        if value is None and self.status.is_success:
            raise ValueError("Observation has no scalar 'value'; not Result-representable")
        return Result(
            query=self.action.as_query(),
            value=float("nan") if value is None else float(value),
            cost=self.cost,
            info=dict(self.diagnostics),
            status=self.status.value,
            failure=self.failure,
        )
