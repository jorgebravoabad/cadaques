"""Cost-aware Bayesian optimization behind ``pip install cadaques[bo]``.

The reference intelligent driver: a Gaussian-process surrogate with
Expected Improvement, made budget- and cost-aware in the two ways the
framework's economics permit *through the view boundary* (ADR-0011 —
a driver never sees the ledger, only its own history and a read-only
``BudgetView``):

* **Expected improvement per unit cost** (Snoek et al., 2012): a
  second GP is fitted to the *settled* costs of past results, and the
  acquisition becomes EI divided by the predicted cost of each
  candidate — the driver learns the price landscape from the same
  history it learns the objective from. The currency is declared
  (``cost_currency``); set ``cost_aware=False`` for plain EI.

* **Budget-annealed exploration**: the exploration margin ``xi``
  shrinks with ``budget.fraction_used`` — explore while rich, exploit
  while poor, the house strategy of
  :class:`~cadaques.drivers.reference.AnnealedLocalDriver` expressed
  in acquisition space.

Failed results train neither surrogate (their value is NaN and their
cost was a failure's cost), but they *were* charged — the driver sees
that through the budget, exactly as a human scientist would.

scikit-learn stays out of core (ADR-0008): importing this module
without it raises a helpful error naming the extra.
"""

from __future__ import annotations

import warnings

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from ..core.cost import BudgetView
from ..core.records import Query, Result

try:  # pragma: no cover - exercised only when the extra is missing
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "BayesianDriver requires scikit-learn. Install the extra: "
        "pip install 'cadaques[bo]'"
    ) from _exc

SearchSpace = Mapping[str, tuple[float, float]]

_SQRT2 = float(np.sqrt(2.0))


def _norm_pdf(z: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z**2) / np.sqrt(2.0 * np.pi)


def _norm_cdf(z: np.ndarray) -> np.ndarray:
    from math import erf

    return 0.5 * (1.0 + np.vectorize(erf)(z / _SQRT2))


