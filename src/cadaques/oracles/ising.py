"""Canonical illustrative oracle: the two-dimensional Ising model.

The campaign it supports is a textbook discovery task with an exact
analytic answer: locate the critical temperature of the 2D Ising
model by maximizing the magnetic susceptibility. Onsager (1944):

    T_c = 2 / ln(1 + sqrt(2)) ≈ 2.269185  (J = k_B = 1)

The oracle is a checkerboard-Metropolis Monte Carlo simulation whose
*fidelity knobs* — lattice size ``L`` and number of measurement
``sweeps`` — carry genuine, heterogeneous cost: accuracy is bought
with compute. This makes it a faithful miniature of real discovery
oracles, where every answer has a price and better answers cost more.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..core.cost import Cost
from ..core.protocols import Query, Result

#: Onsager's exact critical temperature (J = k_B = 1).
T_C_EXACT: float = 2.0 / np.log(1.0 + np.sqrt(2.0))


@dataclass
class Ising2DOracle:
    """Metered Monte Carlo oracle for the 2D Ising model.

    Parameters
    ----------
    default_L, default_sweeps, default_equilibration:
        Fidelity defaults used when a query does not specify them.
    seconds_per_spin_sweep:
        Ex-ante cost model: declared price is proportional to
        ``L**2 * (equilibration + sweeps)``. The settled cost inside
        each Result is the *measured* wall time, so declared/settled
        discrepancies are visible in the ledger by construction.
    seed:
        Base seed; each evaluation derives an independent stream.
    """

    default_L: int = 24
    default_sweeps: int = 400
    default_equilibration: int = 200
    seconds_per_spin_sweep: float = 2.5e-8
    seed: int | None = None

    def __post_init__(self) -> None:
        self._seed_sequence = np.random.SeedSequence(self.seed)

    # -- helpers ------------------------------------------------------
    def _fidelity(self, query: Query) -> tuple[int, int, int]:
        fid = query.fidelity
        L = int(fid.get("L", self.default_L))
        sweeps = int(fid.get("sweeps", self.default_sweeps))
        equilibration = int(fid.get("equilibration", self.default_equilibration))
        if L < 2 or sweeps < 1 or equilibration < 0:
            raise ValueError(f"Invalid fidelity: L={L}, sweeps={sweeps}, eq={equilibration}")
        return L, sweeps, equilibration

    # -- Oracle protocol ----------------------------------------------
    def price(self, query: Query) -> Cost:
        L, sweeps, equilibration = self._fidelity(query)
        seconds = self.seconds_per_spin_sweep * L * L * (sweeps + equilibration)
        return Cost(seconds=seconds, cpu_hours=seconds / 3600.0)

    def evaluate(self, query: Query) -> Result:
        T = float(query.params["T"])
        if T <= 0:
            raise ValueError(f"Temperature must be positive, got T={T}")
        L, sweeps, equilibration = self._fidelity(query)
        rng = np.random.default_rng(self._seed_sequence.spawn(1)[0])

        t0 = time.perf_counter()
        chi, abs_m = _susceptibility(T=T, L=L, sweeps=sweeps, equilibration=equilibration, rng=rng)
        elapsed = time.perf_counter() - t0

        settled = Cost(seconds=elapsed, cpu_hours=elapsed / 3600.0)
        return Result(
            query=query,
            value=chi,
            cost=settled,
            info={"abs_magnetization": abs_m, "L": L, "sweeps": sweeps, "T": T},
        )


# ----------------------------------------------------------------------
# Vectorized checkerboard Metropolis
# ----------------------------------------------------------------------

def _neighbor_sum(spins: np.ndarray) -> np.ndarray:
    return (
        np.roll(spins, 1, axis=0)
        + np.roll(spins, -1, axis=0)
        + np.roll(spins, 1, axis=1)
        + np.roll(spins, -1, axis=1)
    )


def _sweep(spins: np.ndarray, beta: float, masks: tuple[np.ndarray, np.ndarray], rng: np.random.Generator) -> None:
    """One full Metropolis sweep via two checkerboard half-updates."""
    for mask in masks:
        delta_e = 2.0 * spins * _neighbor_sum(spins)
        accept = (delta_e <= 0) | (rng.random(spins.shape) < np.exp(-beta * delta_e))
        spins[mask & accept] *= -1


def _susceptibility(
    *, T: float, L: int, sweeps: int, equilibration: int, rng: np.random.Generator
) -> tuple[float, float]:
    """Magnetic susceptibility per spin, chi = beta * N * (<m^2> - <|m|>^2)."""
    beta = 1.0 / T
    spins = rng.choice(np.array([-1, 1], dtype=np.int8), size=(L, L)).astype(np.int8)

    ii, jj = np.indices((L, L))
    checkerboard = (ii + jj) % 2 == 0
    masks = (checkerboard, ~checkerboard)

    for _ in range(equilibration):
        _sweep(spins, beta, masks, rng)

    m_samples = np.empty(sweeps)
    for k in range(sweeps):
        _sweep(spins, beta, masks, rng)
        m_samples[k] = spins.mean()

    abs_m = float(np.abs(m_samples).mean())
    chi = beta * L * L * float((m_samples**2).mean() - abs_m**2)
    return chi, abs_m
