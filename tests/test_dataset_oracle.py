"""DatasetOracle: retrospective campaigns with a declared miss policy."""

from __future__ import annotations

import math

import pytest

from cadaques import Budget, Campaign, CampaignSpec, Cost, Query, Task
from cadaques.drivers import RandomDriver
from cadaques.oracles import DatasetOracle
from cadaques.testing import check_oracle_contract

ROWS = [
    {"T": 1.0, "P": 10.0, "yield": 0.20, "cost_s": 3600.0},
    {"T": 2.0, "P": 10.0, "yield": 0.35, "cost_s": 3700.0},
    {"T": 3.0, "P": 20.0, "yield": 0.50, "cost_s": 4000.0},
    {"T": 4.0, "P": 20.0, "yield": 0.45, "cost_s": 4100.0},
]


def make(**kw):
    kw.setdefault("tariff", {"seconds": 3600.0})
    return DatasetOracle(rows=[dict(r) for r in ROWS], value="yield",
                         cost_columns={"seconds": "cost_s"}, **kw)


def test_params_inferred_and_bounds_derived():
    o = make()
    assert set(o.params) == {"T", "P"}
    assert o.bounds == {"T": (1.0, 4.0), "P": (10.0, 20.0)}
    assert o.n_rows == 4


def test_exact_hit_serves_row_value_and_recorded_cost():
    o = make()
    r = o.evaluate(Query(params={"T": 3.0, "P": 20.0}))
    assert r.ok and r.value == 0.50
    assert r.cost == Cost(seconds=4000.0)         # settled = recorded, not tariff
    assert o.price(Query(params={"T": 3.0, "P": 20.0})) == Cost(seconds=3600.0)


def test_nearest_within_tolerance_snaps_and_reports_distance():
    o = make(tolerance=0.25)
    r = o.evaluate(Query(params={"T": 2.1, "P": 10.5}))
    assert r.ok and r.value == 0.35
    assert r.info["served_params"] == {"T": 2.0, "P": 10.0}
    assert 0.0 < r.info["distance"] <= 0.25


def test_miss_beyond_tolerance_fails_and_settles_tariff():
    o = make(tolerance=0.01)
    r = o.evaluate(Query(params={"T": 2.5, "P": 15.0}))
    assert not r.ok and math.isnan(r.value)
    assert r.failure.kind == "dataset_miss"
    assert r.cost == Cost(seconds=3600.0)          # a miss consumes budget


def test_fail_policy_requires_exact_point():
    o = make(miss_policy="fail")
    assert o.evaluate(Query(params={"T": 2.0, "P": 10.0})).ok
    assert not o.evaluate(Query(params={"T": 2.0001, "P": 10.0})).ok


def test_missing_parameter_is_a_dataset_miss():
    r = make().evaluate(Query(params={"T": 2.0}))
    assert not r.ok and "lacks parameters" in r.failure.detail


def test_from_csv_roundtrip(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("T,P,yield,cost_s\n1.0,10.0,0.2,3600\n3.0,20.0,0.5,4000\n")
    o = DatasetOracle.from_csv(p, value="yield", cost_columns={"seconds": "cost_s"})
    assert o.n_rows == 2 and o.evaluate(Query(params={"T": 3.0, "P": 20.0})).value == 0.5


def test_spec_roundtrip_carries_the_dataset():
    o = make(tolerance=0.3)
    task = Task.from_bounds(o.bounds, name="retro")
    # meter_driver=False: driver wall time is physical (see C13's lesson);
    # with it off, the retrospective economics are exactly deterministic.
    c = Campaign(o, RandomDriver(space=o.bounds), Budget(total=Cost(seconds=50000)),
                 task=task, seed=5, meter_driver=False)
    spec = CampaignSpec.loads(c.to_spec().dumps())
    rebuilt = Campaign.from_spec(spec)
    a, b = c.run(max_queries=4), rebuilt.run(max_queries=4)
    pa = [dict(r.query.params) for r in a.history]
    pb = [dict(r.query.params) for r in b.history]
    assert pa == pb                                # rng order-canonical (ADR-0012)
    va = [r.value for r in a.history]
    vb = [r.value for r in b.history]
    assert all(x == y or (math.isnan(x) and math.isnan(y)) for x, y in zip(va, vb))
    assert a.state.spent == b.state.spent          # deterministic economics


def test_oracle_contract():
    check_oracle_contract(lambda: make(tolerance=10.0),
                          Query(params={"T": 2.0, "P": 10.0}))


def test_validation_errors():
    with pytest.raises(ValueError, match="at least one row"):
        DatasetOracle(rows=[], value="y")
    with pytest.raises(ValueError, match="miss_policy"):
        make(miss_policy="teleport")
    with pytest.raises(ValueError, match="missing columns"):
        DatasetOracle(rows=[{"x": 1.0}], value="y")
