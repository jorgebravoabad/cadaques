"""Fault schedules and run configurations for Study v2 (PROTOCOL.md §5).

A schedule is a pure function of (scenario, seed): paired runs in
different arms see the same faults at the same submission indices.
Submission indices count successful ``sbatch`` calls, starting at 1.

The run configuration (``run.json``) is the contract between harness,
shim and grader; it is written once per run before the agent starts.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

RUN_SCHEMA = "cadaques.study2.run/1"
SCENARIOS = ("S0", "S1", "S2", "S3", "S4", "S5")
ARMS = ("A", "B", "C")

#: The 1D photonic-crystal task: resolution read from MPB's own output.
PC1D_DECLARED = {
    "output_regex": r"Grid size is (\d+)x",
    "script_regex": r"resolution\s*[=:(]\s*(\d+)",
    "reference": 16,
    "exponent": 1.0,
}


def faults(scenario: str, seed: int, t_ref_s: float) -> dict[str, dict[str, Any]]:
    """Scheduler faults keyed by submission index (as a string, JSON-ready)."""
    rng = random.Random(f"{scenario}:{seed}")
    if scenario == "S1":
        return {
            "3": {"kind": "fail_now"},
            "7": {"kind": "kill_after", "after_s": round(t_ref_s * rng.uniform(0.55, 0.65), 2)},
        }
    if scenario == "S2":
        return {"2": {"kind": "preempt_after",
                      "after_s": round(t_ref_s * rng.uniform(0.65, 0.75), 2)}}
    if scenario == "S4":
        # From #3 on, every other job: the node is slow (settled ≈ 3 × its own runtime).
        return {str(i): {"kind": "slow", "factor": 2.0} for i in range(3, 400, 2)}
    return {}


def session_kill(scenario: str) -> dict[str, Any] | None:
    if scenario != "S3":
        return None
    return {"min_completed": 4, "min_in_flight": 1, "restart_delay_s": 60}


def make_run_config(
    *,
    arm: str,
    scenario: str,
    seed: int,
    model: str,
    c0: float,
    t_ref_s: float,
    backend: dict[str, Any],
    task: str = "pc1d",
    declared: dict[str, Any] | None = None,
    budget_units: float = 40.0,
    max_in_flight: int = 4,
    admission_grace_s: float = 60.0,
) -> dict[str, Any]:
    """The run.json for one cell of the matrix (PROTOCOL.md §§3–6)."""
    if arm not in ARMS or scenario not in SCENARIOS:
        raise ValueError(f"Unknown arm/scenario {arm!r}/{scenario!r}")
    units = 12.0 if scenario == "S5" else budget_units
    record = ({"kind": "cadaques_events", "path": "work/cadaques-workspace/pc1d/events.jsonl"}
              if arm == "A" else
              {"kind": "auto", "path": "work/ledger.jsonl",
               "events_path": "work/cadaques-workspace/pc1d/events.jsonl"})
    return {
        "schema": RUN_SCHEMA,
        "arm": arm, "scenario": scenario, "seed": seed, "model": model, "task": task,
        "c0": c0, "t_ref_s": t_ref_s, "budget": units * c0,
        "max_in_flight": max_in_flight, "admission_grace_s": admission_grace_s,
        "declared": declared or PC1D_DECLARED,
        "record": record, "report_path": "work/report.json",
        "faults": faults(scenario, seed, t_ref_s),
        "session_kill": session_kill(scenario),
        "backend": backend,
    }


def write_run_config(run_dir: str | Path, config: dict[str, Any]) -> Path:
    run_dir = Path(run_dir)
    for sub in ("work", "shim/submissions", "truth"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(json.dumps(config, indent=2))
    return path


def load_run_config(run_dir: str | Path) -> dict[str, Any]:
    config = json.loads((Path(run_dir) / "run.json").read_text())
    if config.get("schema") != RUN_SCHEMA:
        raise ValueError(f"Unsupported run schema {config.get('schema')!r}")
    return config
