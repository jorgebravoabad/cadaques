"""The dual architecture: the :class:`Oracle` and :class:`Driver` protocols.

CADAQUES is agnostic on both sides of the discovery loop:

* an **Oracle** is anything that answers queries at a declared price —
  a simulator, a laboratory instrument, an analytic function;
* a **Driver** is anything that decides what to ask next — random
  search, Bayesian optimization, gradient methods, an LLM agent.

Both sides are metered: the campaign runner charges oracle queries
*and* driver decisions against one budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .cost import BudgetView, Cost


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
    cost: Cost
    info: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Oracle(Protocol):
    """Anything that answers queries at a price.

    ``price`` declares the expected cost *ex ante* so the runner can
    check affordability before committing; ``evaluate`` performs the
    query and reports the *settled* cost inside the Result. Real
    oracles deviate from their declared price — the ledger records
    both, and that discrepancy is itself an observable.
    """

    def price(self, query: Query) -> Cost:
        ...

    def evaluate(self, query: Query) -> Result:
        ...


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
