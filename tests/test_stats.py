"""cadaques.stats: the one audited comparison module (A3)."""

from __future__ import annotations

import numpy as np
import pytest

from cadaques import Budget, Campaign, Cost, Task
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, quadratic_bowl
from cadaques.stats import (
    compare,
    paired_bootstrap_ci,
    paired_campaigns,
    wilcoxon_signed_rank,
)


class TestBootstrap:
    def test_ci_covers_a_known_shift(self):
        rng = np.random.default_rng(1)
        b = rng.normal(0.0, 1.0, 60)
        a = b + 0.5                      # exact paired shift of +0.5
        mean, lo, hi = paired_bootstrap_ci(a, b, rng=np.random.default_rng(2))
        assert mean == pytest.approx(0.5)
        assert lo <= 0.5 <= hi and lo > 0.0   # significant and covering

    def test_input_validation(self):
        with pytest.raises(ValueError):
            paired_bootstrap_ci([1.0], [2.0])
        with pytest.raises(ValueError):
            paired_bootstrap_ci([1.0, 2.0], [1.0, 2.0, 3.0])


class TestWilcoxon:
    def test_exact_case_matches_published_value(self):
        # all 8 differences positive: W+ = 36, one-sided p = 1/2^8 = 0.00390625
        a = np.arange(1.0, 9.0)
        b = np.zeros(8)
        w, p = wilcoxon_signed_rank(a, b, alternative="greater")
        assert w == 36.0 and p == pytest.approx(1 / 256)
        _, p2 = wilcoxon_signed_rank(a, b)          # two-sided doubles it
        assert p2 == pytest.approx(2 / 256)

    def test_symmetric_null_is_not_significant(self):
        rng = np.random.default_rng(3)
        a = rng.normal(0, 1, 20)
        b = rng.normal(0, 1, 20)
        _, p = wilcoxon_signed_rank(a, b)
        assert p > 0.05

    def test_ties_and_zeros_follow_convention(self):
        a = np.array([2.0, 2.0, 3.0, 5.0, 5.0, 4.0])
        b = np.array([2.0, 1.0, 1.0, 1.0, 1.0, 1.0])  # one zero diff dropped
        w, p = wilcoxon_signed_rank(a, b, alternative="greater")
        assert w == 15.0 and 0.0 < p < 0.1

    def test_normal_approximation_regime(self):
        rng = np.random.default_rng(4)
        b = rng.normal(0, 1, 40)
        a = b + 0.4
        _, p = wilcoxon_signed_rank(a, b, alternative="greater")
        assert p < 0.001


class TestComparison:
    def test_compare_bundles_everything(self):
        a = [1.0, 1.2, 0.9, 1.4, 1.1, 1.3]
        b = [0.5, 0.7, 0.6, 0.9, 0.4, 0.8]
        c = compare(a, b, seeds=range(6), metric="best")
        assert c.significant and c.mean_diff > 0
        assert "Wilcoxon" in c.summary() and "n=6" in c.summary()

    def test_paired_campaigns_annealed_beats_random_under_a_binding_budget(self):
        # The budget must bind: AnnealedLocalDriver's whole design reads
        # fraction_used, so with a near-infinite budget it never anneals
        # and its advantage vanishes (this test originally asserted the
        # unbudgeted version and correctly failed). Benchmarking in
        # CADAQUES is budget-bound by thesis.
        SPACE = {"x": (-2.0, 2.0), "y": (-2.0, 2.0)}
        oracle = lambda: AnalyticOracle(  # noqa: E731
            fn=quadratic_bowl({"x": 0.7, "y": -0.3}),
            price_fn=lambda _q: Cost(seconds=1.0),
        )

        def make(driver_cls):
            def factory(seed: int) -> Campaign:
                return Campaign(
                    oracle(), driver_cls(space=SPACE),
                    Budget(total=Cost(seconds=30.0)),   # ~30 queries, binding
                    task=Task.from_bounds(SPACE), seed=seed, meter_driver=False,
                )
            return factory

        result = paired_campaigns(
            make(AnnealedLocalDriver), make(RandomDriver),
            seeds=range(12),
            metric=lambda o: o.best.value,
            metric_name="best_value",
            alternative="greater",
        )
        assert result.mean_a > result.mean_b
        assert result.p_value < 0.05
        assert result.significant
