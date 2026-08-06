"""Campaign-level seed streams (ADR-0012)."""

from __future__ import annotations

from cadaques import Budget, Campaign, CampaignSpec, Cost, Task
from cadaques.drivers import RandomDriver
from cadaques.oracles import AnalyticOracle, Ising2DOracle, quadratic_bowl

SPACE = {"T": (1.0, 4.0)}


def campaign(seed):
    # participants deliberately seed-less: the campaign seed rules them
    return Campaign(
        Ising2DOracle(default_L=6, default_sweeps=20, default_equilibration=10),
        RandomDriver(space=SPACE),
        Budget(total=Cost(seconds=60.0)),
        task=Task.from_bounds(SPACE, name="seeded"),
        seed=seed,
    )


def values(out):
    return [r.value for r in out.history]


def params(out):
    return [dict(r.query.params) for r in out.history]


def test_same_campaign_seed_reproduces_everything():
    a, b = campaign(42).run(max_queries=4), campaign(42).run(max_queries=4)
    assert params(a) == params(b) and values(a) == values(b)


def test_different_seeds_differ():
    a, b = campaign(1).run(max_queries=4), campaign(2).run(max_queries=4)
    assert params(a) != params(b)


def test_seed_survives_spec_roundtrip_and_reproduces():
    c = campaign(7)
    spec = c.to_spec()
    assert spec.seed == 7
    rebuilt = Campaign.from_spec(CampaignSpec.loads(spec.dumps()))
    out1, out2 = c.run(max_queries=3), rebuilt.run(max_queries=3)
    assert values(out1) == values(out2) and params(out1) == params(out2)


def test_seed_recorded_in_started_event():
    out = campaign(9).run(max_queries=1)
    assert out.events.of_kind("campaign_started")[0].payload["seed"] == 9


def test_explicit_participant_seed_still_respected_without_campaign_seed():
    o = AnalyticOracle(fn=quadratic_bowl({"T": 2.0}), price_fn=lambda _q: Cost(seconds=1))
    a = Campaign(o, RandomDriver(space=SPACE, seed=5), Budget(total=Cost(seconds=50))).run(max_queries=3)
    b = Campaign(o, RandomDriver(space=SPACE, seed=5), Budget(total=Cost(seconds=50))).run(max_queries=3)
    assert params(a) == params(b)
