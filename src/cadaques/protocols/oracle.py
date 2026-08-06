"""The :class:`Oracle` protocol: anything that answers queries at a price."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..core.cost import Cost
from ..core.records import Query, Result


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
