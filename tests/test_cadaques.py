"""CADAQUES core test suite."""

from __future__ import annotations

import numpy as np
import pytest

from cadaques import Budget, BudgetExceeded, Campaign, Cost, Query
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, Ising2DOracle, T_C_EXACT, quadratic_bowl

SPACE = {"x": (-2.0, 2.0), "y": (-2.0, 2.0)}


def make_oracle(price_seconds: float = 1.0) -> AnalyticOracle:
    return AnalyticOracle(
        fn=quadratic_bowl({"x": 0.5, "y": -0.25}),
        price_fn=lambda _q: Cost(seconds=price_seconds),
    )


# ----------------------------------------------------------------- cost
class TestCost:
    def test_arithmetic(self):
        a, b = Cost(seconds=2, euros=1), Cost(seconds=3, tokens=10)
        assert a + b == Cost(seconds=5, euros=1, tokens=10)
        assert (a + b) - b == a
        assert a.scaled(2) == Cost(seconds=4, euros=2)

    def test_free_and_domination(self):
        assert Cost().is_free
        assert Cost(seconds=1).dominated_by(Cost(seconds=2, euros=1))
        assert not Cost(euros=2).dominated_by(Cost(seconds=5))

    def test_from_dict_rejects_unknown_currency(self):
        with pytest.raises(ValueError):
            Cost.from_dict({"doubloons": 3})


# --------------------------------------------------------------- budget
class TestBudget:
    def test_only_positive_totals_are_limited(self):
        budget = Budget(total=Cost(seconds=10))  # euros unlimited
        assert budget.can_afford(Cost(seconds=10, euros=1e6))
        assert not budget.can_afford(Cost(seconds=10.01))

    def test_charge_raises_beyond_budget(self):
        budget = Budget(total=Cost(seconds=1))
        with pytest.raises(BudgetExceeded):
            budget.charge(Cost(seconds=2))

    def test_settle_allows_overrun_and_view_reports_it(self):
        budget = Budget(total=Cost(seconds=1))
        budget.charge(Cost(seconds=2), settle=True)  # real world: paid, then discovered
        assert budget.view().fraction_used == pytest.approx(2.0)
        assert budget.exhausted


# ------------------------------------------------------------- campaign
class TestCampaign:
    def test_stops_on_budget_and_ledger_balances(self):
        budget = Budget(total=Cost(seconds=10.5))
        campaign = Campaign(make_oracle(1.0), RandomDriver(SPACE, seed=0), budget, meter_driver=False)
        result = campaign.run()

        assert result.stop_reason == "budget_exhausted"
        assert result.n_queries == 10  # 10 affordable one-second queries
        oracle_total = result.ledger.total("oracle")
        assert oracle_total.seconds == pytest.approx(budget.spent.seconds)
        assert result.best is not None and result.best.value <= 0.0

    def test_driver_decisions_are_metered(self):
        class SlowDeliberateDriver(RandomDriver):
            last_proposal_cost = Cost(tokens=100)  # e.g. an LLM agent

        budget = Budget(total=Cost(seconds=5.5))
        campaign = Campaign(make_oracle(1.0), SlowDeliberateDriver(SPACE, seed=0), budget)
        result = campaign.run()

        driver_total = result.ledger.total("driver")
        assert driver_total.tokens == 100 * len(
            [t for t in result.ledger.transactions if t.kind == "driver"]
        ) / 1  # every proposal charged
        assert driver_total.seconds > 0  # wall time metered too

    def test_max_queries_stop(self):
        campaign = Campaign(
            make_oracle(0.001), RandomDriver(SPACE, seed=1), Budget(total=Cost(seconds=1e6))
        )
        result = campaign.run(max_queries=7)
        assert result.stop_reason == "max_queries" and result.n_queries == 7

    def test_budget_aware_driver_beats_random_on_bowl(self):
        """Under identical budgets, the annealed local driver should land
        closer to the optimum than random search (statistical, seeded)."""
        def final_error(driver) -> float:
            campaign = Campaign(
                make_oracle(1.0), driver, Budget(total=Cost(seconds=60)), meter_driver=False
            )
            best = campaign.run().best
            assert best is not None
            return -best.value  # squared distance to optimum

        err_local = final_error(AnnealedLocalDriver(SPACE, seed=42))
        err_random = final_error(RandomDriver(SPACE, seed=42))
        assert err_local < err_random

    def test_trace_is_monotone(self):
        campaign = Campaign(
            make_oracle(1.0), RandomDriver(SPACE, seed=3), Budget(total=Cost(seconds=30))
        )
        trace = campaign.run().trace("seconds")
        costs = [c for c, _ in trace]
        bests = [v for _, v in trace]
        assert costs == sorted(costs)
        assert bests == sorted(bests)  # maximization: best-so-far is nondecreasing


# ---------------------------------------------------------------- ising
class TestIsingOracle:
    def test_price_scales_with_fidelity(self):
        oracle = Ising2DOracle()
        cheap = oracle.price(Query(params={"T": 2.3}, fidelity={"L": 8, "sweeps": 50}))
        dear = oracle.price(Query(params={"T": 2.3}, fidelity={"L": 32, "sweeps": 50}))
        assert dear.seconds == pytest.approx(cheap.seconds * 16, rel=1e-9)

    def test_evaluate_returns_finite_settled_result(self):
        oracle = Ising2DOracle(seed=7)
        result = oracle.evaluate(
            Query(params={"T": 2.3}, fidelity={"L": 12, "sweeps": 60, "equilibration": 40})
        )
        assert np.isfinite(result.value) and result.value >= 0
        assert result.cost.seconds > 0  # settled, measured cost

    def test_physics_ordering_and_magnetization(self):
        """Low-T phase is ordered (|m| near 1), high-T phase disordered;
        susceptibility near T_c exceeds susceptibility deep in either phase."""
        oracle = Ising2DOracle(seed=11)
        fid = {"L": 16, "sweeps": 300, "equilibration": 200}
        cold = oracle.evaluate(Query(params={"T": 1.2}, fidelity=fid))
        crit = oracle.evaluate(Query(params={"T": T_C_EXACT}, fidelity=fid))
        hot = oracle.evaluate(Query(params={"T": 4.0}, fidelity=fid))

        assert cold.info["abs_magnetization"] > 0.9
        assert hot.info["abs_magnetization"] < 0.4
        assert crit.value > cold.value and crit.value > hot.value

    def test_invalid_queries_rejected(self):
        oracle = Ising2DOracle()
        with pytest.raises(ValueError):
            oracle.evaluate(Query(params={"T": -1.0}))
        with pytest.raises(ValueError):
            oracle.price(Query(params={"T": 2.0}, fidelity={"L": 1}))
