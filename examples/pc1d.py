"""An in-process Oracle: the article's Listing 1, on a one-dimensional photonic crystal.

A stack of two dielectrics (eps_h, eps_l); the query parameter ``d_H`` is
the fraction of the unit cell filled by the high-index layer, and the
objective is the relative width (gap / midgap) of the first photonic band
gap at normal incidence (transfer-matrix formula).

The Oracle is the two-method protocol, no base class: ``price()`` declares
the cost before the query runs; ``evaluate()`` returns the result together
with the cost it actually measured. A short budgeted campaign then prints
the declared against the settled spend and the best point.

Needs the ``[bo]`` extra (the Gaussian-process driver):

    pip install 'cadaques[bo]'
    python examples/pc1d.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from cadaques import Budget, Campaign, Cost, Query, Result
from cadaques.drivers import BayesianDriver

SAMPLES = 8192


@dataclass
class QuarterWaveStack:  # an Oracle: a price before, a settled cost after
    eps_h: float = 13.0
    eps_l: float = 1.0
    s_per_sample: float = 2e-7  # declared CPU seconds per frequency sample (calibrated)

    def price(self, q: Query) -> Cost:
        return Cost(seconds=self.s_per_sample * SAMPLES * q.fidelity.get("resolution", 16))

    def evaluate(self, q: Query) -> Result:
        t0 = time.process_time()
        gap = stack_gap(q.params["d_H"], self.eps_h, self.eps_l,
                        SAMPLES * q.fidelity.get("resolution", 16))
        return Result(query=q, value=gap, cost=Cost(seconds=time.process_time() - t0))


def stack_gap(d, eps_h, eps_l, n):
    """First gap of a 1D crystal, normal incidence, gap / midgap."""
    nh, nl = np.sqrt(eps_h), np.sqrt(eps_l)
    w = np.linspace(1e-6, 0.75 / (nh * d + nl * (1 - d)), n)  # omega a / 2 pi c, below gap 2
    ph, pl = 2 * np.pi * w * nh * d, 2 * np.pi * w * nl * (1 - d)
    half_trace = np.cos(ph) * np.cos(pl) - 0.5 * (nh / nl + nl / nh) * np.sin(ph) * np.sin(pl)
    outside = np.flatnonzero(np.abs(half_trace) > 1)
    if outside.size == 0:  # no gap below the scanned band edge
        return 0.0
    lo, hi = w[outside[0]], w[outside[-1]]
    return float(2 * (hi - lo) / (hi + lo))


def main() -> None:
    campaign = Campaign(
        QuarterWaveStack(),
        BayesianDriver(space={"d_H": [0.05, 0.95]}, fidelity={"resolution": 16}),
        Budget(total=Cost(seconds=1.5)),
        seed=0,
    )
    outcome = campaign.run()  # until no declared cost fits the budget

    txs = [t for t in outcome.ledger.transactions if t.kind != "driver"]
    declared = sum(t.declared.seconds for t in txs)
    settled = sum(t.settled.seconds for t in txs)
    planning = sum(t.settled.seconds for t in outcome.ledger.transactions if t.kind == "driver")
    print(f"stop: {outcome.stop_reason} | evaluations: {outcome.n_queries}")
    print(f"evaluations, declared: {declared:.3f} s | settled: {settled:.3f} s | "
          f"driver planning: {planning:.3f} s | total spent: {outcome.budget.spent.seconds:.3f} s of 1.5 s")
    best = outcome.best
    print(f"best d_H = {best.query.params['d_H']:.4f}  gap/midgap = {best.value:.4f}")


if __name__ == "__main__":
    main()
