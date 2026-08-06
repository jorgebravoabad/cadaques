"""Resource protocol and the OracleResource adapter (ADR-0003)."""

from __future__ import annotations

import math

import pytest

from cadaques import Action, Cost, ObservationStatus
from cadaques.protocols import JobStatus, OracleResource, Resource
from cadaques.oracles import AnalyticOracle, quadratic_bowl


def make_resource():
    return OracleResource(
        AnalyticOracle(fn=quadratic_bowl({"x": 0.0}), price_fn=lambda _q: Cost(seconds=2.0))
    )


def test_adapter_satisfies_protocol_and_lifecycle():
    res = make_resource()
    assert isinstance(res, Resource)
    action = Action(operation="evaluate", parameters={"x": 1.0})
    assert res.estimate(action) == Cost(seconds=2.0)
    h = res.submit(action)
    assert res.status(h) is JobStatus.DONE
    obs = res.collect(h)
    assert obs.status is ObservationStatus.COMPLETED
    assert obs.values["value"] == pytest.approx(-1.0)
    with pytest.raises(KeyError):
        res.collect(h)  # a handle collects exactly once


class Exploding:
    def price(self, q):
        return Cost(seconds=3.0)

    def evaluate(self, q):
        raise RuntimeError("cryostat quench")


def test_oracle_exception_becomes_failed_observation_with_declared_cost():
    res = OracleResource(Exploding())
    h = res.submit(Action(operation="evaluate", parameters={"x": 0.5}))
    assert res.status(h) is JobStatus.FAILED
    obs = res.collect(h)
    assert obs.status is ObservationStatus.FAILED
    assert obs.cost == Cost(seconds=3.0)          # failures settle declared cost
    assert obs.failure.kind == "oracle_error"
    r = obs.as_result()
    assert math.isnan(r.value) and not r.ok


def test_cancel_is_idempotent():
    res = make_resource()
    h = res.submit(Action(operation="evaluate", parameters={"x": 0.0}))
    res.cancel(h)
    res.cancel(h)
    with pytest.raises(KeyError):
        res.status(h)
