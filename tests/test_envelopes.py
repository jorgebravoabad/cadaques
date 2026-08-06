"""Action/Observation envelopes, statuses and failure semantics (0.2 kernel)."""

from __future__ import annotations

import math

import pytest

from cadaques import (
    Action, Cost, FailureRecord, Observation, ObservationStatus, Query, Result,
)


def test_query_action_roundtrip():
    q = Query(params={"x": 1.0}, fidelity={"L": 24}, tag="probe")
    a = Action.from_query(q)
    assert a.operation == "evaluate" and a.parameters == {"x": 1.0}
    assert a.as_query() == q


def test_non_evaluation_action_refuses_query_view():
    with pytest.raises(ValueError):
        Action(operation="measure").as_query()


def test_result_defaults_are_backward_compatible():
    r = Result(query=Query(params={"x": 0.0}), value=1.0, cost=Cost(seconds=1))
    assert r.ok and r.status == "completed" and r.failure is None


def test_failed_result_carries_cost_and_nan_value():
    f = FailureRecord(kind="oracle_error", detail="boom", retryable=True)
    r = Result.failed(Query(params={"x": 0.0}), cost=Cost(seconds=2), failure=f)
    assert not r.ok and math.isnan(r.value) and r.cost == Cost(seconds=2)


def test_observation_result_roundtrip():
    r = Result(query=Query(params={"x": 0.5}, fidelity={"L": 8}), value=3.0,
               cost=Cost(seconds=1), info={"note": "fine"})
    o = Observation.from_result(r)
    assert o.status is ObservationStatus.COMPLETED and o.values == {"value": 3.0}
    assert o.as_result() == r


def test_failed_observation_requires_and_forbids_failure_correctly():
    a = Action(operation="evaluate", parameters={"x": 0.0})
    with pytest.raises(ValueError):
        Observation(action=a, status=ObservationStatus.FAILED, cost=Cost())
    with pytest.raises(ValueError):
        Observation(action=a, status=ObservationStatus.COMPLETED, cost=Cost(),
                    failure=FailureRecord(kind="x"))


def test_status_predicates():
    assert ObservationStatus.RUNNING.is_terminal is False
    assert ObservationStatus.FAILED.is_terminal is True
    assert ObservationStatus.PARTIALLY_COMPLETED.is_success is True
