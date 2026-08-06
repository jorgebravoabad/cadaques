"""Event-sourced runtime: log, derived ledger, failures, rejections (0.2)."""

from __future__ import annotations

import math

import pytest

from cadaques import (
    Budget, Campaign, Cost, EventLog, Query, Result, SearchSpace, Task,
)
from cadaques.core.events import EVENT_SCHEMA
from cadaques.drivers import RandomDriver
from cadaques.oracles import AnalyticOracle, quadratic_bowl

SPACE = {"x": (-2.0, 2.0), "y": (-2.0, 2.0)}


def make_oracle(price=1.0):
    return AnalyticOracle(fn=quadratic_bowl({"x": 0.5, "y": -0.25}),
                          price_fn=lambda _q: Cost(seconds=price))


def run_small(n=5):
    c = Campaign(make_oracle(), RandomDriver(space=SPACE, seed=0),
                 Budget(total=Cost(seconds=100)))
    return c.run(max_queries=n)


class TestEventLog:
    def test_lifecycle_events_present_and_ordered(self):
        out = run_small(3)
        kinds = [e.kind for e in out.events]
        assert kinds[0] == "campaign_started" and kinds[-1] == "stopped"
        assert kinds.count("oracle_result") == 3
        assert [e.seq for e in out.events] == list(range(len(out.events)))

    def test_ledger_is_derived_from_events(self):
        out = run_small(4)
        assert len(out.ledger.transactions) == len(out.events.of_kind("driver_proposal")) + 4
        assert out.ledger.total("oracle") == Cost(seconds=0) + out.ledger.total("oracle")
        assert out.stop_reason == "max_queries"

    def test_jsonl_roundtrip_reconstructs_log(self, tmp_path):
        out = run_small(3)
        p = out.events.to_jsonl(tmp_path / "campaign.jsonl")
        loaded = EventLog.from_jsonl(p)
        assert [e.as_dict() for e in loaded] == [e.as_dict() for e in out.events]
        assert loaded.events[0].as_dict()["schema"] == EVENT_SCHEMA

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            EventLog().append("teleported")


class ExplodingOracle:
    def __init__(self, fail_on=2):
        self.calls, self.fail_on = 0, fail_on
        self._inner = make_oracle()

    def price(self, query):
        return Cost(seconds=1.0)

    def evaluate(self, query):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("detector on fire")
        return self._inner.evaluate(query)


class TestFailureSemantics:
    def test_oracle_exception_becomes_failed_result_and_charges_declared(self):
        c = Campaign(ExplodingOracle(fail_on=2), RandomDriver(space=SPACE, seed=1),
                     Budget(total=Cost(seconds=50)))
        out = c.run(max_queries=4)
        assert out.n_queries == 4 and out.n_failures == 1
        failed = [r for r in out.history if not r.ok][0]
        assert math.isnan(failed.value) and failed.cost == Cost(seconds=1.0)
        assert failed.failure.kind == "oracle_error"
        assert len(out.events.of_kind("oracle_failure")) == 1
        # best is never a failed result
        assert out.best is not None and out.best.ok

    def test_failure_cost_appears_on_ledger_but_not_in_trace_values(self):
        c = Campaign(ExplodingOracle(fail_on=1), RandomDriver(space=SPACE, seed=2),
                     Budget(total=Cost(seconds=50)))
        out = c.run(max_queries=3)
        oracle_txs = [t for t in out.ledger.transactions if t.kind == "oracle"]
        assert len(oracle_txs) == 3
        assert len(out.trace()) == 2  # failed query contributes no value point


class EscapingDriver:
    """Proposes out-of-space points forever."""

    def propose(self, history, budget):
        return Query(params={"x": 99.0, "y": 99.0})

    def observe(self, result):
        pass


class TestRejection:
    def test_out_of_task_proposals_are_recorded_and_bounded(self):
        task = Task.from_bounds(SPACE)
        c = Campaign(make_oracle(), EscapingDriver(), Budget(total=Cost(seconds=1e6)),
                     task=task, max_consecutive_rejections=7)
        out = c.run()
        assert out.stop_reason == "rejection_limit"
        assert len(out.events.of_kind("rejected")) == 7
        assert out.n_queries == 0

    def test_success_stops_campaign(self):
        task = Task.from_bounds(SPACE, success_value=-1.0)  # bowl max is 0.0
        c = Campaign(make_oracle(), RandomDriver(space=SPACE, seed=3),
                     Budget(total=Cost(seconds=1e6)), task=task)
        out = c.run(max_queries=500)
        assert out.stop_reason == "success"
        assert out.best is not None and out.best.value >= -1.0
