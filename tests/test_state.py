"""Derived state and the live/derived equivalence (ADR-0004)."""

from __future__ import annotations

from cadaques import Budget, Campaign, CampaignResult, Cost, Outcome, Task, reduce
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import AnalyticOracle, quadratic_bowl

SPACE = {"x": (-2.0, 2.0), "y": (-2.0, 2.0)}


def make_oracle():
    return AnalyticOracle(fn=quadratic_bowl({"x": 0.5, "y": -0.25}),
                          price_fn=lambda _q: Cost(seconds=1.0))


def test_outcome_is_campaignresult_alias():
    assert CampaignResult is Outcome


def test_live_state_equals_reduced_state():
    c = Campaign(make_oracle(), RandomDriver(space=SPACE, seed=0),
                 Budget(total=Cost(seconds=200)))
    out = c.run(max_queries=6)
    s = out.state
    assert s == reduce(out.events)
    assert s.n_queries == out.n_queries == 6
    assert s.best_value == out.best.value
    assert s.stop_reason == out.stop_reason
    assert s.spent == out.budget.spent
    assert s.n_proposals == 6 and s.n_failures == 0


class Exploding:
    def __init__(self):
        self.calls = 0
        self._inner = make_oracle()

    def price(self, q):
        return Cost(seconds=1.0)

    def evaluate(self, q):
        self.calls += 1
        if self.calls % 2 == 0:
            raise RuntimeError("boom")
        return self._inner.evaluate(q)


def test_state_accounts_failures_and_overrun():
    c = Campaign(Exploding(), RandomDriver(space=SPACE, seed=1),
                 Budget(total=Cost(seconds=200)))
    out = c.run(max_queries=6)
    s = out.state
    assert s.n_failures == 3 and s.n_queries == 6
    assert s.best_value == out.best.value
    # failures settle exactly declared -> their overrun contribution is zero,
    # successes settle declared + measured wall time -> overrun is positive
    assert s.overrun.seconds >= 0.0
    assert s.declared_oracle.seconds == 6.0


def test_annealed_driver_ignores_failed_results():
    d = AnnealedLocalDriver(space=SPACE, seed=2)
    c = Campaign(Exploding(), d, Budget(total=Cost(seconds=200)))
    out = c.run(max_queries=8)
    assert out.best is not None and out.best.ok
    assert d._best is not None and d._best.ok  # never poisoned by NaN


def test_minimize_direction_tracks_best_correctly():
    task = Task.from_bounds(SPACE, direction="minimize")
    c = Campaign(make_oracle(), RandomDriver(space=SPACE, seed=3),
                 Budget(total=Cost(seconds=200)), task=task)
    out = c.run(max_queries=10)
    assert out.state.best_value == min(r.value for r in out.history)