@dataclass
class BayesianDriver:
    """GP + Expected Improvement, cost-aware and budget-annealed.

    Parameters
    ----------
    space:
        Box bounds per parameter (the 0.1 mapping form).
    fidelity:
        Fidelity knobs attached to every proposal.
    n_initial:
        Random proposals before the first surrogate fit.
    n_candidates:
        Size of the uniform candidate pool scored per proposal.
    xi_max, xi_min:
        Exploration margin at fraction_used = 0 and 1 (annealed
        linearly in between).
    cost_aware:
        Divide EI by a GP prediction of settled cost (EI per unit
        cost). With uniform costs this reduces to plain EI.
    cost_currency:
        Which settled currency the cost surrogate learns.
    maximize:
        Direction of the objective.
    seed:
        Own stream; a campaign seed overrides it via ``reseed``.
    """

    space: SearchSpace
    fidelity: Mapping[str, int] = field(default_factory=dict)
    n_initial: int = 5
    n_candidates: int = 256
    xi_max: float = 0.05
    xi_min: float = 0.001
    cost_aware: bool = True
    cost_currency: str = "seconds"
    maximize: bool = True
    seed: int | None = None

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self._names = tuple(sorted(self.space))  # ADR-0012: order-canonical

    def reseed(self, rng: np.random.Generator) -> None:
        """Adopt a campaign-provided stream (ADR-0012)."""
        self._rng = rng

    # ------------------------------------------------------------ helpers
    def _lows_highs(self) -> tuple[np.ndarray, np.ndarray]:
        lows = np.array([self.space[k][0] for k in self._names], dtype=float)
        highs = np.array([self.space[k][1] for k in self._names], dtype=float)
        return lows, highs

    def _sample(self, n: int) -> np.ndarray:
        lows, highs = self._lows_highs()
        return self._rng.uniform(lows, highs, size=(n, len(self._names)))

    def _to_query(self, x: np.ndarray) -> Query:
        return Query(
            params={k: float(v) for k, v in zip(self._names, x)},
            fidelity=dict(self.fidelity),
        )

    def _training_set(
        self, history: Sequence[Result]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ok = [r for r in history if getattr(r, "ok", True)]
        X = np.array(
            [[float(r.query.params[k]) for k in self._names] for r in ok], dtype=float
        )
        y = np.array([r.value for r in ok], dtype=float)
        c = np.array(
            [max(getattr(r.cost, self.cost_currency, 0.0), 1e-12) for r in ok],
            dtype=float,
        )
        return X, y, c

    def _fit_gp(self, X: np.ndarray, y: np.ndarray) -> GaussianProcessRegressor:
        lows, highs = self._lows_highs()
        span = np.maximum(highs - lows, 1e-12)
        kernel = ConstantKernel(1.0, (1e-3, 1e3)) * Matern(
            length_scale=span / 4.0,
            length_scale_bounds=(1e-3, 1e3),
            nu=2.5,
        ) + WhiteKernel(1e-6, (1e-10, 1e-1))
        gp = GaussianProcessRegressor(
            kernel=kernel,
            normalize_y=True,
            n_restarts_optimizer=1,
            random_state=int(self._rng.integers(0, 2**31 - 1)),
        )
        with warnings.catch_warnings():
            from sklearn.exceptions import ConvergenceWarning

            warnings.simplefilter("ignore", ConvergenceWarning)
            gp.fit(X, y)
        return gp

    # ------------------------------------------------------------ Driver
    def propose(self, history: Sequence[Result], budget: BudgetView) -> Query:
        X, y, costs = self._training_set(history)
        if len(y) < self.n_initial:
            return self._to_query(self._sample(1)[0])

        # objective surrogate (internally maximized: flip sign if minimizing)
        y_fit = y if self.maximize else -y
        gp = self._fit_gp(X, y_fit)

        frac = min(max(budget.fraction_used, 0.0), 1.0)
        xi = self.xi_max + (self.xi_min - self.xi_max) * frac

        candidates = self._sample(self.n_candidates)
        mu, sigma = gp.predict(candidates, return_std=True)
        sigma = np.maximum(sigma, 1e-12)
        best = float(np.max(y_fit))
        z = (mu - best - xi) / sigma
        ei = (mu - best - xi) * _norm_cdf(z) + sigma * _norm_pdf(z)
        ei = np.maximum(ei, 0.0)

        predicted_cost = np.ones_like(ei)
        if self.cost_aware and len(np.unique(costs)) > 1:
            cost_gp = self._fit_gp(X, np.log(costs))
            predicted_cost = np.maximum(np.exp(cost_gp.predict(candidates)), 1e-12)

        acquisition = ei / predicted_cost
        if not np.any(acquisition > 0):
            # Flat EI (e.g. a nearly-explained landscape): explore where
            # uncertainty per unit predicted cost is highest — the fallback
            # stays cost-aware instead of reverting to blind sampling.
            acquisition = sigma / predicted_cost
        return self._to_query(candidates[int(np.argmax(acquisition))])

    def rank(
        self,
        history: Sequence[Result],
        k: int = 10,
        *,
        pool: np.ndarray | None = None,
        pool_size: int | None = None,
    ) -> list[dict]:
        """Score a candidate pool against the full history; return the
        top-``k`` as dicts with params, prediction, uncertainty,
        acquisition, predicted cost and score per unit cost.

        This is recommendation mode: the surrogates fit on *all* the
        (successful) history, the pool is sampled inside the space
        (or supplied), and nothing is executed — the ranked list is
        advice for the next real experiments. Deterministic given the
        driver's rng state (ADR-0012).
        """
        X, y, costs = self._training_set(history)
        if len(y) < 2:
            raise ValueError("rank() needs at least two successful results to fit on")
        y_fit = y if self.maximize else -y
        gp = self._fit_gp(X, y_fit)

        n = pool_size or max(self.n_candidates, 4 * k)
        candidates = pool if pool is not None else self._sample(n)

        mu, sigma = gp.predict(candidates, return_std=True)
        sigma = np.maximum(sigma, 1e-12)
        best = float(np.max(y_fit))
        z = (mu - best - self.xi_min) / sigma
        ei = np.maximum((mu - best - self.xi_min) * _norm_cdf(z) + sigma * _norm_pdf(z), 0.0)

        predicted_cost = np.ones_like(ei)
        if self.cost_aware and len(np.unique(costs)) > 1:
            cost_gp = self._fit_gp(X, np.log(costs))
            predicted_cost = np.maximum(np.exp(cost_gp.predict(candidates)), 1e-12)
        score = ei / predicted_cost
        if not np.any(score > 0):
            score = sigma / predicted_cost  # flat-EI fallback, still cost-aware

        order = np.argsort(-score)[: int(k)]
        out = []
        for idx in order:
            i = int(idx)
            out.append(
                {
                    "params": {name: float(v) for name, v in zip(self._names, candidates[i])},
                    "predicted_value": float(mu[i] if self.maximize else -mu[i]),
                    "uncertainty": float(sigma[i]),
                    "acquisition": float(ei[i]),
                    "predicted_cost": float(predicted_cost[i]),
                    "score_per_cost": float(score[i]),
                }
            )
        return out

    def observe(self, result: Result) -> None:  # history arrives via propose()
        pass
