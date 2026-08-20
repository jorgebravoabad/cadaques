"""The chemistry vertical: a reaction table, understood.

The kernel is domain-neutral by constitution — it sees columns, not
chemistry. This module is the thin semantic layer that lets a chemist
speak in their own vocabulary (composition descriptors, reaction
conditions, a target property, a cost per run) and have it *compile*
to kernel objects: the roles declared once in a
:class:`ReactionTable`, everything else derived.

Thin by design: no chemistry is computed here, no featurization, no
domain models — only column roles, validation, and builders. It is the
embryo of the eventual parametric campaign family for chemistry, kept
deliberately unbranded (three-year strategy, Decision Three).

The canonical flow of the first vertical::

    table = ReactionTable.from_csv(
        "reactions.csv",
        composition=("x_Mn", "x_Ce"),
        conditions=("T", "P", "contact_time"),
        objective="c2_yield",
        cost_columns={"seconds": "run_time_s"},
    )
    ranking = table.recommend_next(k=10, seed=42)   # advice for the lab
    ranking.to_csv("next_experiments.csv")

    # or replay it as a budgeted retrospective campaign:
    campaign = table.campaign(driver, budget, seed=42)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.cost import Budget
from ..core.records import Result
from ..core.task import Constraint, Direction, Task
from ..oracles.dataset import DatasetOracle
from ..runtime.recommend import RankedCandidates, recommend

__all__ = ["ReactionTable"]


@dataclass
class ReactionTable:
    """A table of measured reactions with declared column roles.

    Parameters
    ----------
    rows:
        One dict per measured reaction.
    objective:
        The target-property column (e.g. yield, selectivity, STY).
    composition:
        Columns describing what the material/catalyst *is*.
    conditions:
        Columns describing how the reaction was *run*.
    direction:
        ``"maximize"`` (default) or ``"minimize"`` the objective.
    cost_columns:
        Optional ``{currency: column}`` with the recorded cost per run.
    tariff:
        Declared cost per (hypothetical) new run, for budgeting.
    tolerance / miss_policy:
        Retrospective-mode lookup policy, passed to the oracle.

    Descriptors = composition + conditions: together they are the
    campaign's parameters. The split is semantic — it documents the
    chemist's mental model and lets downstream tools (constraints,
    reports, future kits) treat the two groups differently — while the
    kernel receives them uniformly.
    """

    rows: list[dict[str, Any]]
    objective: str
    composition: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    direction: Direction = "maximize"
    cost_columns: Mapping[str, str] = field(default_factory=dict)
    tariff: Mapping[str, float] = field(default_factory=lambda: {"seconds": 1.0})
    tolerance: float = float("inf")
    miss_policy: str = "nearest"
    name: str = "reaction_table"

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("ReactionTable needs at least one measured reaction")
        if not (self.composition or self.conditions):
            raise ValueError(
                "Declare at least one composition or condition column — "
                "the roles are the point of the vertical."
            )
        overlap = set(self.composition) & set(self.conditions)
        if overlap:
            raise ValueError(f"Columns cannot be both composition and condition: {sorted(overlap)}")
        if self.objective in (*self.composition, *self.conditions):
            raise ValueError(f"Objective column {self.objective!r} cannot also be a descriptor")
        missing = [
            c for c in (*self.descriptors, self.objective)
            if c not in self.rows[0]
        ]
        if missing:
            raise ValueError(f"Rows lack declared columns: {missing}")

    # ------------------------------------------------------------ roles
    @property
    def descriptors(self) -> tuple[str, ...]:
        """All campaign parameters: composition + conditions."""
        return (*self.composition, *self.conditions)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        objective: str,
        composition: Sequence[str] = (),
        conditions: Sequence[str] = (),
        **kwargs: Any,
    ) -> "ReactionTable":
        """Build from a CSV (stdlib only; numeric cells coerced)."""
        import csv

        rows: list[dict[str, Any]] = []
        with Path(path).open(newline="", encoding="utf-8") as fh:
            for raw in csv.DictReader(fh):
                row: dict[str, Any] = {}
                for k, v in raw.items():
                    try:
                        row[k] = float(v)
                    except (TypeError, ValueError):
                        row[k] = v
                rows.append(row)
        return cls(
            rows=rows,
            objective=objective,
            composition=tuple(composition),
            conditions=tuple(conditions),
            **kwargs,
        )

    # --------------------------------------------------------- compilers
    def oracle(self) -> DatasetOracle:
        """The table as a retrospective Oracle (kernel object)."""
        return DatasetOracle(
            rows=[dict(r) for r in self.rows],
            value=self.objective,
            params=self.descriptors,
            tariff=dict(self.tariff),
            cost_columns=dict(self.cost_columns),
            tolerance=self.tolerance,
            miss_policy=self.miss_policy,
        )

    def task(
        self,
        constraints: tuple[Constraint, ...] = (),
        success_value: float | None = None,
    ) -> Task:
        """The scientific objective as a Task (kernel object)."""
        return Task.from_bounds(
            self.oracle().bounds,
            direction=self.direction,
            constraints=constraints,
            success_value=success_value,
            name=self.name,
        )

    def history(self) -> list[Result]:
        """The measured past, as campaign history."""
        return self.oracle().as_history()

    # ---------------------------------------------------------- frontends
    def recommend_next(
        self,
        k: int = 10,
        *,
        driver: Any = None,
        constraints: tuple[Constraint, ...] = (),
        pool: Any = None,
        pool_size: int = 1024,
        exclude_measured: bool = True,
        seed: int | None = None,
    ) -> RankedCandidates:
        """Recommendation mode: the ranked next experiments.

        Defaults to the cost-aware BayesianDriver (``[bo]`` extra);
        constraints restrict the candidate pool; the returned object is
        seeded, provenanced and exportable — see
        :mod:`cadaques.runtime.recommend`.
        """
        return recommend(
            self.oracle(),
            driver,
            k=k,
            task=self.task(constraints=constraints),
            pool=pool,
            pool_size=pool_size,
            exclude_measured=exclude_measured,
            seed=seed,
        )

    def campaign(
        self,
        driver: Any,
        budget: Budget,
        *,
        constraints: tuple[Constraint, ...] = (),
        success_value: float | None = None,
        seed: int | None = None,
        **campaign_kwargs: Any,
    ):
        """Retrospective campaign mode over the hidden table."""
        from ..runtime.campaign import Campaign

        return Campaign(
            self.oracle(),
            driver,
            budget,
            task=self.task(constraints=constraints, success_value=success_value),
            seed=seed,
            **campaign_kwargs,
        )
