"""The :class:`Task`: the scientific problem, without the method.

A Task declares *what* is sought — variables, direction, constraints,
fidelity knobs, an optional success criterion — and deliberately does
not prescribe *how* (no algorithm, no executor: ADR-0001 boundaries).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping, Protocol, Sequence, runtime_checkable

import numpy as np

from .records import Query

Direction = Literal["maximize", "minimize"]

#: The 0.1-style bounds mapping, kept as the lightweight interchange form.
Bounds = Mapping[str, tuple[float, float]]


class InvalidQuery(ValueError):
    """A query does not belong to the task's search space."""


@runtime_checkable
class Constraint(Protocol):
    """A declarative restriction on the search space.

    ``satisfied`` must be pure: no I/O, no randomness, no oracle calls
    — constraints are evaluated by the runtime before any budget is
    committed.
    """

    def satisfied(self, params: Mapping[str, Any]) -> bool:
        ...


@dataclass(frozen=True)
class SearchSpace:
    """A box-bounded continuous search space.

    Wraps the plain ``{name: (low, high)}`` mapping the 0.1 drivers
    use, adding validation and sampling. ``bounds`` returns that plain
    mapping, so existing drivers interoperate unchanged::

        driver = RandomDriver(space=task.space.bounds)
    """

    bounds: Bounds

    def __post_init__(self) -> None:
        for name, (low, high) in self.bounds.items():
            if not low < high:
                raise ValueError(f"Empty bounds for {name!r}: ({low}, {high})")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.bounds)

    def contains(self, params: Mapping[str, Any]) -> bool:
        if set(params) != set(self.bounds):
            return False
        return all(
            self.bounds[k][0] <= float(v) <= self.bounds[k][1] for k, v in params.items()
        )

    def validate_point(self, params: Mapping[str, Any]) -> None:
        if not self.contains(params):
            raise InvalidQuery(f"Point {dict(params)!r} outside space {dict(self.bounds)!r}")

    def sample(self, n: int, rng: np.random.Generator) -> list[dict[str, float]]:
        return [
            {k: float(rng.uniform(low, high)) for k, (low, high) in self.bounds.items()}
            for _ in range(n)
        ]

    def items(self) -> Iterator[tuple[str, tuple[float, float]]]:
        return iter(self.bounds.items())


@dataclass(frozen=True)
class Task:
    """The declarative scientific objective of a campaign.

    ``direction`` replaces the loose ``maximize=`` booleans scattered
    through 0.1 (Campaign derives its sense from the task when one is
    given). ``fidelities`` names the fidelity knobs and their defaults;
    ``success_value`` optionally declares a target at which the
    campaign may stop early.
    """

    space: SearchSpace
    direction: Direction = "maximize"
    constraints: tuple[Constraint, ...] = ()
    fidelities: Mapping[str, Any] = field(default_factory=dict)
    success_value: float | None = None
    name: str = ""

    @classmethod
    def from_bounds(cls, bounds: Bounds, **kwargs: Any) -> "Task":
        return cls(space=SearchSpace(dict(bounds)), **kwargs)

    @property
    def maximize(self) -> bool:
        return self.direction == "maximize"

    def validate_query(self, query: Query) -> None:
        """Raise :class:`InvalidQuery` if the query violates the task."""
        self.space.validate_point(query.params)
        for c in self.constraints:
            if not c.satisfied(query.params):
                raise InvalidQuery(f"Constraint {c!r} violated by {dict(query.params)!r}")

    def admits(self, query: Query) -> bool:
        try:
            self.validate_query(query)
        except InvalidQuery:
            return False
        return True

    def succeeded(self, value: float) -> bool:
        if self.success_value is None:
            return False
        return value >= self.success_value if self.maximize else value <= self.success_value
