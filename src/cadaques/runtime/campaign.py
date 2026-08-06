"""The Campaign runner: the discovery loop with an accountant inside.

The loop is deliberately small. Its one structural commitment is the
framework's thesis: *every query counts*. Each iteration prices the
oracle query before committing, meters the driver's decision, and the
campaign ends when the budget is exhausted — not when an iteration
counter runs out.

Since 0.2 the loop is event-sourced (ADR-0004): every transition is
appended to an :class:`~cadaques.core.events.EventLog`, and the
accounting :class:`~cadaques.core.ledger.Ledger` is derived from it.
Failures are results (ADR-0006): an oracle exception becomes a FAILED
:class:`Result` that settles its declared cost and stays on the
record, instead of aborting the loop and losing the ledger tail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Sequence

from ..core.cost import Budget, Cost
from ..core.events import EventLog
from ..core.ledger import Ledger
from ..core.observation import FailureRecord
from ..core.records import Query, Result
from ..core.task import Task
from ..protocols.driver import Driver
from ..protocols.oracle import Oracle

if TYPE_CHECKING:  # pragma: no cover
    from ..core.spec import CampaignSpec

StopReason = Literal[
    "budget_exhausted",
    "max_queries",
    "driver_stopped",
    "rejection_limit",
    "success",
]


class DriverStopped(Exception):
    """A Driver may raise this to end a campaign early."""


@dataclass
class CampaignResult:
    best: Result | None
    history: list[Result]
    ledger: Ledger
    events: EventLog
    budget: Budget
    stop_reason: StopReason

    @property
    def n_queries(self) -> int:
        return len(self.history)

    @property
    def n_failures(self) -> int:
        return sum(1 for r in self.history if not r.ok)

    def trace(self, currency: str = "seconds") -> list[tuple[float, float]]:
        """(cumulative settled cost, best-so-far value) — the signature
        cost-normalized curve of a CADAQUES campaign. Failed queries
        contribute cost but never a value."""
        points: list[tuple[float, float]] = []
        best: float | None = None
        for tx, running in self.ledger.cumulative():
            if tx.kind != "oracle":
                continue
            value = tx.meta.get("value")
            if value is None:
                continue
            best = value if best is None else max(best, value) if self._maximize else min(best, value)
            points.append((getattr(running, currency), best))
        return points

    _maximize: bool = field(default=True, repr=False)


class Campaign:
    """Couple one Oracle to one Driver under one Budget."""

    def __init__(
        self,
        oracle: Oracle,
        driver: Driver,
        budget: Budget,
        *,
        task: Task | None = None,
        maximize: bool | None = None,
        meter_driver: bool = True,
        max_consecutive_rejections: int = 100,
    ) -> None:
        if task is not None and maximize is not None and maximize != task.maximize:
            raise ValueError("Conflicting 'maximize' and task.direction; pass one.")
        self.oracle = oracle
        self.driver = driver
        self.budget = budget
        self.task = task
        self.maximize = task.maximize if task is not None else (True if maximize is None else maximize)
        self.meter_driver = meter_driver
        self.max_consecutive_rejections = max_consecutive_rejections
        self.events = EventLog()
        self.history: list[Result] = []

    # ------------------------------------------------------------------
    def to_spec(self, *, max_queries: int | None = None) -> "CampaignSpec":
        """Declare this campaign as a :class:`CampaignSpec` (ADR-0004/A1).

        Raises :class:`~cadaques.core.spec.SpecError` if a participant
        is not spec-representable (e.g. holds callables).
        """
        from ..core.spec import CampaignSpec, component_spec, task_to_dict

        return CampaignSpec(
            oracle=component_spec(self.oracle),
            driver=component_spec(self.driver),
            budget_total=self.budget.total.as_dict(),
            task=task_to_dict(self.task) if self.task is not None else None,
            maximize=self.maximize,
            meter_driver=self.meter_driver,
            max_consecutive_rejections=self.max_consecutive_rejections,
            max_queries=max_queries,
        )

    @classmethod
    def from_spec(cls, spec: "CampaignSpec") -> "Campaign":
        """Reconstruct a fresh campaign from its declaration."""
        return spec.build()

    # ------------------------------------------------------------------
    @property
    def ledger(self) -> Ledger:
        """The accounting view, derived from the event log (ADR-0004)."""
        return Ledger.from_events(self.events)

    # ------------------------------------------------------------------
    def run(self, max_queries: int | None = None) -> CampaignResult:
        self.events.append(
            "campaign_started",
            oracle=type(self.oracle).__name__,
            driver=type(self.driver).__name__,
            maximize=self.maximize,
            meter_driver=self.meter_driver,
            budget_total=self.budget.total.as_dict(),
            task=self.task.name if self.task is not None else None,
            max_queries=max_queries,
        )

        stop_reason: StopReason = "budget_exhausted"
        best: Result | None = None
        consecutive_rejections = 0

        while True:
            if max_queries is not None and len(self.history) >= max_queries:
                stop_reason = "max_queries"
                break

            # -- driver decision (metered) -------------------------------
            t0 = time.perf_counter()
            try:
                query = self.driver.propose(self.history, self.budget.view())
            except DriverStopped:
                stop_reason = "driver_stopped"
                break
            elapsed = time.perf_counter() - t0

            driver_cost = Cost()
            if self.meter_driver:
                driver_cost = Cost(seconds=elapsed) + getattr(
                    self.driver, "last_proposal_cost", Cost()
                )
                self.budget.charge(driver_cost, settle=True)
            self.events.append(
                "driver_proposal",
                label=type(self.driver).__name__,
                declared=driver_cost.as_dict(),
                settled=driver_cost.as_dict(),
            )

            # -- task validation: rejection is recorded, not raised ------
            if self.task is not None and not self.task.admits(query):
                consecutive_rejections += 1
                self.events.append(
                    "rejected",
                    label=type(self.driver).__name__,
                    params=dict(query.params),
                    fidelity=dict(query.fidelity),
                    reason="task_validation",
                )
                if consecutive_rejections >= self.max_consecutive_rejections:
                    stop_reason = "rejection_limit"
                    break
                continue
            consecutive_rejections = 0

            # -- oracle query: price, afford, evaluate, settle ------------
            declared = self.oracle.price(query)
            if not self.budget.can_afford(declared):
                stop_reason = "budget_exhausted"
                break

            try:
                result = self.oracle.evaluate(query)
            except Exception as exc:  # noqa: BLE001 — failure is a result (ADR-0006)
                failure = FailureRecord(
                    kind="oracle_error",
                    detail=str(exc),
                    retryable=False,
                    exception=type(exc).__name__,
                )
                result = Result.failed(query, cost=declared, failure=failure)

            self.budget.charge(result.cost, settle=True)
            if result.ok:
                self.events.append(
                    "oracle_result",
                    label=type(self.oracle).__name__,
                    declared=declared.as_dict(),
                    settled=result.cost.as_dict(),
                    value=result.value,
                    params=dict(result.query.params),
                    fidelity=dict(result.query.fidelity),
                    status=result.status,
                )
            else:
                assert result.failure is not None
                self.events.append(
                    "oracle_failure",
                    label=type(self.oracle).__name__,
                    declared=declared.as_dict(),
                    settled=result.cost.as_dict(),
                    params=dict(result.query.params),
                    fidelity=dict(result.query.fidelity),
                    status=result.status,
                    failure_kind=result.failure.kind,
                    failure_detail=result.failure.detail,
                    exception=result.failure.exception,
                )

            self.history.append(result)
            self.driver.observe(result)

            if result.ok and (best is None or self._improves(result, best)):
                best = result

            if (
                self.task is not None
                and result.ok
                and self.task.succeeded(result.value)
            ):
                stop_reason = "success"
                break

        self.events.append(
            "stopped",
            reason=stop_reason,
            n_queries=len(self.history),
            spent=self.budget.spent.as_dict(),
        )
        campaign_result = CampaignResult(
            best=best,
            history=self.history,
            ledger=self.ledger,
            events=self.events,
            budget=self.budget,
            stop_reason=stop_reason,
        )
        campaign_result._maximize = self.maximize
        return campaign_result

    def _improves(self, candidate: Result, incumbent: Result) -> bool:
        if self.maximize:
            return candidate.value > incumbent.value
        return candidate.value < incumbent.value
