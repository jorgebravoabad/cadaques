"""One audited statistics module for every comparison the programme makes.

Reproducible evaluation ends at the confidence interval: every
comparative claim in every paper built on CADAQUES should run through
this module rather than per-notebook code (three-year strategy, A3).
Two primitives and one convenience:

* :func:`paired_bootstrap_ci` — bootstrap confidence interval for the
  mean paired difference.
* :func:`wilcoxon_signed_rank` — the Wilcoxon signed-rank test,
  implemented on numpy alone (ADR-0008): exact null distribution up to
  n = 25, normal approximation with tie correction beyond.
* :func:`compare` / :func:`paired_campaigns` — run two campaign
  factories over shared seeds and report the full paired analysis.

Pairing is by seed: method A and method B face the same campaign seed,
so the difference isolates the method (ADR-0012 is what makes this
valid).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

__all__ = [
    "Comparison",
    "compare",
    "paired_bootstrap_ci",
    "paired_campaigns",
    "wilcoxon_signed_rank",
]


# ------------------------------------------------------------- bootstrap
def paired_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    rng: np.random.Generator | None = None,
) -> tuple[float, float, float]:
    """Mean paired difference ``a - b`` with a bootstrap CI.

    Returns ``(mean_diff, ci_low, ci_high)`` at level ``1 - alpha``
    (percentile method on resampled pairs).
    """
    a_arr, b_arr = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a_arr.shape != b_arr.shape or a_arr.ndim != 1:
        raise ValueError("a and b must be 1-D sequences of equal length")
    if len(a_arr) < 2:
        raise ValueError("need at least two pairs")
    rng = rng or np.random.default_rng(0)
    diff = a_arr - b_arr
    idx = rng.integers(0, len(diff), size=(n_boot, len(diff)))
    boot_means = diff[idx].mean(axis=1)
    lo, hi = np.quantile(boot_means, [alpha / 2, 1 - alpha / 2])
    return float(diff.mean()), float(lo), float(hi)


# -------------------------------------------------------------- wilcoxon
def _exact_wilcoxon_sf(w: float, n: int) -> float:
    """P(W+ >= w) under the exact null (no ties), by dynamic programming."""
    # distribution of W+ = sum of a random subset of ranks 1..n
    counts = np.zeros(n * (n + 1) // 2 + 1, dtype=float)
    counts[0] = 1.0
    total = 0
    for rank in range(1, n + 1):
        counts[rank : total + rank + 1] += counts[0 : total + 1]
        total += rank
    counts /= counts.sum()
    w_ceil = int(np.ceil(w - 1e-12))
    return float(counts[w_ceil:].sum())


def wilcoxon_signed_rank(
    a: Sequence[float],
    b: Sequence[float],
    *,
    alternative: str = "two-sided",
) -> tuple[float, float]:
    """Wilcoxon signed-rank test for paired samples.

    Returns ``(W_plus, p_value)``. Zero differences are discarded
    (Wilcoxon's convention); ties among ``|differences|`` receive average
    ranks. Exact null distribution for n <= 25 without ties; normal
    approximation with tie correction and continuity correction
    otherwise. ``alternative``: ``"two-sided"``, ``"greater"``
    (a tends to exceed b) or ``"less"``.
    """
    if alternative not in ("two-sided", "greater", "less"):
        raise ValueError(f"Unknown alternative {alternative!r}")
    a_arr, b_arr = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a_arr.shape != b_arr.shape or a_arr.ndim != 1:
        raise ValueError("a and b must be 1-D sequences of equal length")
    diff = a_arr - b_arr
    diff = diff[diff != 0.0]
    n = len(diff)
    if n == 0:
        return 0.0, 1.0

    abs_d = np.abs(diff)
    order = np.argsort(abs_d, kind="stable")
    ranks = np.empty(n, dtype=float)
    sorted_abs = abs_d[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_abs[j + 1] == sorted_abs[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0  # average rank
        i = j + 1

    w_plus = float(ranks[diff > 0].sum())
    has_ties = len(np.unique(abs_d)) < n

    if n <= 25 and not has_ties:
        sf = _exact_wilcoxon_sf(w_plus, n)                     # P(W+ >= w)
        cdf = 1.0 - _exact_wilcoxon_sf(w_plus + 1.0, n)        # P(W+ <= w)
        if alternative == "greater":
            p = sf
        elif alternative == "less":
            p = cdf
        else:
            p = min(1.0, 2.0 * min(sf, cdf))
        return w_plus, float(p)

    mean = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0
    # tie correction
    _, tie_counts = np.unique(abs_d, return_counts=True)
    var -= float(np.sum(tie_counts**3 - tie_counts)) / 48.0
    sd = np.sqrt(max(var, 1e-12))

    def sf_normal(z: float) -> float:
        from math import erfc

        return 0.5 * erfc(z / np.sqrt(2.0))

    if alternative == "greater":
        p = sf_normal((w_plus - mean - 0.5) / sd)
    elif alternative == "less":
        p = sf_normal((mean - w_plus - 0.5) / sd)
    else:
        z = (abs(w_plus - mean) - 0.5) / sd
        p = min(1.0, 2.0 * sf_normal(z))
    return w_plus, float(p)


# ------------------------------------------------------------ comparison
@dataclass(frozen=True)
class Comparison:
    """A paired method comparison, ready to be quoted in a paper."""

    metric: str
    seeds: tuple[int, ...]
    a: tuple[float, ...]
    b: tuple[float, ...]
    mean_a: float
    mean_b: float
    mean_diff: float
    ci_low: float
    ci_high: float
    w_statistic: float
    p_value: float

    @property
    def significant(self) -> bool:
        """CI excludes zero (at the alpha the CI was built with)."""
        return self.ci_low > 0.0 or self.ci_high < 0.0

    def summary(self) -> str:
        return (
            f"{self.metric}: A={self.mean_a:.4g} vs B={self.mean_b:.4g} | "
            f"diff={self.mean_diff:+.4g} CI[{self.ci_low:+.4g}, {self.ci_high:+.4g}] | "
            f"Wilcoxon W+={self.w_statistic:.1f}, p={self.p_value:.4g} | "
            f"n={len(self.seeds)} paired seeds"
        )


def compare(
    a: Sequence[float],
    b: Sequence[float],
    *,
    seeds: Sequence[int] = (),
    metric: str = "metric",
    n_boot: int = 10_000,
    alpha: float = 0.05,
    alternative: str = "two-sided",
    rng: np.random.Generator | None = None,
) -> Comparison:
    """Full paired analysis of two per-seed metric vectors."""
    mean_diff, lo, hi = paired_bootstrap_ci(a, b, n_boot=n_boot, alpha=alpha, rng=rng)
    w, p = wilcoxon_signed_rank(a, b, alternative=alternative)
    return Comparison(
        metric=metric,
        seeds=tuple(int(s) for s in seeds) or tuple(range(len(a))),
        a=tuple(float(x) for x in a),
        b=tuple(float(x) for x in b),
        mean_a=float(np.mean(a)),
        mean_b=float(np.mean(b)),
        mean_diff=mean_diff,
        ci_low=lo,
        ci_high=hi,
        w_statistic=w,
        p_value=p,
    )


def paired_campaigns(
    make_a: Callable[[int], "object"],
    make_b: Callable[[int], "object"],
    *,
    seeds: Sequence[int],
    metric: Callable[[object], float],
    metric_name: str = "best_value",
    run_kwargs: Mapping[str, object] | None = None,
    alternative: str = "two-sided",
) -> Comparison:
    """Run two campaign factories over shared seeds; compare paired.

    ``make_a(seed)`` / ``make_b(seed)`` build fresh campaigns;
    ``metric(outcome)`` extracts the per-seed number (e.g.
    ``lambda o: o.best.value`` or ``lambda o: o.state.spent.seconds``).
    Benchmarking is campaigning: every cell is a full Campaign.
    """
    kwargs = dict(run_kwargs or {})
    a_vals = [float(metric(make_a(int(s)).run(**kwargs))) for s in seeds]
    b_vals = [float(metric(make_b(int(s)).run(**kwargs))) for s in seeds]
    return compare(
        a_vals, b_vals, seeds=seeds, metric=metric_name, alternative=alternative
    )
