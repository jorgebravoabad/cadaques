"""CampaignSpec: declarative round-trip of campaign definitions (0.2, A1)."""

from __future__ import annotations

import pytest

from cadaques import Budget, Campaign, CampaignSpec, Cost, SpecError, Task
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, Ising2DOracle, quadratic_bowl

SPACE = {"T": (1.0, 4.0)}


def small_ising_campaign():
    task = Task.from_bounds(SPACE, direction="maximize", name="ising_tc")
    oracle = Ising2DOracle(default_L=6, default_sweeps=20, default_equilibration=10, seed=7)
    driver = RandomDriver(space=SPACE, seed=11)
    return Campaign(oracle, driver, Budget(total=Cost(seconds=30.0)), task=task)


class TestRoundTrip:
    def test_spec_json_roundtrip_is_exact(self):
        spec = small_ising_campaign().to_spec(max_queries=3)
        again = CampaignSpec.loads(spec.dumps())
        assert again == spec
        assert spec.task["name"] == "ising_tc" and spec.schema == "cadaques.spec/1"

    def test_file_roundtrip(self, tmp_path):
        spec = small_ising_campaign().to_spec()
        p = spec.dump(tmp_path / "campaign.spec.json")
        assert CampaignSpec.load(p) == spec

    def test_rebuilt_campaign_reproduces_values(self):
        c1 = small_ising_campaign()
        spec = c1.to_spec()
        c2 = Campaign.from_spec(spec)
        out1 = c1.run(max_queries=3)
        out2 = c2.run(max_queries=3)
        assert [r.value for r in out1.history] == [r.value for r in out2.history]
        assert [dict(r.query.params) for r in out1.history] == [
            dict(r.query.params) for r in out2.history
        ]
        assert c2.maximize is True and c2.task.name == "ising_tc"

    def test_annealed_driver_is_spec_serializable(self):
        driver = AnnealedLocalDriver(space=SPACE, seed=3, sigma_max=0.4)
        oracle = Ising2DOracle(default_L=6, default_sweeps=10, seed=1)
        spec = Campaign(oracle, driver, Budget(total=Cost(seconds=10))).to_spec()
        rebuilt = Campaign.from_spec(spec)
        assert type(rebuilt.driver).__name__ == "AnnealedLocalDriver"
        assert rebuilt.driver.sigma_max == 0.4


class TestSpecErrors:
    def test_callable_participant_is_refused_loudly(self):
        oracle = AnalyticOracle(fn=quadratic_bowl({"x": 0.0}))
        campaign = Campaign(oracle, RandomDriver(space={"x": (-1, 1)}),
                            Budget(total=Cost(seconds=5)))
        with pytest.raises(SpecError, match="not JSON-serializable"):
            campaign.to_spec()

    def test_constrained_task_is_refused_for_now(self):
        class Half:
            def satisfied(self, p):
                return p["T"] < 2.0

        task = Task.from_bounds(SPACE, constraints=(Half(),))
        oracle = Ising2DOracle(default_L=6, seed=0)
        campaign = Campaign(oracle, RandomDriver(space=SPACE),
                            Budget(total=Cost(seconds=5)), task=task)
        with pytest.raises(SpecError, match="constraints"):
            campaign.to_spec()

    def test_wrong_schema_is_refused(self):
        spec = small_ising_campaign().to_spec()
        bad = spec.dumps().replace("cadaques.spec/1", "cadaques.spec/9")
        with pytest.raises(SpecError, match="schema"):
            CampaignSpec.loads(bad)
