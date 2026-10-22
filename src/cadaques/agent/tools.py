"""The agent-facing tool surface of CADAQUES (ADR-0013, ADR-0014).

Every operation an agent may perform on a campaign is a :class:`Tool`
with a typed JSON-Schema input and output, explicit side-effect
annotations and a handler. :class:`Toolbox` dispatches calls and wraps
every outcome in one envelope::

    {"ok": true,  "tool": ..., "result": {...}, "next_actions": [...]}
    {"ok": false, "tool": ..., "error": {"code", "message", "retryable",
                                         "next_actions", "details"}}

so a caller never parses a traceback. The same registry backs the MCP
server (:mod:`cadaques.agent.mcp_server`) and the JSON command line
(``cadaques-agent call``); neither adds behaviour of its own.

The properties this layer guarantees, each tested in CI:

* typed schemas for every tool (``Toolbox.schemas()``);
* queryable capabilities (``describe_capabilities``, ``get_campaign_status``);
* structured errors from a closed vocabulary (:mod:`.errors`);
* idempotent mutations keyed by ``request_id``, and campaigns resumable
  from disk by any later session (:mod:`.store`);
* the kernel, not the caller, admits work against the budget and owns
  the record: reservations are checked against declared cost plus
  outstanding reservations, settlements are charged unconditionally;
* every event records its ``actor`` and the ``request_id`` that caused it.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import __version__
from ..core.cost import CURRENCIES, Cost
from ..core.events import EVENT_KINDS, EVENT_SCHEMA, Event
from ..core.records import Query
from ..core.spec import SPEC_SCHEMA, CampaignSpec, SpecError
from ..drivers.external import ExternalDriver
from ..oracles.external import ExternalOracle
from ..runtime.campaign import TERMINAL_STOP_REASONS, Campaign, DriverStopped
from ..runtime.replay import CHECKPOINT_SCHEMA
from ..runtime.state import CampaignState
from . import schema as S
from .components import ComponentRegistry
from .errors import ERROR_CODES, ToolError
from .store import CampaignStore, Session, args_digest

STOP_REASONS: tuple[str, ...] = (
    "budget_exhausted", "max_queries", "driver_stopped",
    "rejection_limit", "success", "operator_stopped",
)


@dataclass(frozen=True)
class Tool:
    """One operation of the agent surface."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    handler: Callable[["_Call"], tuple[dict[str, Any], list[str]]]
    read_only: bool = False
    destructive: bool = False

    @property
    def idempotent(self) -> bool:
        """Read-only tools trivially; mutating tools through request_id."""
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "annotations": {
                "read_only": self.read_only,
                "destructive": self.destructive,
                "idempotent": self.idempotent,
            },
        }


@dataclass
class _Call:
    """Context handed to a handler."""

    toolbox: "Toolbox"
    args: Mapping[str, Any]
    committed: bool = False
    #: A read-only call holds no campaign lock and must read defensively.
    readonly: bool = False
    sessions: dict[str, Session] = field(default_factory=dict)

    @property
    def store(self) -> CampaignStore:
        return self.toolbox.store

    def session(self, campaign_id: str, *, view: bool = False) -> Session:
        """The campaign's session for this call (ADR-0015); a view is read-only."""
        held = self.sessions.get(campaign_id)
        if held is None or (not view and not held.full):
            read = self.store.read_session if self.readonly else self.store.session
            held = self.sessions[campaign_id] = read(campaign_id, view=view)
        return held

    def new_session(self, campaign_id: str, campaign: Campaign) -> Session:
        session = self.sessions[campaign_id] = self.store.create(campaign_id, campaign)
        return session

    def save(self, session: Session) -> None:
        self.store.save(session)
        self.committed = True

    def release(self) -> None:
        """Return sessions to the store's cache; uncommitted ones are dropped there."""
        for session in self.sessions.values():
            self.store.release(session)
        self.sessions.clear()


# ====================================================================== helpers
def _obj(properties: Mapping[str, Any], required: Iterable[str] = (), **extra: Any) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required),
            "additionalProperties": False, **extra}


