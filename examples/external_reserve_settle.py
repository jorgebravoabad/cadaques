"""Running evaluations outside the process: reserve, run, settle.

An ``ExternalOracle`` is only a price model: the kernel declares what an
evaluation will cost, but the evaluation itself runs elsewhere (a batch job,
an instrument). The campaign is operated through the agent ``Toolbox``:

1. ``create_campaign``      -- from a JSON-able spec, in a workspace on disk;
2. ``reserve_evaluation``   -- the driver proposes a point and the declared
                               cost is held against the budget;
3. a simulated external job -- here a function; its measured cost differs
                               from the declaration;
4. ``settle_evaluation``    -- the measured cost (``settled_cost``) replaces
                               the declaration in the ledger;
5. ``get_campaign_status``  -- budget, pending reservations, best value.

Every mutating call carries an ``actor`` and a ``request_id``; repeating a
request id replays the recorded answer instead of acting twice.

Core install only:  python examples/external_reserve_settle.py
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from cadaques.agent import Toolbox

ACTOR = {"kind": "human", "id": "example"}
CAMPAIGN = "demo"

SPEC = {
    "oracle": {
        "class": "cadaques.oracles.external.ExternalOracle",
        # Declared price: 30 s at resolution 16, growing as resolution**2.
        "params": {"tariff": {"seconds": 30.0}, "scaling": {"resolution": 2.0},
                   "reference": {"resolution": 16}},
    },
    "driver": {"class": "cadaques.drivers.reference.RandomDriver",
               "params": {"space": {"x": [0.0, 1.0]}}},
    "budget_total": {"seconds": 300.0},
    "task": {"bounds": {"x": [0.0, 1.0]}, "direction": "maximize", "fidelities": {},
             "success_value": None, "name": "demo"},
    "seed": 0,
}


def external_job(params: dict) -> tuple[float, float]:
    """Stand-in for a cluster job: returns (value, measured CPU seconds)."""
    x = params["x"]
    value = math.sin(3.0 * x) * math.exp(-x)
    measured = 22.0 + 10.0 * x  # differs from the declared 30 s
    return value, measured


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tb = Toolbox(Path(tmp) / "workspace")

        def call(tool: str, request_id: str | None = None, **args):
            if request_id is not None:
                args = {"actor": ACTOR, "request_id": request_id, **args}
            response = tb.call(tool, args)
            if not response["ok"]:
                raise RuntimeError(f"{tool} failed: {response['error']}")
            return response["result"]

        call("create_campaign", "create", campaign_id=CAMPAIGN, spec=SPEC)

        for i in range(4):
            reserved = call("reserve_evaluation", f"reserve-{i}", campaign_id=CAMPAIGN)
            value, measured = external_job(reserved["params"])
            call("settle_evaluation", f"settle-{i}", campaign_id=CAMPAIGN,
                 evaluation_id=reserved["evaluation_id"], outcome="completed",
                 value=value, settled_cost={"seconds": measured})

        status = call("get_campaign_status", campaign_id=CAMPAIGN)
        spent = status["budget"]["spent"]["seconds"]
        print(f"evaluations: {status['n_queries']} | spent: {spent:.1f} s of "
              f"{status['budget']['total']['seconds']:.0f} s | best: {status['best']}")

        print("\nledger (declared -> settled seconds):")
        events = call("get_events", campaign_id=CAMPAIGN, kinds=["oracle_result"])["events"]
        for event in events:
            p = event["payload"]
            print(f"  {p['evaluation_id']}: {p['declared']['seconds']:.1f} -> "
                  f"{p['settled']['seconds']:.1f}  ({p['settled_source']}, by {event['actor']['id']})")


if __name__ == "__main__":
    main()
