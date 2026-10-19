"""Reference physics for the pc1d task: exact 1D gap by the transfer matrix.

Pure standard library so the grader runs anywhere. ``gap_ratio(f)`` is
Δω/ω_m of the fundamental gap at normal incidence for d_H/a = f; it
agrees with ``reference/qw_check.py`` (SciPy) to better than 1e-6.
"""

from __future__ import annotations

import math

EPS_H, EPS_L = 13.0, 1.0
N_H, N_L = math.sqrt(EPS_H), math.sqrt(EPS_L)
D_OPT = N_L / (N_H + N_L)
GAP_OPT = 4 / math.pi * math.asin((N_H - N_L) / (N_H + N_L))


def _trace(w: float, f: float) -> float:
    ph, pl = 2 * math.pi * w * N_H * f, 2 * math.pi * w * N_L * (1 - f)
    return (math.cos(ph) * math.cos(pl)
            - 0.5 * (N_H / N_L + N_L / N_H) * math.sin(ph) * math.sin(pl))


def _edge(f: float, a: float, b: float) -> float:
    """Bisection on |trace| = 1 between a (inside one regime) and b."""
    ga = abs(_trace(a, f)) - 1
    for _ in range(80):
        m = 0.5 * (a + b)
        gm = abs(_trace(m, f)) - 1
        if (gm > 0) == (ga > 0):
            a, ga = m, gm
        else:
            b = m
    return 0.5 * (a + b)


def gap_ratio(f: float, samples: int = 20000) -> float:
    if not 0.0 < f < 1.0:
        return 0.0
    w_max = 1.0 / (N_H * f + N_L * (1 - f))  # second Bragg frequency
    step = w_max / samples
    prev_in, lo = False, None
    for i in range(1, samples + 1):
        w = i * step
        inside = abs(_trace(w, f)) > 1
        if inside and not prev_in:
            lo = _edge(f, w, w - step)
        elif not inside and prev_in:
            hi = _edge(f, w - step, w)
            return 2 * (hi - lo) / (hi + lo)
        prev_in = inside
    return 0.0
