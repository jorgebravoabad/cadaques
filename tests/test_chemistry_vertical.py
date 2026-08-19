"""The chemistry vertical: roles in, kernel objects and rankings out."""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from cadaques import Budget, Cost
from cadaques.drivers import RandomDriver
from cadaques.verticals import ReactionTable

ROWS = [
    {"x_Mn": 0.10, "x_Ce": 0.05, "T": 700.0, "P": 1.0, "c2_yield": 0.18, "run_s": 3600.0},
    {"x_Mn": 0.20, "x_Ce": 0.05, "T": 750.0, "P": 1.0, "c2_yield": 0.29, "run_s": 3650.0},
    {"x_Mn": 0.20, "x_Ce": 0.10, "T": 800.0, "P": 1.5, "c2_yield": 0.43, "run_s": 3800.0},
    {"x_Mn": 0.25, "x_Ce": 0.10, "T": 820.0, "P": 1.6, "c2_yield": 0.47, "run_s": 3850.0},
    {"x_Mn": 0.30, "x_Ce": 0.15, "T": 850.0, "P": 2.0, "c2_yield": 0.44, "run_s": 4000.0},
    {"x_Mn": 0.15, "x_Ce": 0.20, "T": 760.0, "P": 1.8, "c2_yield": 0.33, "run_s": 3700.0},
    {"x_Mn": 0.28, "x_Ce": 0.08, "T": 840.0, "P": 1.4, "c2_yield": 0.45, "run_s": 3900.0},
]


def table(**kw):
    return ReactionTable(
        rows=[dict(r) for r in ROWS],
        objective="c2_yield",
        composition=("x_Mn", "x_Ce"),
        conditions=("T", "P"),
        cost_columns={"seconds": "run_s"},
        tariff={"seconds": 3800.0},
        name="ocm_demo",
        **kw,
    )


def test_roles_compile_to_kernel_objects():
    t = table()
    assert t.descriptors == ("x_Mn", "x_Ce", "T", "P")
    oracle = t.oracle()
    assert set(oracle.params) == set(t.descriptors)
    task = t.task()
    assert task.maximize and task.name == "ocm_demo"
    assert set(task.space.names) == set(t.descriptors)
    assert len(t.history()) == len(ROWS)


def test_role_validation():
    with pytest.raises(ValueError, match="at least one"):
        ReactionTable(rows=[{"a": 1.0, "y": 2.0}], objective="y")
    with pytest.raises(ValueError, match="both composition and condition"):
        ReactionTable(rows=[{"a": 1.0, "y": 2.0}], objective="y",
                      composition=("a",), conditions=("a",))
    with pytest.raises(ValueError, match="cannot also be a descriptor"):
        ReactionTable(rows=[{"a": 1.0, "y": 2.0}], objective="y",
                      composition=("y",), conditions=("a",))
    with pytest.raises(ValueError, match="lack declared columns"):
        ReactionTable(rows=[{"a": 1.0, "y": 2.0}], objective="y",
                      composition=("a", "missing"))


def test_recommend_next_end_to_end():
    rec = table().recommend_next(k=5, seed=42, pool_size=512)
    assert len(rec) == 5
    top = rec.best.params
    assert set(top) == {"x_Mn", "x_Ce", "T", "P"}
    # the data peak sits near (x_Mn~0.25, T~820): the top pick should be sane
    assert 0.10 <= top["x_Mn"] <= 0.32 and 740.0 <= top["T"] <= 860.0
    assert rec.provenance["task"] == "ocm_demo"


def test_constraints_restrict_recommendations():
    class MildConditions:
        def satisfied(self, p):
            return p["T"] <= 780.0 and p["P"] <= 1.5

    rec = table().recommend_next(k=4, constraints=(MildConditions(),),
                                 seed=3, pool_size=2048)
    assert all(c.params["T"] <= 780.0 and c.params["P"] <= 1.5 for c in rec)


def test_campaign_mode_from_the_same_table():
    t = table(tolerance=0.8)
    out = t.campaign(RandomDriver(space=t.oracle().bounds),
                     Budget(total=Cost(seconds=30000.0)),
                     seed=11, meter_driver=False).run(max_queries=6)
    assert out.n_queries == 6
    assert out.state.spent.seconds > 0


def test_from_csv(tmp_path):
    p = tmp_path / "reactions.csv"
    header = ",".join(ROWS[0])
    lines = [header] + [",".join(str(r[k]) for k in ROWS[0]) for r in ROWS]
    p.write_text("\n".join(lines))
    t = ReactionTable.from_csv(p, objective="c2_yield",
                               composition=("x_Mn", "x_Ce"), conditions=("T", "P"),
                               cost_columns={"seconds": "run_s"})
    assert len(t.rows) == len(ROWS)
    assert t.recommend_next(k=2, seed=1, pool_size=128).best.predicted_value > 0
