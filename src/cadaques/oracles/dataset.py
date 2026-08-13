"""The :class:`DatasetOracle`: retrospective campaigns over hidden data.

The door to every spreadsheet-based study: a table of previously
measured experiments becomes an Oracle, so drivers can be compared on
*real* experimental landscapes under real budgets — the retrospective
mode of the campaign architecture.

Two design commitments:

* **The miss policy is declared, never implicit.** A query that lands
  where no experiment was measured either snaps to the nearest
  measured point within a normalized tolerance (``"nearest"``) or
  becomes a FAILED result that *settles its declared cost*
  (``"fail"``, ADR-0006) — a dataset miss is the retrospective world's
  native failure mode, and it consumes budget exactly like a wasted
  experiment would have.

* **Deterministic economics.** The declared price is a fixed tariff,
  optionally overridden per row by recorded cost columns; settled
  equals declared unless a row records otherwise. Retrospective
  campaigns are therefore exactly reproducible — economics included —
  which is what the statistics module needs.

Rows are plain JSON-able dicts, so a DatasetOracle rides a
:class:`~cadaques.core.spec.CampaignSpec` unchanged (the dataset is
part of the declaration; for large tables prefer
:meth:`DatasetOracle.from_csv` at construction and cite the file).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.cost import Cost
from ..core.observation import FailureRecord
from ..core.records import Query, Result


@dataclass
class DatasetOracle:
    """A hidden tabular dataset served through the Oracle protocol.

    Parameters
    ----------
    rows:
        The measured experiments: one dict per row, containing every
        parameter column, the value column, and (optionally) recorded
        cost columns.
    value:
        Name of the objective column.
    params:
        Parameter column names. Empty means: every column that is not
        the value and not a cost column.
    tariff:
        Declared cost per query, as a ``{currency: amount}`` mapping
        (kept JSON-able for spec round-trips).
    cost_columns:
        Optional ``{currency: column_name}`` mapping; when present, a
        served row settles the row's recorded cost instead of the
        tariff — replaying the true historical economics.
    miss_policy:
        ``"nearest"`` (default) snaps to the nearest measured point if
        within ``tolerance``; ``"fail"`` — and ``"nearest"`` beyond
        tolerance — returns a FAILED result settling the declared
        tariff.
    tolerance:
        Normalized distance bound for ``"nearest"`` (each parameter
        scaled by its data range; ``inf`` accepts any query).
    """

    rows: list[dict[str, Any]]
    value: str
    params: tuple[str, ...] = ()
    tariff: Mapping[str, float] = field(default_factory=lambda: {"seconds": 1.0})
    cost_columns: Mapping[str, str] = field(default_factory=dict)
    miss_policy: str = "nearest"
    tolerance: float = float("inf")

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("DatasetOracle needs at least one row")
        if self.miss_policy not in ("nearest", "fail"):
            raise ValueError(f"Unknown miss_policy {self.miss_policy!r}")
        reserved = {self.value, *self.cost_columns.values()}
        if not self.params:
            self.params = tuple(k for k in self.rows[0] if k not in reserved)
        for row in self.rows:
            missing = [c for c in (*self.params, self.value) if c not in row]
            if missing:
                raise ValueError(f"Row missing columns {missing}: {row!r}")
        self._ranges = {
            p: (
                min(float(r[p]) for r in self.rows),
                max(float(r[p]) for r in self.rows),
            )
            for p in self.params
        }

    # ----------------------------------------------------------- helpers
    @classmethod
    def from_csv(cls, path: str | Path, value: str, **kwargs: Any) -> "DatasetOracle":
        """Build from a CSV file (stdlib only; numeric cells coerced)."""
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
        return cls(rows=rows, value=value, **kwargs)

    @property
    def bounds(self) -> dict[str, tuple[float, float]]:
        """Data-derived box bounds — feed them to a Task or a driver."""
        return dict(self._ranges)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    # ----------------------------------------------------------- lookup
    def _distance(self, query_params: Mapping[str, Any], row: Mapping[str, Any]) -> float:
        d = 0.0
        for p in self.params:
            low, high = self._ranges[p]
            span = (high - low) or 1.0
            d += ((float(query_params[p]) - float(row[p])) / span) ** 2
        return d ** 0.5

    def _nearest(self, query_params: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        best_row, best_d = None, float("inf")
        for row in self.rows:
            d = self._distance(query_params, row)
            if d < best_d:
                best_row, best_d = row, d
        assert best_row is not None
        return best_row, best_d

    def _row_cost(self, row: Mapping[str, Any]) -> Cost:
        if not self.cost_columns:
            return Cost.from_dict(dict(self.tariff))
        settled = dict(self.tariff)
        for currency, column in self.cost_columns.items():
            if column in row:
                settled[currency] = float(row[column])
        return Cost.from_dict(settled)

    # ----------------------------------------------------------- Oracle
    def price(self, query: Query) -> Cost:
        return Cost.from_dict(dict(self.tariff))

    def evaluate(self, query: Query) -> Result:
        missing = [p for p in self.params if p not in query.params]
        if missing:
            return Result.failed(
                query,
                cost=self.price(query),
                failure=FailureRecord(
                    kind="dataset_miss",
                    detail=f"query lacks parameters {missing}",
                    retryable=False,
                ),
            )
        row, distance = self._nearest(query.params)
        hit = distance == 0.0 if self.miss_policy == "fail" else distance <= self.tolerance
        if not hit:
            return Result.failed(
                query,
                cost=self.price(query),
                failure=FailureRecord(
                    kind="dataset_miss",
                    detail=(
                        f"nearest measured point at normalized distance "
                        f"{distance:.4g} exceeds policy "
                        f"({self.miss_policy}, tolerance={self.tolerance})"
                    ),
                    retryable=False,
                ),
            )
        return Result(
            query=query,
            value=float(row[self.value]),
            cost=self._row_cost(row),
            info={
                "served_params": {p: row[p] for p in self.params},
                "distance": distance,
            },
        )