def _out(properties: Mapping[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    # Outputs may grow new keys in minor releases; inputs may not.
    return {"type": "object", "properties": dict(properties), "required": list(required)}


def _cost(data: Mapping[str, Any] | None) -> Cost:
    return Cost.from_dict(dict(data or {}))


def _budget(campaign: Campaign, state: CampaignState) -> dict[str, Any]:
    total, spent, reserved = campaign.budget.total, campaign.budget.spent, state.reserved
    available = {
        c: (getattr(total, c) - getattr(spent, c) - getattr(reserved, c))
        if getattr(total, c) > 0 else None
        for c in CURRENCIES
    }
    return {
        "total": total.as_dict(),
        "spent": spent.as_dict(),
        "reserved": reserved.as_dict(),
        "available": available,
        "capped": [c for c in CURRENCIES if getattr(total, c) > 0],
        "fraction_used": campaign.budget.view().fraction_used,
    }


def _pending(session: Session) -> list[dict[str, Any]]:
    now = time.time()
    return [{
        "evaluation_id": ev.payload["evaluation_id"],
        "params": ev.payload["params"],
        "fidelity": ev.payload["fidelity"],
        "declared_cost": ev.payload["declared"],
        "reserved_at": ev.timestamp,
        "age_seconds": now - ev.timestamp,
        "reserved_by": ev.actor,
    } for ev in session.pending()]


def _accepting(state: CampaignState) -> bool:
    return state.stop_reason not in TERMINAL_STOP_REASONS


def _status(campaign_id: str, session: Session) -> dict[str, Any]:
    campaign, state = session.campaign, session.state()
    return {
        "campaign_id": campaign_id,
        "oracle": type(campaign.oracle).__name__,
        "driver": type(campaign.driver).__name__,
        "task": campaign.task.name if campaign.task is not None else None,
        "bounds": ({k: list(v) for k, v in campaign.task.space.bounds.items()}
                   if campaign.task is not None else None),
        "direction": "maximize" if campaign.maximize else "minimize",
        "stop_reason": state.stop_reason,
        "accepting_reservations": _accepting(state),
        "n_queries": state.n_queries,
        "n_failures": state.n_failures,
        "n_rejected": state.n_rejected,
        "n_cancelled": state.n_cancelled,
        "n_events": session.n_events,
        "best": ({"value": state.best_value, "params": dict(state.best_params)}
                 if state.best_value is not None else None),
        "budget": _budget(campaign, state),
        "overrun": state.overrun.as_dict(),
        "pending": _pending(session),
    }


def _status_next(status: Mapping[str, Any]) -> list[str]:
    if status["pending"]:
        return ["settle_evaluation", "cancel_evaluation", "get_campaign_status"]
    if status["accepting_reservations"]:
        return ["reserve_evaluation", "run_campaign", "get_campaign_status", "stop_campaign"]
    return ["get_events", "get_campaign_status"]


def _ensure_accepting(state: CampaignState) -> None:
    if not _accepting(state):
        raise ToolError(
            "campaign_stopped",
            f"The campaign has stopped ({state.stop_reason}); it admits no new work. "
            "Pending evaluations can still be settled or cancelled.",
            details={"stop_reason": state.stop_reason, "pending": list(state.pending)},
        )


def _check_finite(name: str, value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolError("invalid_arguments", f"{name} must be a finite number.",
                        details={"field": name})


# ====================================================================== handlers
def _describe_capabilities(call: _Call) -> tuple[dict[str, Any], list[str]]:
    tb = call.toolbox
    return {
        "cadaques_version": __version__,
        "schemas": {"events": EVENT_SCHEMA, "spec": SPEC_SCHEMA, "checkpoint": CHECKPOINT_SCHEMA},
        "workspace": str(tb.store.root),
        "currencies": list(CURRENCIES),
        "event_kinds": list(EVENT_KINDS),
        "stop_reasons": list(STOP_REASONS),
        "terminal_stop_reasons": sorted(TERMINAL_STOP_REASONS),
        "error_codes": {code: {"retryable": r, "next_actions": list(n)}
                        for code, (r, n) in ERROR_CODES.items()},
        "drivers": tb.components.describe("driver"),
        "oracles": tb.components.describe("oracle"),
        "tools": [{"name": t.name, "read_only": t.read_only, "destructive": t.destructive}
                  for t in tb.tools.values()],
        "spec_template": {
            "schema": SPEC_SCHEMA,
            "oracle": {"class": "<allowed oracle class>", "params": {}},
            "driver": {"class": "<allowed driver class>", "params": {}},
            "budget_total": {"seconds": 3600.0},
            "task": {"bounds": {"x": [0.0, 1.0]}, "direction": "maximize",
                     "fidelities": {}, "success_value": None, "name": "my-task"},
            "seed": 0,
        },
    }, ["list_campaigns", "validate_spec", "create_campaign"]


def _parse_spec(call: _Call, raw: Mapping[str, Any]) -> CampaignSpec:
    problems = (call.toolbox.components.check("oracle", raw.get("oracle"))
                + call.toolbox.components.check("driver", raw.get("driver")))
    if problems:
        raise ToolError("spec_invalid", "The spec names a participant that may not be built.",
                        details={"problems": problems})
    data = {"schema": SPEC_SCHEMA, **raw}
    try:
        return CampaignSpec.loads(json.dumps(data))
    except (SpecError, TypeError, ValueError) as exc:
        raise ToolError("spec_invalid", f"Not a valid campaign spec: {exc}",
                        details={"exception": type(exc).__name__}) from None


def _build(spec: CampaignSpec) -> Campaign:
    try:
        return spec.build()
    except Exception as exc:  # noqa: BLE001 — any construction failure is a spec problem
        raise ToolError("spec_invalid", f"The spec could not be built: {exc}",
                        details={"exception": type(exc).__name__}) from None


def _validate_spec(call: _Call) -> tuple[dict[str, Any], list[str]]:
    spec = _parse_spec(call, call.args["spec"])
    campaign = _build(spec)
    normalized = json.loads(spec.dumps())
    return {
        "valid": True,
        "spec": normalized,
        "oracle": type(campaign.oracle).__name__,
        "driver": type(campaign.driver).__name__,
        "operator_proposes": isinstance(campaign.driver, ExternalDriver),
        "evaluated_externally": isinstance(campaign.oracle, ExternalOracle),
    }, ["create_campaign"]


def _create_campaign(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid = a["campaign_id"]
    if call.store.exists(cid):
        raise ToolError("campaign_exists", f"Campaign {cid!r} already exists.",
                        details={"campaign_id": cid})
    spec = _parse_spec(call, a["spec"])
    campaign = _build(spec)
    spec_sha = hashlib.sha256(spec.dumps().encode("utf-8")).hexdigest()
    call.store.path(cid).mkdir(parents=True, exist_ok=True)
    session = call.new_session(cid, campaign)
    with campaign.events.acting_as(a["actor"], a["request_id"]):
        campaign.events.append(
            "campaign_started",
            oracle=type(campaign.oracle).__name__,
            driver=type(campaign.driver).__name__,
            maximize=campaign.maximize,
            meter_driver=campaign.meter_driver,
            budget_total=campaign.budget.total.as_dict(),
            task=campaign.task.name if campaign.task is not None else None,
            seed=campaign.seed,
            max_queries=None,
            spec_sha256=spec_sha,
            note=a.get("note", ""),
        )
    call.save(session)
    status = _status(cid, session)
    return {"created": True, "spec_sha256": spec_sha, "status": status}, _status_next(status)


def _list_campaigns(call: _Call) -> tuple[dict[str, Any], list[str]]:
    items = []
    for cid in call.store.campaign_ids():
        try:
            session = call.session(cid, view=True)
        except Exception as exc:  # noqa: BLE001 — one broken directory must not hide the rest
            items.append({"campaign_id": cid, "readable": False, "reason": str(exc)})
            continue
        campaign, state = session.campaign, session.state()
        items.append({
            "campaign_id": cid, "readable": True,
            "oracle": type(campaign.oracle).__name__, "driver": type(campaign.driver).__name__,
            "stop_reason": state.stop_reason, "accepting_reservations": _accepting(state),
            "n_queries": state.n_queries, "n_pending": len(state.pending),
            "best_value": state.best_value,
        })
    return {"campaigns": items}, ["get_campaign_status", "create_campaign"]


def _get_campaign_status(call: _Call) -> tuple[dict[str, Any], list[str]]:
    cid = call.args["campaign_id"]
    status = _status(cid, call.session(cid, view=True))
    return status, _status_next(status)


def _get_events(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    session = call.session(a["campaign_id"])
    log = session.campaign.events.events
    since, limit = a.get("since_seq", 0), a.get("limit", 200)
    kinds = set(a.get("kinds", EVENT_KINDS))
    page: list[Event] = []
    more = False
    for event in log[since:]:  # seq is the position in the log
        if event.kind not in kinds:
            continue
        if len(page) == limit:
            more = True
            break
        page.append(event)
    next_seq = page[-1].seq + 1 if more else None
    return {
        "events": [e.as_dict() for e in page],
        "next_seq": next_seq,
        "total_events": len(log),
    }, (["get_events"] if next_seq is not None else ["get_campaign_status"])


def _reserve_evaluation(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid = a["campaign_id"]
    session = call.session(cid)
    campaign, state = session.campaign, session.state()
    _ensure_accepting(state)
    decision_cost = _cost(a.get("decision_cost"))
    fidelity_override = dict(a.get("fidelity", {}))
    operator_params = a.get("params")
    if operator_params is not None:
        for name, value in operator_params.items():
            _check_finite(f"params.{name}", value)

    with campaign.events.acting_as(a["actor"], a["request_id"]):
        if operator_params is None:
            if isinstance(campaign.driver, ExternalDriver):
                raise ToolError(
                    "invalid_arguments",
                    "This campaign's driver is external: pass 'params' with the point to evaluate.",
                    details={"driver": "ExternalDriver"},
                    next_actions=["reserve_evaluation"],
                )
            query = _driver_proposal(call, session, decision_cost, fidelity_override)
        else:
            query = Query(params=dict(operator_params), fidelity=fidelity_override,
                          tag=a.get("tag", ""))
            campaign.budget.charge(decision_cost, settle=True)
            campaign.events.append(
                "driver_proposal", label="operator",
                declared=decision_cost.as_dict(), settled=decision_cost.as_dict(),
                metered=Cost().as_dict(), reported=decision_cost.as_dict(),
            )
            if campaign.task is not None and not campaign.task.admits(query):
                campaign.events.append("rejected", label="operator", params=dict(query.params),
                                       fidelity=dict(query.fidelity), reason="task_validation")
                call.save(session)
                raise ToolError(
                    "invalid_query",
                    "The point is outside the task's search space or violates a constraint.",
                    details={"params": dict(query.params),
                             "bounds": {k: list(v) for k, v in campaign.task.space.bounds.items()}},
                    next_actions=["reserve_evaluation", "get_campaign_status"],
                )

        declared = campaign.oracle.price(query)
        if not campaign.budget.can_afford(state.reserved + declared):
            if not state.pending:
                campaign.events.append("stopped", reason="budget_exhausted",
                                       n_queries=len(campaign.history),
                                       spent=campaign.budget.spent.as_dict())
            call.save(session)
            raise ToolError(
                "budget_exhausted",
                "The declared cost of this evaluation does not fit in the budget left after "
                "outstanding reservations.",
                details={"declared_cost": declared.as_dict(),
                         "reserved": state.reserved.as_dict(),
                         "remaining": campaign.budget.remaining.as_dict(),
                         "pending": list(state.pending),
                         "campaign_stopped": not state.pending},
                next_actions=(["settle_evaluation", "cancel_evaluation", "get_campaign_status"]
                              if state.pending else ["get_campaign_status", "get_events"]),
            )

        evaluation_id = f"ev-{len(campaign.events):06d}"
        campaign.events.append(
            "evaluation_reserved", evaluation_id=evaluation_id,
            label=type(campaign.oracle).__name__, declared=declared.as_dict(),
            params=dict(query.params), fidelity=dict(query.fidelity), tag=query.tag,
        )
    call.save(session)
    state = session.state()
    return {
        "campaign_id": cid,
        "evaluation_id": evaluation_id,
        "params": dict(query.params),
        "fidelity": dict(query.fidelity),
        "tag": query.tag,
        "declared_cost": declared.as_dict(),
        "budget": _budget(campaign, state),
    }, ["settle_evaluation", "cancel_evaluation"]


def _driver_proposal(call: _Call, session: Session, decision_cost: Cost,
                     fidelity_override: Mapping[str, Any]) -> Query:
    """Ask the in-kernel driver, as the sync loop does: metered, validated."""
    campaign = session.campaign
    rejections = 0
    while True:
        t0 = time.perf_counter()
        try:
            query = campaign.driver.propose(campaign.history, campaign.budget.view())
        except DriverStopped:
            campaign.events.append("stopped", reason="driver_stopped",
                                   n_queries=len(campaign.history),
                                   spent=campaign.budget.spent.as_dict())
            call.save(session)
            raise ToolError("campaign_stopped", "The driver ended the campaign.",
                            details={"stop_reason": "driver_stopped"}) from None
        elapsed = time.perf_counter() - t0
        metered = Cost()
        if campaign.meter_driver:
            metered = Cost(seconds=elapsed) + getattr(campaign.driver, "last_proposal_cost", Cost())
        cost = metered + decision_cost
        decision_cost = Cost()  # the operator's own cost is charged once
        if fidelity_override:
            query = Query(params=dict(query.params),
                          fidelity={**dict(query.fidelity), **fidelity_override}, tag=query.tag)
        campaign.budget.charge(cost, settle=True)
        campaign.events.append(
            "driver_proposal", label=type(campaign.driver).__name__,
            declared=cost.as_dict(), settled=cost.as_dict(),
            metered=metered.as_dict(), reported=(cost - metered).as_dict(),
        )
        if campaign.task is None or campaign.task.admits(query):
            return query
        rejections += 1
        campaign.events.append("rejected", label=type(campaign.driver).__name__,
                               params=dict(query.params), fidelity=dict(query.fidelity),
                               reason="task_validation")
        if rejections >= campaign.max_consecutive_rejections:
            campaign.events.append("stopped", reason="rejection_limit",
                                   n_queries=len(campaign.history),
                                   spent=campaign.budget.spent.as_dict())
            call.save(session)
            raise ToolError("campaign_stopped",
                            "The driver kept proposing points outside the task.",
                            details={"stop_reason": "rejection_limit"})


def _open_reservation(session: Session, evaluation_id: str) -> Event:
    reservation = session.reservation(evaluation_id)
    if reservation is None:
        raise ToolError("unknown_evaluation", f"No reservation {evaluation_id!r} in this campaign.",
                        details={"evaluation_id": evaluation_id})
    closing = session.closing(evaluation_id)
    if closing is not None:
        raise ToolError(
            "already_settled",
            f"Evaluation {evaluation_id!r} was already "
            f"{'cancelled' if closing.kind == 'evaluation_cancelled' else 'settled'}.",
            details={"evaluation_id": evaluation_id, "kind": closing.kind, "seq": closing.seq,
                     "request_id": closing.request_id, "actor": closing.actor},
        )
    return reservation


def _settle_evaluation(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid, evaluation_id = a["campaign_id"], a["evaluation_id"]
    session = call.session(cid)
    campaign = session.campaign
    reservation = _open_reservation(session, evaluation_id)
    rp = reservation.payload
    declared = Cost.from_dict(rp["declared"])
    if "settled_cost" in a:
        settled, source = _cost(a["settled_cost"]), "reported"
    else:
        settled, source = declared, "declared_default"
    outcome = a["outcome"]
    if outcome == "completed" and "value" not in a:
        raise ToolError("invalid_arguments", "A completed evaluation needs a 'value'.",
                        details={"field": "value"}, next_actions=["settle_evaluation"])
    common = dict(
        label=rp["label"], declared=declared.as_dict(), settled=settled.as_dict(),
        params=rp["params"], fidelity=rp["fidelity"], evaluation_id=evaluation_id,
        settled_source=source, elapsed_since_reservation=time.time() - reservation.timestamp,
        timings=dict(a.get("timings", {})), artifacts=list(a.get("artifacts", [])),
    )
    with campaign.events.acting_as(a["actor"], a["request_id"]):
        campaign.budget.charge(settled, settle=True)
        if outcome == "completed":
            value = float(a["value"])
            campaign.events.append("oracle_result", value=value, status="completed", **common)
        else:
            failure = dict(a.get("failure", {}))
            campaign.events.append(
                "oracle_failure", status="failed",
                failure_kind=failure.get("kind", "external_failure"),
                failure_detail=failure.get("detail", ""),
                retryable=bool(failure.get("retryable", False)),
                exception=None, **common,
            )
        state = session.state()
        if (outcome == "completed" and campaign.task is not None
                and campaign.task.succeeded(value) and _accepting(state)):
            campaign.events.append("stopped", reason="success", n_queries=state.n_queries,
                                   spent=campaign.budget.spent.as_dict())
    call.save(session)
    status = _status(cid, session)
    return {
        "evaluation_id": evaluation_id,
        "outcome": outcome,
        "declared_cost": declared.as_dict(),
        "settled_cost": settled.as_dict(),
        "settled_source": source,
        "overrun": (settled - declared).as_dict(),
        "status": status,
    }, _status_next(status)


def _cancel_evaluation(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid, evaluation_id = a["campaign_id"], a["evaluation_id"]
    session = call.session(cid)
    campaign = session.campaign
    reservation = _open_reservation(session, evaluation_id)
    rp = reservation.payload
    declared = Cost.from_dict(rp["declared"])
    if "settled_cost" in a:
        settled, source = _cost(a["settled_cost"]), "reported"
    else:
        settled, source = declared, "declared_default"
    with campaign.events.acting_as(a["actor"], a["request_id"]):
        campaign.budget.charge(settled, settle=True)
        campaign.events.append(
            "evaluation_cancelled", label=rp["label"], evaluation_id=evaluation_id,
            declared=declared.as_dict(), settled=settled.as_dict(), settled_source=source,
            params=rp["params"], fidelity=rp["fidelity"], reason=a.get("reason", ""),
        )
    call.save(session)
    status = _status(cid, session)
    return {"evaluation_id": evaluation_id, "cancelled": True,
            "settled_cost": settled.as_dict(), "settled_source": source,
            "status": status}, _status_next(status)


def _run_campaign(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid = a["campaign_id"]
    session = call.session(cid)
    campaign, state = session.campaign, session.state()
    if isinstance(campaign.oracle, ExternalOracle) or isinstance(campaign.driver, ExternalDriver):
        raise ToolError(
            "unsupported_operation",
            "run_campaign drives in-process oracles with an in-process driver; this campaign "
            "is operated externally. Use reserve_evaluation and settle_evaluation.",
            details={"oracle": type(campaign.oracle).__name__,
                     "driver": type(campaign.driver).__name__},
            next_actions=["reserve_evaluation"],
        )
    if state.pending:
        raise ToolError("pending_evaluations",
                        "Settle or cancel outstanding reservations before running the loop.",
                        details={"pending": list(state.pending)})
    _ensure_accepting(state)
    before = len(campaign.history)
    outcome = campaign.run(max_queries=before + a["max_new_queries"],
                           actor=a["actor"], request_id=a["request_id"])
    call.save(session)
    status = _status(cid, session)
    return {"stop_reason": outcome.stop_reason, "n_new_queries": len(campaign.history) - before,
            "status": status}, _status_next(status)


def _stop_campaign(call: _Call) -> tuple[dict[str, Any], list[str]]:
    a = call.args
    cid = a["campaign_id"]
    session = call.session(cid)
    campaign, state = session.campaign, session.state()
    already = not _accepting(state)
    if not already:
        with campaign.events.acting_as(a["actor"], a["request_id"]):
            campaign.events.append("stopped", reason="operator_stopped", note=a.get("reason", ""),
                                   n_queries=state.n_queries, spent=campaign.budget.spent.as_dict())
        call.save(session)
    status = _status(cid, session)
    return {"stopped": True, "already_stopped": already, "status": status}, _status_next(status)


# ====================================================================== registry
_CID = {"campaign_id": S.CAMPAIGN_ID}
_MUT = {"actor": S.ACTOR, "request_id": S.REQUEST_ID}
_MUT_REQ = ["campaign_id", "actor", "request_id"]
_STATUS_OUT = _out({
    "campaign_id": {"type": "string"}, "stop_reason": {"type": ["string", "null"]},
    "accepting_reservations": {"type": "boolean"}, "n_queries": {"type": "integer"},
    "best": {"type": ["object", "null"]}, "budget": {"type": "object"},
    "pending": {"type": "array"},
}, ["campaign_id", "accepting_reservations", "budget", "pending"])
_SPEC_IN = {"type": "object", "description": (
    "A campaign spec (cadaques.spec/1): oracle and driver as {class, params} using classes "
    "listed by describe_capabilities, budget_total, and optionally task, seed, meter_driver, "
    "max_consecutive_rejections.")}

TOOLS: tuple[Tool, ...] = (
    Tool(
        "describe_capabilities",
        "List what this CADAQUES server offers: allowed drivers and oracles with their "
        "parameters, cost currencies, stop reasons, error codes, schemas and tools. Call first.",
        _obj({}), _out({"cadaques_version": {"type": "string"}, "drivers": {"type": "array"},
                        "oracles": {"type": "array"}, "tools": {"type": "array"}},
                       ["cadaques_version", "drivers", "oracles", "tools"]),
        _describe_capabilities, read_only=True,
    ),
    Tool(
        "validate_spec",
        "Check a campaign spec without creating anything; returns the normalized spec.",
        _obj({"spec": _SPEC_IN}, ["spec"]),
        _out({"valid": {"type": "boolean"}, "spec": {"type": "object"}}, ["valid", "spec"]),
        _validate_spec, read_only=True,
    ),
    Tool(
        "create_campaign",
        "Create a persistent campaign from a spec. The budget is fixed here and enforced by "
        "the kernel for the life of the campaign.",
        _obj({**_CID, "spec": _SPEC_IN, **_MUT,
              "note": {"type": "string", "description": "Free text recorded on the start event."}},
             ["campaign_id", "spec", "actor", "request_id"]),
        _out({"created": {"type": "boolean"}, "spec_sha256": {"type": "string"},
              "status": _STATUS_OUT}, ["created", "status"]),
        _create_campaign,
    ),
    Tool(
        "list_campaigns",
        "List the campaigns in this workspace with their state.",
        _obj({}), _out({"campaigns": {"type": "array"}}, ["campaigns"]),
        _list_campaigns, read_only=True,
    ),
    Tool(
        "get_campaign_status",
        "Current state of a campaign, derived from its record: budget (total, spent, reserved, "
        "available), best result, pending evaluations, and whether it accepts new work.",
        _obj(_CID, ["campaign_id"]), _STATUS_OUT, _get_campaign_status, read_only=True,
    ),
    Tool(
        "get_events",
        "Read a campaign's event log (the source of truth), paginated by sequence number.",
        _obj({**_CID,
              "since_seq": {"type": "integer", "minimum": 0},
              "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
              "kinds": {"type": "array", "items": {"type": "string", "enum": list(EVENT_KINDS)}}},
             ["campaign_id"]),
        _out({"events": {"type": "array"}, "next_seq": {"type": ["integer", "null"]},
              "total_events": {"type": "integer"}}, ["events", "total_events"]),
        _get_events, read_only=True,
    ),
    Tool(
        "reserve_evaluation",
        "Admit the next evaluation and reserve its declared cost. Without 'params' the "
        "campaign's driver proposes the point; with 'params' the caller proposes it. Run the "
        "evaluation yourself (e.g. as a cluster job), then report it with settle_evaluation. "
        "Report your own deliberation cost in 'decision_cost'.",
        _obj({**_CID, **_MUT, "params": S.PARAMS, "fidelity": S.FIDELITY,
              "tag": {"type": "string"}, "decision_cost": S.COST}, _MUT_REQ),
        _out({"evaluation_id": {"type": "string"}, "params": {"type": "object"},
              "fidelity": {"type": "object"}, "declared_cost": {"type": "object"},
              "budget": {"type": "object"}},
             ["evaluation_id", "params", "fidelity", "declared_cost", "budget"]),
        _reserve_evaluation,
    ),
    Tool(
        "settle_evaluation",
        "Report the outcome of a reserved evaluation with its real cost. Settlement is charged "
        "even if it overruns the budget: the work was done. Failures are results too.",
        _obj({**_CID, **_MUT,
              "evaluation_id": {"type": "string", "minLength": 1},
              "outcome": {"type": "string", "enum": ["completed", "failed"]},
              "value": {"type": "number", "description": "Objective value; required if completed."},
              "settled_cost": S.COST,
              "failure": _obj({"kind": {"type": "string"}, "detail": {"type": "string"},
                               "retryable": {"type": "boolean"}}),
              "timings": {"type": "object", "additionalProperties": {"type": "number"},
                          "description": "Optional breakdown, e.g. queue_seconds, run_seconds."},
              "artifacts": {"type": "array", "items": {"type": "string"}, "maxItems": 100}},
             _MUT_REQ + ["evaluation_id", "outcome"]),
        _out({"evaluation_id": {"type": "string"}, "settled_cost": {"type": "object"},
              "overrun": {"type": "object"}, "status": _STATUS_OUT},
             ["evaluation_id", "settled_cost", "status"]),
        _settle_evaluation,
    ),
    Tool(
        "cancel_evaluation",
        "Withdraw a reserved evaluation that will not report a result. Whatever it cost is "
        "still charged (default: its declared cost).",
        _obj({**_CID, **_MUT, "evaluation_id": {"type": "string", "minLength": 1},
              "settled_cost": S.COST, "reason": {"type": "string"}},
             _MUT_REQ + ["evaluation_id"]),
        _out({"evaluation_id": {"type": "string"}, "cancelled": {"type": "boolean"},
              "status": _STATUS_OUT}, ["evaluation_id", "cancelled", "status"]),
        _cancel_evaluation, destructive=True,
    ),
    Tool(
        "run_campaign",
        "Run the in-process loop for up to max_new_queries evaluations (in-process oracle and "
        "driver only). Stops early on budget exhaustion, success or driver stop.",
        _obj({**_CID, **_MUT, "max_new_queries": {"type": "integer", "minimum": 1,
                                                  "maximum": 100000}},
             _MUT_REQ + ["max_new_queries"]),
        _out({"stop_reason": {"type": "string"}, "n_new_queries": {"type": "integer"},
              "status": _STATUS_OUT}, ["stop_reason", "n_new_queries", "status"]),
        _run_campaign,
    ),
    Tool(
        "stop_campaign",
        "Stop admitting new work. Pending evaluations can still be settled or cancelled.",
        _obj({**_CID, **_MUT, "reason": {"type": "string"}}, _MUT_REQ),
        _out({"stopped": {"type": "boolean"}, "already_stopped": {"type": "boolean"},
              "status": _STATUS_OUT}, ["stopped", "status"]),
        _stop_campaign, destructive=True,
    ),
)


# ====================================================================== dispatch
@dataclass
class Toolbox:
    """Dispatch tool calls against one workspace."""

    root: str | Path
    allow_components: Iterable[str] = ()
    lock_timeout: float = 30.0
    store: CampaignStore = field(init=False)
    components: ComponentRegistry = field(init=False)
    tools: dict[str, Tool] = field(init=False)

    def __post_init__(self) -> None:
        self.store = CampaignStore(self.root, lock_timeout=self.lock_timeout)
        self.components = ComponentRegistry(self.allow_components)
        self.tools = {t.name: t for t in TOOLS}

    def schemas(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.tools.values()]

    def call(self, name: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run one tool call; always returns an envelope, never raises."""
        args = dict(args or {})
        try:
            tool = self.tools.get(name)
            if tool is None:
                raise ToolError("invalid_arguments", f"Unknown tool {name!r}.",
                                details={"tools": list(self.tools)},
                                next_actions=["describe_capabilities"])
            problems = S.validate(args, tool.input_schema)
            if problems:
                raise ToolError("invalid_arguments", "Arguments do not match the tool schema.",
                                details={"problems": problems, "input_schema": tool.input_schema},
                                next_actions=[name])
            actor = args.get("actor")
            if actor is not None and actor["kind"] == "agent" and not actor.get("model"):
                raise ToolError("invalid_actor",
                                "An agent actor must state its model (and preferably version).",
                                details={"actor": actor}, next_actions=[name])
            if tool.read_only:
                call = _Call(self, args, readonly=True)
                try:
                    result, next_actions = tool.handler(call)
                finally:
                    call.release()
                return self._ok(name, result, next_actions)
            return self._mutate(tool, args)
        except ToolError as err:
            return self._err(name, err)
        except Exception as exc:  # noqa: BLE001 — the envelope is the contract
            return self._err(name, ToolError(
                "internal_error", f"{type(exc).__name__}: {exc}",
                details={"exception": type(exc).__name__}))

    def _mutate(self, tool: Tool, args: Mapping[str, Any]) -> dict[str, Any]:
        cid, request_id = args["campaign_id"], args["request_id"]
        digest = args_digest(tool.name, args)
        call = _Call(self, args)
        try:
            with self.store.lock(cid):
                prior = self.store.journal_lookup(cid, request_id)
                if prior is not None:
                    if prior["args_sha256"] != digest:
                        raise ToolError(
                            "idempotency_conflict",
                            f"request_id {request_id!r} was already used for a different call.",
                            details={"request_id": request_id, "previous_tool": prior["tool"]})
                    return {**prior["response"], "replayed": True}
                if self.store.exists(cid):
                    session = call.session(cid)
                    committed = session.events_for(request_id)
                    if committed:
                        return self._reconstructed(tool.name, cid, session, committed)
                try:
                    result, next_actions = tool.handler(call)
                    response = self._ok(tool.name, result, next_actions)
                except ToolError as err:
                    response = self._err(tool.name, err)
                if call.committed:
                    self.store.journal_append(cid, request_id, tool.name, digest, response)
                return response
        finally:
            call.release()

    def _reconstructed(self, name: str, cid: str, session: Session,
                       events: list[Event]) -> dict[str, Any]:
        """The call committed but its response was never journaled (crash window)."""
        campaign = session.campaign
        reserved = [e for e in events if e.kind == "evaluation_reserved"]
        if name == "reserve_evaluation" and reserved:
            p = reserved[-1].payload
            state = session.state()
            result = {"campaign_id": cid, "evaluation_id": p["evaluation_id"],
                      "params": p["params"], "fidelity": p["fidelity"], "tag": p.get("tag", ""),
                      "declared_cost": p["declared"], "budget": _budget(campaign, state)}
            return {**self._ok(name, result, ["settle_evaluation", "cancel_evaluation"]),
                    "replayed": True, "reconstructed": True}
        status = _status(cid, session)
        result = {"committed_events": [e.as_dict() for e in events], "status": status}
        return {**self._ok(name, result, _status_next(status)),
                "replayed": True, "reconstructed": True}

    @staticmethod
    def _ok(name: str, result: Mapping[str, Any], next_actions: list[str]) -> dict[str, Any]:
        return {"ok": True, "tool": name, "result": dict(result), "next_actions": next_actions}

    @staticmethod
    def _err(name: str, err: ToolError) -> dict[str, Any]:
        return {"ok": False, "tool": name, "error": err.as_dict()}
