"""Recommendation mode: ranked, seeded, provenanced, metered (0.3b)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sklearn")

from cadaques import RankedCandidates, Task, recommend
from cadaques.oracles import DatasetOracle as _DS  # noqa: F401
from cadaques.drivers import BayesianDriver, RandomDriver
from cadaques.oracles import DatasetOracle

ROWS = [
    {"T": 700.0, "P": 1.0, "y": 0.20, "cost_s": 3600.0},
    {"T": 750.0, "P": 1.0, "y": 0.30, "cost_s": 3650.0},
    {"T": 800.0, "P": 1.5, "y": 0.44, "cost_s": 3800.0},
    {"T": 820.0, "P": 1.6, "y": 0.47, "cost_s": 3850.0},
    {"T": 850.0, "P": 2.0, "y": 0.46, "cost_s": 4000.0},
    {"T": 900.0, "P": 2.0, "y": 0.38, "cost_s": 4200.0},
    {"T": 760.0, "P": 1.8, "y": 0.35, "cost_s": 3700.0},
    {"T": 840.0, "P": 1.4, "y": 0.45, "cost_s": 3900.0},
]


def oracle():
    return DatasetOracle(rows=[dict(r) for r in ROWS], value="y",
                         cost_columns={"seconds": "cost_s"},
                         tariff={"seconds": 3800.0})


def test_recommend_returns_k_ranked_candidates_with_provenance():
    rec = recommend(oracle(), k=5, seed=7, pool_size=512)
    assert isinstance(rec, RankedCandidates) and len(rec) == 5
    assert [c.rank for c in rec] == [1, 2, 3, 4, 5]
    scores = [c.score_per_cost for c in rec]
    assert scores == sorted(scores, reverse=True)
    prov = rec.provenance
    assert prov["schema"] == "cadaques.recommendation/1"
    assert prov["history"]["n_results"] == len(ROWS)
    assert prov["seed"] == 7 and prov["metering"]["rank_seconds"] > 0
    assert "BayesianDriver" in prov["driver"]["class"]


def test_same_seed_same_ranking_bit_for_bit():
    a = recommend(oracle(), k=6, seed=42, pool_size=512)
    b = recommend(oracle(), k=6, seed=42, pool_size=512)
    assert [c.as_dict() for c in a] == [c.as_dict() for c in b]
    assert a.provenance["history"]["sha256"] == b.provenance["history"]["sha256"]


def test_recommendation_targets_the_promising_region():
    # data peaks around T~820, P~1.6; the top candidate should land near it
    rec = recommend(oracle(), k=3, seed=1, pool_size=2048)
    top = rec.best.params
    assert 770.0 <= top["T"] <= 880.0
    assert rec.best.predicted_value > 0.35


def test_task_constraints_filter_the_pool():
    class LowTemperature:
        def satisfied(self, p):
            return p["T"] <= 780.0

    task = Task.from_bounds(oracle().bounds, constraints=(LowTemperature(),))
    rec = recommend(oracle(), k=4, task=task, seed=3, pool_size=2048)
    assert all(c.params["T"] <= 780.0 for c in rec)


def test_exports_csv_and_json(tmp_path):
    rec = recommend(oracle(), k=4, seed=5, pool_size=256)
    csv_path = rec.to_csv(tmp_path / "next_experiments.csv")
    lines = csv_path.read_text().strip().splitlines()
    assert len(lines) == 5 and lines[0].startswith("rank,P,T,")
    payload = json.loads(rec.to_json(tmp_path / "rec.json").read_text())
    assert payload["schema"] == "cadaques.recommendation/1"
    assert len(payload["candidates"]) == 4


def test_history_sequence_input_and_derived_bounds():
    rec = recommend(oracle().as_history(), k=3, seed=2, pool_size=256)
    assert len(rec) == 3
    assert set(rec.provenance["bounds"]) == {"T", "P"}


def test_rankless_driver_is_refused():
    with pytest.raises(TypeError, match="rank"):
        recommend(oracle(), driver=RandomDriver(space=oracle().bounds), k=3)


def test_needs_enough_history():
    o = DatasetOracle(rows=[{"x": 1.0, "y": 2.0}], value="y")
    with pytest.raises(ValueError):
        recommend(o, driver=BayesianDriver(space={"x": (0, 2)}), k=2)


def test_summary_is_readable():
    rec = recommend(oracle(), k=2, seed=9, pool_size=256)
    s = rec.summary()
    assert "#1" in s and "score/cost" in s and "thinking time" in s


class TestFinitePoolPolicy:
    """External-review follow-up: measured-point exclusion as pool policy
    in recommend(), not driver mechanism (constraint-filtering precedent)."""

    def _pool_dicts(self):
        # 3 of these 5 are exactly measured rows of ROWS
        return [
            {"T": 700.0, "P": 1.0},   # measured
            {"T": 725.0, "P": 1.2},   # new
            {"T": 800.0, "P": 1.5},   # measured
            {"T": 805.0, "P": 1.55},  # new
            {"T": 850.0, "P": 2.0},   # measured
        ]

    def test_measured_candidates_are_excluded_by_default(self):
        rec = recommend(oracle(), k=10, pool=self._pool_dicts(), seed=1)
        got = {(round(c.params["T"], 6), round(c.params["P"], 6)) for c in rec}
        assert got == {(725.0, 1.2), (805.0, 1.55)}
        assert rec.provenance["pool_supplied"] is True
        assert rec.provenance["exclude_measured"] is True
        assert rec.provenance["pool_n_after_filters"] == 2

    def test_fully_measured_pool_fails_with_actionable_message(self):
        measured_only = [d for d in self._pool_dicts()
                         if d not in ({"T": 725.0, "P": 1.2}, {"T": 805.0, "P": 1.55})]
        with pytest.raises(ValueError, match="exclude_measured=False"):
            recommend(oracle(), k=3, pool=measured_only, seed=1)

    def test_opt_out_allows_replicates(self):
        measured_only = [{"T": 700.0, "P": 1.0}, {"T": 800.0, "P": 1.5}]
        rec = recommend(oracle(), k=5, pool=measured_only,
                        exclude_measured=False, seed=1)
        assert len(rec) == 2  # replicates are legitimate science, on request

    def test_array_pool_and_sorted_column_order(self):
        import numpy as np
        # sorted(bounds) order is (P, T)
        arr = np.array([[1.2, 725.0], [1.55, 805.0]])
        rec = recommend(oracle(), k=5, pool=arr, seed=1)
        assert {round(c.params["T"], 6) for c in rec} == {725.0, 805.0}

    def test_pool_dict_missing_parameter_is_refused(self):
        with pytest.raises(ValueError, match="lack parameters"):
            recommend(oracle(), k=2, pool=[{"T": 725.0}], seed=1)

    def test_failed_points_are_not_excluded_but_measured_are(self):
        from cadaques import Cost, FailureRecord, Query, Result

        o = DatasetOracle(
            rows=[{"T": 700.0, "P": 1.0, "y": 0.20},
                  {"T": 800.0, "P": 1.5, "y": 0.40}],
            value="y", tariff={"seconds": 1.0},
        )
        failed = Result.failed(
            Query(params={"T": 725.0, "P": 1.2}), cost=Cost(seconds=1.0),
            failure=FailureRecord(kind="dataset_miss", retryable=True),
        )
        hist = o.as_history() + [failed]
        drv = BayesianDriver(space={"T": (600.0, 900.0), "P": (0.5, 2.5)},
                             n_initial=2, seed=0)

        # the previously FAILED point is still recommendable...
        rec = recommend(hist, driver=drv, k=2, pool=[{"T": 725.0, "P": 1.2}], seed=1)
        assert len(rec) == 1 and rec.best.params["T"] == 725.0

        # ...while a successfully measured one is excluded
        with pytest.raises(ValueError, match="exclude_measured=False"):
            recommend(hist, driver=drv, k=2, pool=[{"T": 700.0, "P": 1.0}], seed=1)
