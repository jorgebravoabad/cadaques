"""Replay and resume: the campaign as a durable object (ADR-0004/0012).

Two guarantees, both tested in CI:

* **Replay** — ``replay(spec, events)`` derives the final
  :class:`~cadaques.runtime.state.CampaignState` from the record
  alone, and it equals the live outcome's state. Nothing is
  re-executed: replay is derivation, so it is deterministic by
  construction and audits a campaign without paying for it again.

* **Resume** — a checkpointed campaign continues exactly where it
  stopped: :func:`checkpoint` captures spec, events and the rng
  states of every participant; :func:`resume` rebuilds the campaign,
  replays history into the driver, restores budget and randomness,
  and the continued run is query-for-query identical to an
  uninterrupted one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..core.cost import Cost
from ..core.events import EventLog
from ..core.observation import FailureRecord
from ..core.records import Query, Result
from ..core.spec import CampaignSpec, SpecError
from .state import CampaignState, reduce

if TYPE_CHECKING:  # pragma: no cover
    from .campaign import Campaign

CHECKPOINT_SCHEMA: str = "cadaques.checkpoint/1"


# ---------------------------------------------------------------- replay
def replay(spec: CampaignSpec, events: EventLog | str | Path) -> CampaignState:
    """Derive the final state of a recorded campaign (no re-execution)."""
    if spec.schema != "cadaques.spec/1":
        raise SpecError(f"Unsupported spec schema {spec.schema!r}")
    log = events if isinstance(events, EventLog) else EventLog.from_jsonl(events)
    return reduce(log)


# ---------------------------------------------------------------- rng state
def _rng_state(participant: Any) -> dict | None:
    rng = getattr(participant, "_rng", None)
    if isinstance(rng, np.random.Generator):
        return rng.bit_generator.state
    seq = getattr(participant, "_seed_sequence", None)
    if isinstance(seq, np.random.SeedSequence):
        return {
            "seed_sequence": {
                "entropy": seq.entropy,
                "spawn_key": list(seq.spawn_key),
                "n_children_spawned": seq.n_children_spawned,
            }
        }
    return None


def _restore_rng(participant: Any, state: dict | None) -> None:
    if state is None:
        return
    if "seed_sequence" in state:
        s = state["seed_sequence"]
        participant._seed_sequence = np.random.SeedSequence(
            entropy=s["entropy"],
            spawn_key=tuple(s["spawn_key"]),
            n_children_spawned=s["n_children_spawned"],
        )
        return
    rng = np.random.Generator(np.random.PCG64())
    rng.bit_generator.state = state
    participant._rng = rng


# ---------------------------------------------------------------- checkpoint
def checkpoint(campaign: "Campaign", directory: str | Path) -> Path:
    """Persist a running campaign: spec + events + participant rng states."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    campaign.to_spec().dump(directory / "spec.json")
    campaign.events.to_jsonl(directory / "events.jsonl")
    meta = {
        "schema": CHECKPOINT_SCHEMA,
        "rng": {
            "driver": _rng_state(campaign.driver),
            "oracle": _rng_state(campaign.oracle),
        },
    }
    (directory / "checkpoint.json").write_text(json.dumps(meta, indent=2))
    return directory


def _result_from_event(event) -> Result:
    p = event.payload
    query = Query(params=dict(p.get("params", {})), fidelity=dict(p.get("fidelity", {})))
    settled = Cost.from_dict(p["settled"])
    if event.kind == "oracle_result":
        return Result(query=query, value=float(p["value"]), cost=settled,
                      status=p.get("status", "completed"))
    failure = FailureRecord(
        kind=p.get("failure_kind", "oracle_error"),
        detail=p.get("failure_detail", ""),
        retryable=False,
        exception=p.get("exception"),
    )
    return Result.failed(query, cost=settled, failure=failure)


def resume(directory: str | Path) -> "Campaign":
    """Rebuild a checkpointed campaign, primed to continue.

    History, driver knowledge, budget position, event log and rng
    states are all restored; ``run(max_queries=N)`` counts N as the
    *total* including restored history, so an interrupted campaign and
    an uninterrupted one obey the same contract.
    """
    directory = Path(directory)
    meta = json.loads((directory / "checkpoint.json").read_text())
    if meta.get("schema") != CHECKPOINT_SCHEMA:
        raise SpecError(f"Unsupported checkpoint schema {meta.get('schema')!r}")

    from .campaign import Campaign

    spec = CampaignSpec.load(directory / "spec.json")
    campaign = Campaign.from_spec(spec)
    events = EventLog.from_jsonl(directory / "events.jsonl")

    # Restore record and accounting from events (source of truth).
    campaign.events = events
    for event in events:
        if event.kind == "driver_proposal":
            campaign.budget.charge(Cost.from_dict(event.payload["settled"]), settle=True)
        elif event.kind in ("oracle_result", "oracle_failure"):
            result = _result_from_event(event)
            campaign.budget.charge(result.cost, settle=True)
            campaign.history.append(result)
            campaign.driver.observe(result)

    # Restore randomness *after* replaying (observe must not consume rng;
    # states were captured at checkpoint time).
    _restore_rng(campaign.driver, meta["rng"].get("driver"))
    _restore_rng(campaign.oracle, meta["rng"].get("oracle"))
    return campaign
