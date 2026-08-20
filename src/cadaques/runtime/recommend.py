"""Recommendation mode: from a measured table to the next experiments.

Campaign mode replays or executes queries one at a time; *recommendation
mode* answers the question a bench scientist actually asks: given
everything we have measured, **which experiments should we run next?**
The history is the known past, the surrogates fit on all of it, nothing
is executed, and the output is a :class:`RankedCandidates` — a small,
auditable scientific object, because a ranked list handed to a
laboratory is a scientific claim:

* **Seeded and reproducible** (ADR-0012): the same inputs and seed
  reproduce the same ranking, bit for bit.
* **Provenanced**: the ranking carries the driver's declaration
  (component spec), a fingerprint of the history it was fitted on, the
  seed, the pool size, and the schema version.
* **Metered** (the price of intelligence): the wall time the driver
  spent thinking is measured and recorded on the object.
* **Exportable**: ``to_csv`` for the lab notebook, ``to_json`` for the
  record.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..core.records import Result
from ..core.spec import SpecError, component_spec
from ..core.task import Task

RECOMMENDATION_SCHEMA: str = "cadaques.recommendation/1"


@dataclass(frozen=True)
class Candidate:
    """One recommended experiment, with the reasoning attached."""

    rank: int
    params: Mapping[str, float]
    predicted_value: float
    uncertainty: float
    acquisition: float
    predicted_cost: float
    score_per_cost: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "params": dict(self.params),
            "predicted_value": self.predicted_value,
            "uncertainty": self.uncertainty,
            "acquisition": self.acquisition,
            "predicted_cost": self.predicted_cost,
            "score_per_cost": self.score_per_cost,
        }


@dataclass(frozen=True)
class RankedCandidates:
    """A ranked recommendation: candidates plus full provenance."""

    candidates: tuple[Candidate, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def best(self) -> Candidate:
        return self.candidates[0]

    def summary(self) -> str:
        lines = [
            f"Ranked recommendation — {len(self)} candidates "
            f"(driver: {self.provenance.get('driver', {}).get('class', '?').rsplit('.', 1)[-1]}, "
            f"fitted on {self.provenance.get('history', {}).get('n_results', '?')} results, "
            f"thinking time {self.provenance.get('metering', {}).get('rank_seconds', 0.0):.3f} s)"
        ]
        for c in self.candidates:
            params = ", ".join(f"{k}={v:.4g}" for k, v in c.params.items())
            lines.append(
                f"  #{c.rank}: {params} | predicted={c.predicted_value:.4g} "
                f"± {c.uncertainty:.2g} | cost≈{c.predicted_cost:.4g} "
                f"| score/cost={c.score_per_cost:.3g}"
            )
        return "\n".join(lines)

    # -- persistence ----------------------------------------------------
    def to_json(self, path: str | Path) -> Path:
        path = Path(path)
        payload = {
            "schema": RECOMMENDATION_SCHEMA,
            "provenance": dict(self.provenance),
            "candidates": [c.as_dict() for c in self.candidates],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def to_csv(self, path: str | Path) -> Path:
        """The lab-notebook export: one row per recommended experiment."""
        import csv

        path = Path(path)
        param_names = sorted(self.candidates[0].params) if self.candidates else []
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["rank", *param_names, "predicted_value", "uncertainty",
                 "acquisition", "predicted_cost", "score_per_cost"]
            )
            for c in self.candidates:
                writer.writerow(
                    [c.rank, *[c.params[p] for p in param_names],
                     c.predicted_value, c.uncertainty, c.acquisition,
                     c.predicted_cost, c.score_per_cost]
                )
        return path


def _history_fingerprint(history: Sequence[Result]) -> dict[str, Any]:
    hasher = hashlib.sha256()
    for r in history:
        hasher.update(
            json.dumps(
                [sorted(r.query.params.items()), r.value, r.status],
                sort_keys=True, default=float,
            ).encode()
        )
    return {
        "n_results": len(history),
        "n_ok": sum(1 for r in history if getattr(r, "ok", True)),
        "sha256": hasher.hexdigest(),
    }


def recommend(
    source: Any,
    driver: Any = None,
    *,
    k: int = 10,
    task: Task | None = None,
    pool: Any = None,
    pool_size: int = 1024,
    exclude_measured: bool = True,
    seed: int | None = None,
) -> RankedCandidates:
    """Rank the next experiments from a measured history.

    Parameters
    ----------
    source:
        A :class:`~cadaques.oracles.dataset.DatasetOracle` (its table
        becomes the history) or a sequence of :class:`Result`.
    driver:
        A driver implementing ``rank(history, k, pool_size=...)``.
        Defaults to a cost-aware
        :class:`~cadaques.drivers.bo.BayesianDriver` over the data
        bounds (requires the ``[bo]`` extra).
    k:
        Number of candidates to return.
    task:
        Optional Task; its constraints filter the pool, and its
        direction configures the default driver.
    pool:
        Optional finite candidate library: an array of shape
        ``(n, n_parameters)`` in sorted parameter order, or a sequence
        of parameter dicts. When given, no sampling occurs — the
        ranking chooses among exactly these candidates (a compound
        catalog, a stock list, a design grid).
    pool_size:
        Candidate pool sampled inside the space when no ``pool`` is
        supplied.
    exclude_measured:
        Drop candidates already present in the successful history
        before ranking (default). Advice that re-runs a measured
        experiment is useless for a finite library — but replicates
        are legitimate science, so pass ``False`` to allow them.
        Failed points (e.g. retryable errors, dataset misses) are
        never excluded: they were not successfully measured. With a
        sampled continuous pool this filter is a no-op in practice
        (exact float collisions have measure zero).
    seed:
        Reseeds the driver (ADR-0012); same inputs + seed → same
        ranking.
    """
    # -- history --------------------------------------------------------
    if hasattr(source, "as_history"):
        history: list[Result] = list(source.as_history())
        bounds = dict(source.bounds)
    else:
        history = list(source)
        if not history:
            raise ValueError("recommend() needs a non-empty history")
        names = sorted(history[0].query.params)
        bounds = {
            n: (
                min(float(r.query.params[n]) for r in history),
                max(float(r.query.params[n]) for r in history),
            )
            for n in names
        }

    # -- driver ----------------------------------------------------------
    if driver is None:
        try:
            from ..drivers.bo import BayesianDriver
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "recommend() defaults to BayesianDriver; install the extra: "
                "pip install 'cadaques[bo]' — or pass a driver with rank()."
            ) from exc
        driver = BayesianDriver(
            space=bounds,
            maximize=task.maximize if task is not None else True,
        )
    rank_fn = getattr(driver, "rank", None)
    if not callable(rank_fn):
        raise TypeError(
            f"{type(driver).__name__} does not implement rank(); "
            "recommendation mode needs a ranking-capable driver."
        )
    seed_children = np.random.SeedSequence(seed).spawn(2) if seed is not None else None
    if seed_children is not None and callable(getattr(driver, "reseed", None)):
        driver.reseed(np.random.default_rng(seed_children[0]))

    # -- pool policy: all filtering happens BEFORE ranking -----------------
    # Candidates violating the task, and (by default) candidates already
    # successfully measured, are never scored; the driver receives the
    # filtered pool in canonical sorted-parameter column order (ADR-0012).
    names = sorted(bounds)

    if pool is not None:
        rows = list(pool)
        if rows and isinstance(rows[0], Mapping):
            missing = [n for n in names if n not in rows[0]]
            if missing:
                raise ValueError(f"pool dicts lack parameters {missing}")
            pool = np.array([[float(row[n]) for n in names] for row in rows])
        else:
            pool = np.asarray(pool, dtype=float)
        if pool.ndim != 2 or pool.shape[1] != len(names):
            raise ValueError(
                "pool must have shape (n_candidates, n_parameters) "
                f"with parameter order {tuple(names)}"
            )
        if task is not None and task.constraints:
            keep = [
                i for i in range(len(pool))
                if all(
                    con.satisfied({n: float(v) for n, v in zip(names, pool[i])})
                    for con in task.constraints
                )
            ]
            pool = pool[keep]
    elif task is not None and task.constraints:
        pool_rng = (
            np.random.default_rng(seed_children[1])
            if seed_children is not None
            else np.random.default_rng()
        )
        lows = np.array([bounds[n][0] for n in names])
        highs = np.array([bounds[n][1] for n in names])
        kept: list[np.ndarray] = []
        for _ in range(50):  # resample until the pool fills or we give up
            batch = pool_rng.uniform(lows, highs, size=(pool_size, len(names)))
            for row in batch:
                params = {n: float(v) for n, v in zip(names, row)}
                if all(con.satisfied(params) for con in task.constraints):
                    kept.append(row)
            if len(kept) >= pool_size:
                break
        if len(kept) < k:
            raise ValueError(
                "Could not sample enough candidates satisfying the task "
                "constraints; enlarge the pool or relax the constraints."
            )
        pool = np.array(kept[:pool_size])

    # -- exclusion of already-measured candidates (policy, not mechanism) --
    if exclude_measured and pool is not None and len(pool):
        measured = np.array(
            [
                [float(res.query.params[n]) for n in names]
                for res in history
                if getattr(res, "ok", True) and all(n in res.query.params for n in names)
            ]
        )
        if len(measured):
            already = np.any(
                np.all(
                    np.isclose(pool[:, None, :], measured[None, :, :],
                               rtol=1e-12, atol=1e-12),
                    axis=2,
                ),
                axis=1,
            )
            pool = pool[~already]
    if pool is not None and len(pool) == 0:
        raise ValueError(
            "Every candidate in the supplied pool was filtered out "
            "(constraints and/or already measured). Enlarge the pool, relax "
            "the constraints, or pass exclude_measured=False to allow "
            "replicates."
        )

    # -- rank (metered: the price of intelligence) ------------------------
    t0 = time.perf_counter()
    raw = rank_fn(history, k, pool=pool, pool_size=pool_size)
    rank_seconds = time.perf_counter() - t0
    if not raw:
        raise ValueError("The driver returned an empty ranking.")

    # -- provenance --------------------------------------------------------
    try:
        driver_decl: Mapping[str, Any] = component_spec(driver)
    except SpecError:
        driver_decl = {"class": f"{type(driver).__module__}.{type(driver).__qualname__}",
                       "params": "<not spec-serializable>"}
    provenance = {
        "schema": RECOMMENDATION_SCHEMA,
        "driver": driver_decl,
        "history": _history_fingerprint(history),
        "bounds": {kk: list(v) for kk, v in bounds.items()},
        "task": task.name if task is not None else None,
        "seed": seed,
        "k": k,
        "pool_size": pool_size,
        "pool_supplied": pool is not None,
        "pool_n_after_filters": int(len(pool)) if pool is not None else None,
        "exclude_measured": exclude_measured,
        "metering": {"rank_seconds": rank_seconds},
        "created_at": time.time(),
    }

    candidates = tuple(
        Candidate(
            rank=i + 1,
            params=c["params"],
            predicted_value=c["predicted_value"],
            uncertainty=c["uncertainty"],
            acquisition=c["acquisition"],
            predicted_cost=c["predicted_cost"],
            score_per_cost=c["score_per_cost"],
        )
        for i, c in enumerate(raw)
    )
    return RankedCandidates(candidates=candidates, provenance=provenance)
