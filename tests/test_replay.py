"""The north-star tests: replay equivalence and kill-and-resume (ADR-0004/0012)."""

from __future__ import annotations

from cadaques import (
    Budget, Campaign, CampaignSpec, Cost, Task, checkpoint, replay, resume,
)
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import Ising2DOracle

SPACE = {"T": (1.0, 4.0)}


from dataclasses import dataclass


@dataclass
class DeterministicOracle:
    """Settled == declared exactly: fully deterministic economics.

    A dataclass so checkpoint()'s to_spec() can declare it (specs refuse
    non-dataclass participants by design)."""

    tariff_seconds: float = 1.0

    def price(self, query):
        return Cost(seconds=1.0)

    def evaluate(self, query):
        from cadaques import Result

        t = float(query.params["T"])
        return Result(query=query, value=-(t - 2.269) ** 2, cost=Cost(seconds=1.0))


def make_campaign(seed=42, driver="random"):
    drv = (RandomDriver(space=SPACE) if driver == "random"
           else AnnealedLocalDriver(space=SPACE, sigma_max=0.4))
    return Campaign(
        Ising2DOracle(default_L=6, default_sweeps=20, default_equilibration=10),
        drv,
        Budget(total=Cost(seconds=120.0)),
        task=Task.from_bounds(SPACE, name="replaytest"),
        seed=seed,
    )


class TestReplay:
    def test_replayed_state_equals_live_state(self, tmp_path):
        c = make_campaign()
        out = c.run(max_queries=5)
        spec = c.to_spec()
        path = out.events.to_jsonl(tmp_path / "events.jsonl")
        assert replay(spec, path) == out.state
        assert replay(spec, out.events) == out.state

    def test_replay_after_json_spec_roundtrip(self, tmp_path):
        c = make_campaign(seed=7)
        out = c.run(max_queries=4)
        spec = CampaignSpec.loads(c.to_spec().dumps())
        state = replay(spec, out.events.to_jsonl(tmp_path / "e.jsonl"))
        assert state.n_queries == 4 and state.best_value == out.best.value


class TestResume:
    def _values(self, out):
        return [r.value for r in out.history]

    def _params(self, out):
        return [dict(r.query.params) for r in out.history]

    def test_kill_and_resume_matches_uninterrupted_run(self, tmp_path):
        full = make_campaign(seed=11).run(max_queries=6)

        c = make_campaign(seed=11)
        c.run(max_queries=3)                    # "killed" after 3
        checkpoint(c, tmp_path / "ckpt")
        revived = resume(tmp_path / "ckpt")
        out = revived.run(max_queries=6)        # total, including restored

        assert self._params(out) == self._params(full)
        assert self._values(out) == self._values(full)
        # Decisions, values, counts and *declared* costs are deterministic;
        # settled costs contain measured wall time, a physical quantity that
        # differs between any two runs (declared vs settled is the thesis).
        s, f = out.state, full.state
        assert (s.n_queries, s.n_failures, s.best_value, s.best_params,
                s.stop_reason, s.declared_oracle) == (
               f.n_queries, f.n_failures, f.best_value, f.best_params,
               f.stop_reason, f.declared_oracle)
        assert s.spent.seconds > 0
        assert len(out.events.of_kind("campaign_started")) == 1

    def test_resume_with_stateful_budget_aware_driver(self, tmp_path):
        # A budget-aware driver inherits the determinism of the costs it
        # reads: fraction_used built from measured wall time makes its
        # decisions physical. Exact resume-equality is therefore tested
        # under deterministic settled costs and unmetered decisions —
        # which is precisely what the guarantee promises, no more.
        def make():
            return Campaign(
                DeterministicOracle(),
                AnnealedLocalDriver(space=SPACE, sigma_max=0.4),
                Budget(total=Cost(seconds=60.0)),
                task=Task.from_bounds(SPACE, name="det"),
                seed=3,
                meter_driver=False,
            )

        full = make().run(max_queries=6)
        c = make()
        c.run(max_queries=3)
        checkpoint(c, tmp_path / "ckpt")
        out = resume(tmp_path / "ckpt").run(max_queries=6)

        assert self._params(out) == self._params(full)
        assert self._values(out) == self._values(full)
        assert out.state == full.state  # fully deterministic here, so exact

    def test_resumed_budget_position_is_exact(self, tmp_path):
        c = make_campaign(seed=5)
        c.run(max_queries=3)
        spent_before = c.budget.spent
        checkpoint(c, tmp_path / "ckpt")
        revived = resume(tmp_path / "ckpt")
        assert revived.budget.spent == spent_before
        assert revived.history and len(revived.history) == 3
