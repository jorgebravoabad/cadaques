"""The :class:`Driver` protocol: anything that decides what to ask next."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..core.cost import BudgetView
from ..core.records import Query, Result


@runtime_checkable
class Driver(Protocol):
    """Anything that decides what to ask next.

    ``propose`` receives the full history and a read-only view of the
    budget, enabling budget-aware strategies. ``observe`` feeds the
    settled result back. Drivers that incur their own accountable
    costs beyond wall time (e.g. LLM tokens) may expose a
    ``last_proposal_cost`` attribute, which the runner will charge.
    """

    def propose(self, history: Sequence[Result], budget: BudgetView) -> Query:
        ...

    def observe(self, result: Result) -> None:
        ...
