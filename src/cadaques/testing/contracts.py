"""Reusable conformance suites for the three participant contracts.

Each check takes a *factory* (a zero-argument callable returning a
fresh participant) so tests never share state between assertions, and
raises ``AssertionError`` with a named clause on the first violation.
The clauses are the protocols' fine print, written once:

* Oracles: pricing is side-effect free and repeatable; evaluation
  echoes the query and settles a real cost.
* Drivers: proposals are in-protocol; failed results never crash
  ``observe``; behaviour is reproducible under ``reseed`` (ADR-0012)
  when the driver supports it.
* Resources: estimation is side-effect free; handles reach a terminal
  status and collect exactly once; failures are FAILED observations
  that settle cost, never exceptions (ADR-0006).
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from ..core.cost import Budget, Cost
from ..core.observation import Action, Observation, ObservationStatus
from ..core.records import Query, Result


def _fresh_budget(seconds: float = 1e9) -> Budget:
    return Budget(total=Cost(seconds=seconds))


# --------------------------------------------------------------- oracle
def check_oracle_contract(
    oracle_factory: Callable[[], Any],
    sample_query: Query,
) -> None:
    """Assert the Oracle fine print for one implementation."""
    oracle = oracle_factory()

    declared_1 = oracle.price(sample_query)
    declared_2 = oracle.price(sample_query)
    assert isinstance(declared_1, Cost), "price() must return a Cost"
    assert declared_1 == declared_2, (
        "price() must be repeatable for the same query (side-effect free)"
    )
    assert all(v >= 0 for v in declared_1.as_dict().values()), (
        "declared cost must be non-negative in every currency"
    )

    result = oracle.evaluate(sample_query)
    assert isinstance(result, Result), "evaluate() must return a Result"
    assert result.query == sample_query, "Result must echo its Query"
    assert isinstance(result.cost, Cost), "Result must settle a Cost"
    if result.ok:
        assert isinstance(result.value, float), "successful Result.value must be float"

    # a second evaluation must not be prevented by internal state
    oracle.evaluate(sample_query)


# --------------------------------------------------------------- driver
def check_driver_contract(
    driver_factory: Callable[[], Any],
    space: Mapping[str, tuple[float, float]],
    history: Sequence[Result] = (),
) -> None:
    """Assert the Driver fine print for one implementation."""
    driver = driver_factory()
    budget = _fresh_budget()

    query = driver.propose(list(history), budget.view())
    assert isinstance(query, Query), "propose() must return a Query"
    assert set(query.params), "proposal must carry parameters"

    # failed results never crash observe (ADR-0006)
    failed = Result.failed(
        query,
        cost=Cost(seconds=1.0),
        failure=__import__(
            "cadaques.core.observation", fromlist=["FailureRecord"]
        ).FailureRecord(kind="contract_probe", detail="synthetic"),
    )
    driver.observe(failed)
    follow_up = driver.propose([failed], budget.view())
    assert isinstance(follow_up, Query), "propose() must survive a failed history"

    # reproducibility under reseed, when supported (ADR-0012)
    if callable(getattr(driver_factory(), "reseed", None)):
        a, b = driver_factory(), driver_factory()
        a.reseed(np.random.default_rng(1234))
        b.reseed(np.random.default_rng(1234))
        seq_a = [a.propose([], _fresh_budget().view()).params for _ in range(3)]
        seq_b = [b.propose([], _fresh_budget().view()).params for _ in range(3)]
        assert seq_a == seq_b, "reseed(rng) must make proposal sequences reproducible"


# -------------------------------------------------------------- resource
def check_resource_contract(
    resource_factory: Callable[[], Any],
    sample_action: Action,
) -> None:
    """Assert the Resource fine print for one implementation."""
    resource = resource_factory()

    est_1 = resource.estimate(sample_action)
    est_2 = resource.estimate(copy.deepcopy(sample_action))
    assert isinstance(est_1, Cost) and est_1 == est_2, (
        "estimate() must be a repeatable, side-effect-free Cost"
    )

    handle = resource.submit(sample_action)
    status = resource.status(handle)
    assert status.is_terminal or resource.status(handle) is not None, (
        "status() must be queryable until terminal"
    )

    observation = resource.collect(handle)
    assert isinstance(observation, Observation), "collect() must return an Observation"
    assert isinstance(observation.cost, Cost), "Observation must settle a Cost"
    if observation.status == ObservationStatus.FAILED:
        assert observation.failure is not None, (
            "FAILED observations must carry a FailureRecord (ADR-0006)"
        )

    try:
        resource.collect(handle)
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a handle must collect exactly once")
