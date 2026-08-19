"""Study v1 — the frozen protocol runner for the 0.3 vertical paper.

Four datasets exactly, twenty seeds, four drivers, per-unit-settled-cost
reporting through cadaques.stats. Every cell is a full, spec'd,
replayable Campaign; every comparative number in the paper traces to
this file and the pinned data under studies/data/ (see
studies/README.md for the frozen protocol and provenance).

Usage:
    python studies/run_study.py --smoke          # 6 seeds, reduced budgets (CI-sized)
    python studies/run_study.py                  # the full frozen matrix (paper)
    python studies/run_study.py --datasets snar fullerenes

Outputs (studies/results/):
    results_<dataset>.csv       per-seed, per-driver raw metrics
    comparisons_<dataset>.txt   paired analyses (bootstrap CI + Wilcoxon)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from cadaques import Budget, Campaign, Cost, Task  # noqa: E402
from cadaques.drivers import AnnealedLocalDriver, BayesianDriver, RandomDriver  # noqa: E402
from cadaques.oracles import Ising2DOracle  # noqa: E402
from cadaques.stats import compare  # noqa: E402
from cadaques.verticals import ReactionTable  # noqa: E402
from ocm.featurize import load_ocm291  # noqa: E402

# ----------------------------------------------------------------- protocol
SEEDS_FULL = tuple(range(20))
SEEDS_SMOKE = tuple(range(6))

DATA = HERE / "data"
RESULTS = HERE / "results"


def _reaction_table(csv_name: str, **kw) -> ReactionTable:
    rows = []
    for raw in csv.DictReader(open(DATA / csv_name, encoding="utf-8")):
        rows.append({k: float(v) for k, v in raw.items()})
    return ReactionTable(rows=rows, **kw)


def dataset_ising():
    """Controlled ground truth (in-package): budget in abstract seconds."""
    task = Task.from_bounds({"T": (1.0, 4.0)}, name="ising")
    def build(driver, seed, budget_s):
        oracle = Ising2DOracle(default_L=8, default_sweeps=40, default_equilibration=20)
        return Campaign(oracle, driver, Budget(total=Cost(seconds=budget_s)),
                        task=task, seed=seed, meter_driver=False)
    return {"name": "ising", "space": {"T": (1.0, 4.0)}, "build": build,
            "budget_full": 1e9, "budget_smoke": 1e9,
            "max_queries_full": 40, "max_queries_smoke": 15, "maximize": True}


def dataset_ocm291():
    table = load_ocm291(DATA / "ocm291.csv")
    space = table.oracle().bounds
    def build(driver, seed, budget_s):
        return table.campaign(driver, Budget(total=Cost(seconds=budget_s)),
                              seed=seed, meter_driver=False)
    return {"name": "ocm291", "space": space, "build": build,
            "budget_full": 40.0, "budget_smoke": 15.0, "maximize": True}


def dataset_snar():
    # residence_time [min] is the real per-run cost: cost_s = 60 * residence_time
    rows = []
    for raw in csv.DictReader(open(DATA / "olympus_snar.csv", encoding="utf-8")):
        row = {k: float(v) for k, v in raw.items()}
        row["cost_s"] = 60.0 * row["residence_time"]
        rows.append(row)
    table = ReactionTable(
        rows=rows, objective="e_factor",
        conditions=("residence_time", "ratio", "concentration", "temperature"),
        direction="minimize", cost_columns={"seconds": "cost_s"},
        tariff={"seconds": 60.0 * 1.25}, tolerance=float("inf"), name="snar_olympus",
    )
    space = table.oracle().bounds
    def build(driver, seed, budget_s):
        return table.campaign(driver, Budget(total=Cost(seconds=budget_s)),
                              seed=seed, meter_driver=False)
    return {"name": "snar", "space": space, "build": build,
            "budget_full": 1800.0, "budget_smoke": 900.0, "maximize": False}


def dataset_fullerenes():
    # reaction_time [min] is the real per-run cost (3-31 min: 10x heterogeneity)
    rows = []
    for raw in csv.DictReader(open(DATA / "olympus_fullerenes.csv", encoding="utf-8")):
        row = {k: float(v) for k, v in raw.items()}
        row["cost_s"] = 60.0 * row["reaction_time"]
        rows.append(row)
    table = ReactionTable(
        rows=rows, objective="product_mole_percent",
        conditions=("reaction_time", "sultine", "temperature"),
        direction="maximize", cost_columns={"seconds": "cost_s"},
        tariff={"seconds": 60.0 * 17.0}, tolerance=float("inf"), name="fullerenes_olympus",
    )
    space = table.oracle().bounds
    def build(driver, seed, budget_s):
        return table.campaign(driver, Budget(total=Cost(seconds=budget_s)),
                              seed=seed, meter_driver=False)
    return {"name": "fullerenes", "space": space, "build": build,
            "budget_full": 18000.0, "budget_smoke": 7200.0, "maximize": True}


DATASETS = {
    "ising": dataset_ising,
    "ocm291": dataset_ocm291,
    "snar": dataset_snar,
    "fullerenes": dataset_fullerenes,
}


def drivers_for(space, maximize):
    return {
        "random": lambda: RandomDriver(space=space),
        "annealed": lambda: AnnealedLocalDriver(space=space),
        "bo_cost_aware": lambda: BayesianDriver(
            space=space, maximize=maximize, n_initial=5, n_candidates=256,
            cost_aware=True),
        "bo_cost_oblivious": lambda: BayesianDriver(
            space=space, maximize=maximize, n_initial=5, n_candidates=256,
            cost_aware=False),
    }


# ----------------------------------------------------------------- running
def run_matrix(spec, seeds, budget_key):
    space, build = spec["space"], spec["build"]
    budget = spec[budget_key]
    max_q = spec.get(budget_key.replace("budget", "max_queries"))
    drivers = drivers_for(space, spec["maximize"])
    records = []
    for driver_name, make_driver in drivers.items():
        for seed in seeds:
            t0 = time.perf_counter()
            out = build(make_driver(), int(seed), budget).run(max_queries=max_q)
            records.append({
                "dataset": spec["name"], "driver": driver_name, "seed": int(seed),
                "best_value": out.best.value if out.best else float("nan"),
                "n_queries": out.n_queries, "n_failures": out.n_failures,
                "settled_seconds": out.state.settled_oracle.seconds,
                "declared_seconds": out.state.declared_oracle.seconds,
                "overrun_seconds": out.state.overrun.seconds,
                "stop_reason": out.stop_reason,
                "wall_s": round(time.perf_counter() - t0, 2),
            })
            print(f"  {spec['name']:11s} {driver_name:17s} seed {seed:2d} "
                  f"best={records[-1]['best_value']:.4g} "
                  f"n={records[-1]['n_queries']} fail={records[-1]['n_failures']}")
    return records


def analyze(records, spec, out_path):
    drivers = sorted({r["driver"] for r in records})
    seeds = sorted({r["seed"] for r in records})
    series = {
        d: [next(r["best_value"] for r in records
                 if r["driver"] == d and r["seed"] == s) for s in seeds]
        for d in drivers
    }
    sign = 1.0 if spec["maximize"] else -1.0
    lines = [f"# {spec['name']} — paired comparisons (best value, "
             f"{'maximize' if spec['maximize'] else 'minimize'}; n={len(seeds)} seeds)"]
    baseline = "random"
    for d in drivers:
        if d == baseline:
            continue
        c = compare([sign * v for v in series[d]],
                    [sign * v for v in series[baseline]],
                    seeds=seeds, metric=f"{d} vs {baseline}",
                    alternative="greater")
        lines.append(c.summary())
    c = compare([sign * v for v in series["bo_cost_aware"]],
                [sign * v for v in series["bo_cost_oblivious"]],
                seeds=seeds, metric="bo_cost_aware vs bo_cost_oblivious",
                alternative="two-sided")
    lines.append(c.summary())
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="6 seeds, reduced budgets")
    parser.add_argument("--datasets", nargs="*", default=list(DATASETS))
    args = parser.parse_args()

    seeds = SEEDS_SMOKE if args.smoke else SEEDS_FULL
    budget_key = "budget_smoke" if args.smoke else "budget_full"
    RESULTS.mkdir(exist_ok=True)

    for name in args.datasets:
        spec = DATASETS[name]()
        print(f"\n=== {name} ({'smoke' if args.smoke else 'FULL'}; "
              f"budget={spec[budget_key]}s; seeds={len(seeds)}) ===")
        records = run_matrix(spec, seeds, budget_key)
        with open(RESULTS / f"results_{name}.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        analyze(records, spec, RESULTS / f"comparisons_{name}.txt")


if __name__ == "__main__":
    main()
