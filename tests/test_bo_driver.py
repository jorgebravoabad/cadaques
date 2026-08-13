"""Cost-aware Bayesian driver: contract, learning, and economics."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")

from cadaques import Budget, Campaign, Cost, Task
from cadaques.drivers import BayesianDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, DatasetOracle, quadratic_bowl  # noqa: F401
from cadaques.testing import check_driver_contract

SPACE = {"x": (-2.0, 2.0), "y": (-2.0, 2.0)}


def bowl_oracle():
    return AnalyticOracle(fn=quadratic_bowl({"x": 0.7, "y": -0.3}),
                          price_fn=lambda _q: Cost(seconds=1.0))


def test_driver_contract():
    check_driver_contract(
        lambda: BayesianDriver(space=SPACE, n_initial=2, n_candidates=32, seed=0),
        SPACE,
    )


def test_bo_beats_random_on_the_bowl_per_query():
    def run(driver):
        return Campaign(bowl_oracle(), driver, Budget(total=Cost(seconds=1e6)),
                        task=Task.from_bounds(SPACE), seed=7,
                        meter_driver=False).run(max_queries=25).best.value

    bo = run(BayesianDriver(space=SPACE, n_initial=5, n_candidates=128))
    rnd = run(RandomDriver(space=SPACE))
    assert bo > rnd            # same seed, same budget of queries
    assert bo > -0.05          # essentially at the optimum (max is 0.0)


def test_reproducible_under_campaign_seed():
    def run():
        return Campaign(bowl_oracle(),
                        BayesianDriver(space=SPACE, n_initial=3, n_candidates=64),
                        Budget(total=Cost(seconds=1e6)),
                        seed=11, meter_driver=False).run(max_queries=10)

    a, b = run(), run()
    assert [dict(r.query.params) for r in a.history] == \
           [dict(r.query.params) for r in b.history]


def test_cost_aware_acquisition_prefers_the_cheap_half():
    # The mechanism, isolated: hand-crafted history with values symmetric
    # in ±x but settled costs 1 s (left) vs 100 s (right). Plain EI has no
    # preference by symmetry; EI per predicted unit cost must propose in
    # the cheap half — for every seed. (The emergent whole-campaign
    # economics of this rule are a statistics-module study, not a unit
    # assertion: they are noisy by nature.)
    from cadaques import Query, Result

    def history():
        rows = [(x, 1.0 - 0.5 * (abs(x) - 1.0) ** 2, 1.0)
                for x in (-1.8, -1.4, -1.0, -0.6, -0.2)]
        rows += [(x, 1.0 - 0.5 * (abs(x) - 1.0) ** 2, 100.0)
                 for x in (0.2, 0.6, 1.0, 1.4, 1.8)]
        return [Result(query=Query(params={"x": x}), value=v, cost=Cost(seconds=c))
                for x, v, c in rows]

    for seed in range(12):
        d = BayesianDriver(space={"x": (-2.0, 2.0)}, n_initial=5,
                           n_candidates=512, cost_aware=True, seed=seed)
        q = d.propose(history(), Budget(total=Cost(seconds=1e9)).view())
        assert q.params["x"] < 0, f"seed {seed} proposed in the expensive half"


def test_survives_failed_history():
    driver = BayesianDriver(space=SPACE, n_initial=2, n_candidates=32, seed=1)
    o = DatasetOracle(rows=[{"x": 0.0, "y": 0.0, "v": 1.0}], value="v",
                      miss_policy="fail", tariff={"seconds": 1.0})
    out = Campaign(o, driver, Budget(total=Cost(seconds=1e6)),
                   seed=2, meter_driver=False).run(max_queries=6)
    assert out.n_queries == 6      # misses everywhere, still proposes
    assert out.n_failures >= 5
