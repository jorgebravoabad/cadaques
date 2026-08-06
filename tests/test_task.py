"""Task, SearchSpace and constraint semantics (0.2 kernel)."""

from __future__ import annotations

import numpy as np
import pytest

from cadaques import Budget, Campaign, Cost, InvalidQuery, Query, SearchSpace, Task
from cadaques.drivers import RandomDriver
from cadaques.oracles import AnalyticOracle, quadratic_bowl

SPACE = SearchSpace({"x": (-2.0, 2.0), "y": (-2.0, 2.0)})


class HalfPlane:
    def satisfied(self, params):
        return params["x"] + params["y"] <= 2.0


def test_space_contains_validate_sample():
    assert SPACE.contains({"x": 0.0, "y": 1.0})
    assert not SPACE.contains({"x": 3.0, "y": 0.0})
    assert not SPACE.contains({"x": 0.0})  # missing variable
    with pytest.raises(InvalidQuery):
        SPACE.validate_point({"x": 3.0, "y": 0.0})
    pts = SPACE.sample(5, np.random.default_rng(0))
    assert len(pts) == 5 and all(SPACE.contains(p) for p in pts)


def test_space_rejects_empty_bounds():
    with pytest.raises(ValueError):
        SearchSpace({"x": (1.0, 1.0)})


def test_task_query_validation_and_constraints():
    task = Task(space=SPACE, constraints=(HalfPlane(),))
    assert task.admits(Query(params={"x": 0.5, "y": 0.5}))
    assert not task.admits(Query(params={"x": 1.5, "y": 1.5}))  # constraint
    assert not task.admits(Query(params={"x": 3.0, "y": 0.0}))  # bounds


def test_task_direction_and_success():
    t = Task.from_bounds({"x": (0, 1)}, direction="minimize", success_value=0.1)
    assert not t.maximize
    assert t.succeeded(0.05) and not t.succeeded(0.5)


def test_campaign_accepts_task_and_derives_direction():
    task = Task(space=SPACE, direction="minimize")
    oracle = AnalyticOracle(
        fn=quadratic_bowl({"x": 0.5, "y": -0.25}), price_fn=lambda _q: Cost(seconds=1.0)
    )
    driver = RandomDriver(space=task.space.bounds, seed=0)
    campaign = Campaign(oracle, driver, Budget(total=Cost(seconds=20)), task=task)
    assert campaign.maximize is False
    outcome = campaign.run(max_queries=5)
    assert outcome.n_queries == 5


def test_campaign_rejects_conflicting_direction():
    task = Task(space=SPACE, direction="minimize")
    oracle = AnalyticOracle(
        fn=quadratic_bowl({"x": 0.0, "y": 0.0}), price_fn=lambda _q: Cost(seconds=1.0)
    )
    with pytest.raises(ValueError):
        Campaign(oracle, RandomDriver(space=SPACE.bounds), Budget(total=Cost(seconds=5)),
                 task=task, maximize=True)
