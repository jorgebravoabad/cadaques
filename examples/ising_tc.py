"""Canonical CADAQUES campaign: locate the Ising critical temperature.

Two drivers — budget-oblivious random search and a budget-aware
annealed local search — receive *identical* compute budgets to locate
the critical temperature of the 2D Ising model by maximizing the
magnetic susceptibility. Onsager's exact T_c ≈ 2.269 provides the
ground truth. This is the miniature of every CADAQUES benchmark:
same oracle, same budget, drivers compared per unit cost.

Run:  python examples/ising_tc.py
"""

from __future__ import annotations

from cadaques import Budget, Campaign, Cost
from cadaques.drivers import AnnealedLocalDriver, RandomDriver
from cadaques.oracles import Ising2DOracle, T_C_EXACT

SPACE = {"T": (1.5, 3.5)}
FIDELITY = {"L": 24, "sweeps": 400, "equilibration": 200}
BUDGET_SECONDS = 25.0
SEED = 2026


def run_campaign(driver_name: str) -> None:
    oracle = Ising2DOracle(seed=SEED)
    driver = {
        "random": RandomDriver(SPACE, fidelity=FIDELITY, seed=SEED),
        "annealed": AnnealedLocalDriver(SPACE, fidelity=FIDELITY, seed=SEED),
    }[driver_name]

    campaign = Campaign(oracle, driver, Budget(total=Cost(seconds=BUDGET_SECONDS)))
    outcome = campaign.run()

    best_T = outcome.best.query.params["T"]
    ledger = outcome.ledger
    print(f"\n=== {type(driver).__name__} ===")
    print(f"  queries executed : {outcome.n_queries}  (stop: {outcome.stop_reason})")
    print(f"  best T           : {best_T:.4f}")
    print(f"  |T - T_c exact|  : {abs(best_T - T_C_EXACT):.4f}   (T_c = {T_C_EXACT:.4f})")
    print(f"  susceptibility   : {outcome.best.value:.1f}")
    print(f"  oracle spend     : {ledger.total('oracle').seconds:.2f} s "
          f"(declared vs settled visible in ledger)")
    print(f"  driver spend     : {ledger.total('driver').seconds * 1e3:.2f} ms")
    ledger_path = ledger.to_jsonl(f"campaign_{driver_name}.jsonl")
    print(f"  ledger           : {ledger_path} ({len(ledger)} transactions)")


if __name__ == "__main__":
    print(f"CADAQUES demo — Ising 2D critical temperature under a "
          f"{BUDGET_SECONDS:.0f}-second budget")
    for name in ("random", "annealed"):
        run_campaign(name)
