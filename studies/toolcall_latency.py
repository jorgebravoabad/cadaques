"""Tool-path latency versus log length (replicates the tool timing of the E5 overhead benchmark).

Times ``reserve_evaluation`` + ``settle_evaluation`` through the Toolbox over a
file-backed workspace, with an ``ExternalOracle`` and a ``RandomDriver``, and
reports the median per-call latency in windows at 100, 1000, 2000 and 5000
completed queries (an event log of about 2.0 events per query).

    python studies/toolcall_latency.py [--queries 100,1000,2000,5000] [--window 50]

Run it with ``PYTHONPATH`` pointing at the tree under test to compare versions.
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time

from cadaques.agent import Toolbox

ACTOR = {"kind": "agent", "id": "bench", "model": "none"}
SPEC = {
    "oracle": {"class": "cadaques.oracles.external.ExternalOracle",
               "params": {"tariff": {"seconds": 1.0}}},
    "driver": {"class": "cadaques.drivers.reference.RandomDriver",
               "params": {"space": {"x": [0.0, 1.0]}}},
    "budget_total": {"seconds": 1e9},
    "task": {"bounds": {"x": [0.0, 1.0]}, "direction": "maximize", "fidelities": {},
             "success_value": None, "name": "bench"},
    "seed": 0,
}


def measure(checkpoints: list[int], window: int) -> dict[int, tuple[float, float]]:
    """Median ms per tool call (reserve and settle separately averaged) in the window before each checkpoint."""
    out: dict[int, tuple[float, float]] = {}
    with tempfile.TemporaryDirectory() as d:
        tb = Toolbox(d)
        assert tb.call("create_campaign", {"campaign_id": "b", "spec": SPEC, "actor": ACTOR,
                                           "request_id": "c"})["ok"]
        times: list[float] = []
        for i in range(max(checkpoints)):
            t0 = time.perf_counter()
            r = tb.call("reserve_evaluation", {"campaign_id": "b", "actor": ACTOR,
                                               "request_id": f"r{i}"})
            ev = r["result"]
            tb.call("settle_evaluation", {
                "campaign_id": "b", "actor": ACTOR, "request_id": f"s{i}",
                "evaluation_id": ev["evaluation_id"], "outcome": "completed",
                "value": -(ev["params"]["x"] - 0.3) ** 2, "settled_cost": {"seconds": 1.0}})
            times.append(time.perf_counter() - t0)
            if i + 1 in checkpoints:
                w = times[-window:]
                out[i + 1] = (statistics.median(w) * 1e3 / 2, statistics.mean(w) * 1e3 / 2)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queries", default="100,1000,2000,5000")
    ap.add_argument("--window", type=int, default=50)
    ns = ap.parse_args()
    import cadaques

    print(f"cadaques {cadaques.__version__} from {cadaques.__file__}")
    for n, (med, mean) in measure([int(x) for x in ns.queries.split(",")], ns.window).items():
        print(f"{n:>6} queries: {med:8.2f} ms/call (median), {mean:8.2f} ms/call (mean)")
