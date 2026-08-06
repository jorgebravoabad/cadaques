"""Analytic oracles: exact functions with synthetic cost models.

Useful for unit tests and for controlled driver-benchmarking
experiments where the ground truth and the cost model are both known
exactly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..core.cost import Cost
from ..core.records import Query, Result


@dataclass
class AnalyticOracle:
    """Wrap ``fn(params) -> float`` as a metered Oracle.

    ``price_fn`` maps a query to its declared cost (default: a flat
    one-second tariff), letting tests exercise heterogeneous-cost
    scenarios deterministically. The settled cost equals the declared
    cost plus the (negligible) measured evaluation wall time, keeping
    the declared/settled distinction alive even in toy settings.
    """

    fn: Callable[[Mapping[str, float]], float]
    price_fn: Callable[[Query], Cost] = field(default=lambda _q: Cost(seconds=1.0))
    name: str = "analytic"

    def price(self, query: Query) -> Cost:
        return self.price_fn(query)

    def evaluate(self, query: Query) -> Result:
        t0 = time.perf_counter()
        value = float(self.fn(query.params))
        elapsed = time.perf_counter() - t0
        settled = self.price_fn(query) + Cost(seconds=elapsed)
        return Result(query=query, value=value, cost=settled, info={"oracle": self.name})


def quadratic_bowl(center: Mapping[str, float]) -> Callable[[Mapping[str, float]], float]:
    """Negative squared distance to ``center`` (maximum 0 at the center)."""

    def fn(params: Mapping[str, float]) -> float:
        return -sum((float(params[k]) - float(v)) ** 2 for k, v in center.items())

    return fn
