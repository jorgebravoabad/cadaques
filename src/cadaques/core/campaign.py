"""The Campaign runner: the discovery loop with an accountant inside.

The loop is deliberately small. Its one structural commitment is the
framework's thesis: *every query counts*. Each iteration prices the
oracle query before committing, meters the driver's decision, and the
campaign ends when the budget is exhausted — not when an iteration
counter runs out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from .cost import Budget, Cost
from .ledger import Ledger
from .protocols import Driver, Oracle, Result

StopReason = Literal["budget_exhausted", "max_queries", "driver_stopped"]


class DriverStopped(Exception):
    """A Driver may raise this to end a campaign early."""


@dataclass
class CampaignResult:
    best: Result | None
    history: list[Result]
    ledger: Ledger
    budget: Budget
    stop_reason: StopReason

    @property
    def n_queries(self) -> int:
        return len(self.history)

    def trace(self, currency: str = "seconds") -> list[tuple[float, float]]:
        """(cumulative settled cost, best-so-far value) — the signature
        cost-normalized curve of a CADAQUES campaign."""
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
        maximize: bool = True,
        meter_driver: bool = True,
    ) -> None:
        self.oracle = oracle
        self.driver = driver
        self.budget = budget
        self.maximize = maximize
        self.meter_driver = meter_driver
        self.ledger = Ledger()
        self.history: list[Result] = []

    # ------------------------------------------------------------------
    def run(self, max_queries: int | None = None) -> CampaignResult:
        stop_reason: StopReason = "budget_exhausted"
        best: Result | None = None

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
            self.ledger.record(
                "driver",
                label=type(self.driver).__name__,
                declared=driver_cost,
                settled=driver_cost,
            )

            # -- oracle query: price, afford, evaluate, settle ------------
            declared = self.oracle.price(query)
            if not self.budget.can_afford(declared):
                stop_reason = "budget_exhausted"
                break

            result = self.oracle.evaluate(query)
            self.budget.charge(result.cost, settle=True)
            self.ledger.record(
                "oracle",
                label=type(self.oracle).__name__,
                declared=declared,
                settled=result.cost,
                value=result.value,
                params=dict(result.query.params),
                fidelity=dict(result.query.fidelity),
            )

            self.history.append(result)
            self.driver.observe(result)

            if best is None or self._improves(result, best):
                best = result

        campaign_result = CampaignResult(
            best=best,
            history=self.history,
            ledger=self.ledger,
            budget=self.budget,
            stop_reason=stop_reason,
        )
        campaign_result._maximize = self.maximize
        return campaign_result

    def _improves(self, candidate: Result, incumbent: Result) -> bool:
        if self.maximize:
            return candidate.value > incumbent.value
        return candidate.value < incumbent.value
