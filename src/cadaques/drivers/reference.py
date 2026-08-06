"""Reference drivers.

Two deliberately simple strategies that already span the design
space CADAQUES cares about:

* :class:`RandomDriver` — the universal, budget-oblivious baseline
  against which every intelligent driver must justify its cost.
* :class:`AnnealedLocalDriver` — a budget-*aware* strategy: it
  explores globally while funds are plentiful and contracts around
  the incumbent as the budget runs out, reading the remaining
  fraction from the ``BudgetView`` it receives each iteration.

Bayesian optimization, gradient-based and LLM-agent drivers plug in
through the same two-method protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from ..core.cost import BudgetView
from ..core.records import Query, Result

SearchSpace = Mapping[str, tuple[float, float]]


def _uniform_sample(space: SearchSpace, rng: np.random.Generator) -> dict[str, float]:
    return {k: float(rng.uniform(low, high)) for k, (low, high) in space.items()}


@dataclass
class RandomDriver:
    """Uniform random search over a box-bounded space."""

    space: SearchSpace
    fidelity: Mapping[str, int] = field(default_factory=dict)
    seed: int | None = None

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def propose(self, history: Sequence[Result], budget: BudgetView) -> Query:
        return Query(params=_uniform_sample(self.space, self._rng), fidelity=dict(self.fidelity))

    def observe(self, result: Result) -> None:  # random search learns nothing
        pass


@dataclass
class AnnealedLocalDriver:
    """Gaussian local search whose step size anneals with the budget.

    The proposal is a perturbation of the best point seen so far,
    with standard deviation ``sigma_max`` at the start of the
    campaign shrinking linearly to ``sigma_min`` as
    ``budget.fraction_used`` approaches 1 — spend on exploration
    while rich, on exploitation while poor.
    """

    space: SearchSpace
    fidelity: Mapping[str, int] = field(default_factory=dict)
    sigma_max: float = 0.5
    sigma_min: float = 0.02
    maximize: bool = True
    seed: int | None = None

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self._best: Result | None = None

    # -- Driver protocol ------------------------------------------------
    def propose(self, history: Sequence[Result], budget: BudgetView) -> Query:
        if self._best is None:
            return Query(params=_uniform_sample(self.space, self._rng), fidelity=dict(self.fidelity))

        frac = min(max(budget.fraction_used, 0.0), 1.0)
        sigma_rel = self.sigma_max + (self.sigma_min - self.sigma_max) * frac

        params: dict[str, float] = {}
        for key, (low, high) in self.space.items():
            width = high - low
            center = float(self._best.query.params[key])
            proposal = center + self._rng.normal(0.0, sigma_rel * width)
            params[key] = float(np.clip(proposal, low, high))
        return Query(params=params, fidelity=dict(self.fidelity))

    def observe(self, result: Result) -> None:
        if self._best is None or self._improves(result):
            self._best = result

    def _improves(self, result: Result) -> bool:
        assert self._best is not None
        return result.value > self._best.value if self.maximize else result.value < self._best.value
