"""The agent-first surface (ADR-0013/0014): one test class per guarantee."""

from __future__ import annotations

import json
import threading

import pytest

from cadaques import Budget, Campaign, Cost, EventLog, Ledger, Task, replay
from cadaques.agent import ERROR_CODES, TOOLS, Toolbox
from cadaques.agent.cli import main as cli_main
from cadaques.agent.schema import validate
from cadaques.core.spec import CampaignSpec
from cadaques.drivers import RandomDriver
from cadaques.oracles import ExternalOracle, Ising2DOracle
from cadaques.runtime.state import reduce

AGENT = {"kind": "agent", "id": "test-agent", "model": "test-model", "version": "1"}
HUMAN = {"kind": "human", "id": "jorge"}
BOUNDS = {"x": [0.0, 1.0]}


def external_spec(seconds=100.0, driver="random", tariff=10.0, success=None):
    drivers = {
        "random": {"class": "cadaques.drivers.reference.RandomDriver", "params": {"space": BOUNDS}},
        "external": {"class": "cadaques.drivers.external.ExternalDriver", "params": {}},
    }
    return {
        "oracle": {"class": "cadaques.oracles.external.ExternalOracle",
                   "params": {"tariff": {"seconds": tariff}}},
        "driver": drivers[driver],
        "budget_total": {"seconds": seconds},
        "task": {"bounds": BOUNDS, "direction": "maximize", "fidelities": {},
                 "success_value": success, "name": "t"},
        "seed": 3,
    }


def ising_spec(seconds=5.0, seed=11):
    return {
        "oracle": {"class": "cadaques.oracles.ising.Ising2DOracle",
                   "params": {"default_L": 6, "default_sweeps": 20, "default_equilibration": 10}},
        "driver": {"class": "cadaques.drivers.reference.RandomDriver",
                   "params": {"space": {"T": [1.5, 3.5]}}},
        "budget_total": {"seconds": seconds},
        "task": {"bounds": {"T": [1.5, 3.5]}, "direction": "maximize", "fidelities": {},
                 "success_value": None, "name": "ising"},
        "seed": seed,
    }


class Agent:
    """A tiny scripted operator: fresh request ids, envelope checks."""

    def __init__(self, toolbox, actor=AGENT, cid="c"):
        self.tb, self.actor, self.cid, self.n = toolbox, actor, cid, 0

    def __call__(self, tool, request_id=None, **args):
        tool_def = next(t for t in TOOLS if t.name == tool)
        if not tool_def.read_only:
            self.n += 1
            args = {"actor": self.actor, "request_id": request_id or f"{tool}-{self.n}", **args}
        if "campaign_id" in tool_def.input_schema["properties"]:
            args.setdefault("campaign_id", self.cid)
        response = self.tb.call(tool, args)
        if response["ok"]:  # every success honours its declared output schema
            assert validate(response["result"], tool_def.output_schema) == []
        else:
            assert response["error"]["code"] in ERROR_CODES
        return response


@pytest.fixture
def tb(tmp_path):
    return Toolbox(tmp_path / "ws")


@pytest.fixture
def agent(tb):
    return Agent(tb)


# ---------------------------------------------------------------- typed tools
class TestToolSchemas:
    def test_every_tool_has_object_schemas_and_annotations(self, tb):
        for schema in tb.schemas():
            assert schema["input_schema"]["type"] == "object"
            assert schema["input_schema"]["additionalProperties"] is False
            assert schema["output_schema"]["type"] == "object"
            assert set(schema["annotations"]) == {"read_only", "destructive", "idempotent"}

    def test_mutating_tools_require_actor_and_request_id(self, tb):
        for tool in TOOLS:
            if not tool.read_only:
                assert {"actor", "request_id"} <= set(tool.input_schema["required"])

    def test_error_vocabulary_points_at_real_tools(self):
        names = {t.name for t in TOOLS}
        for _, next_actions in ERROR_CODES.values():
            assert set(next_actions) <= names

    def test_schemas_are_json_serializable(self, tb):
        json.dumps(tb.schemas())


# ---------------------------------------------------------------- capabilities
class TestCapabilities:
    def test_describe_lists_participants_with_constructor_params(self, agent):
        r = agent("describe_capabilities")["result"]
        oracles = {d["class"].rsplit(".", 1)[1]: d for d in r["oracles"]}
        assert "ExternalOracle" in oracles and "AnalyticOracle" not in oracles
        assert {p["name"] for p in oracles["ExternalOracle"]["params"]} == {"tariff", "scaling", "reference"}
        assert r["schemas"]["events"] == "cadaques.events/2"
        assert {t["name"] for t in r["tools"]} == {t.name for t in TOOLS}

    def test_status_exposes_budget_and_pending(self, agent):
        agent("create_campaign", spec=external_spec())
        agent("reserve_evaluation")
        s = agent("get_campaign_status")["result"]
        assert s["accepting_reservations"] and len(s["pending"]) == 1
        assert s["budget"]["reserved"]["seconds"] == 10.0
        assert s["budget"]["capped"] == ["seconds"]


# ---------------------------------------------------------------- structured errors
class TestStructuredErrors:
    def test_unknown_tool(self, tb):
        r = tb.call("teleport", {})
        assert r["error"]["code"] == "invalid_arguments"
        assert "describe_capabilities" in r["error"]["next_actions"]

    def test_schema_violation_names_the_field(self, agent):
        agent("create_campaign", spec=external_spec())
        r = agent("settle_evaluation", evaluation_id="ev-1", outcome="exploded")
        assert any(p.startswith("/outcome") for p in r["error"]["details"]["problems"])

    def test_agent_actor_must_name_its_model(self, tb):
        r = Agent(tb, actor={"kind": "agent", "id": "anon"})("create_campaign", spec=external_spec())
        assert r["error"]["code"] == "invalid_actor"

    def test_unknown_campaign(self, agent):
        r = agent("get_campaign_status", campaign_id="nope")
        assert r["error"]["code"] == "unknown_campaign"
        assert "list_campaigns" in r["error"]["next_actions"]

    def test_internal_errors_are_enveloped(self, tb, monkeypatch):
        monkeypatch.setattr(tb.store, "campaign_ids", lambda: 1 / 0)
        r = tb.call("list_campaigns", {})
        assert r["error"]["code"] == "internal_error"


# ---------------------------------------------------------------- spec allowlist
class TestComponentAllowlist:
    def test_arbitrary_classes_are_refused_before_import(self, agent, tmp_path):
        marker = tmp_path / "pwned"
        spec = external_spec()
        spec["oracle"] = {"class": "subprocess.Popen", "params": {"args": ["touch", str(marker)]}}
        r = agent("create_campaign", spec=spec)
        assert r["error"]["code"] == "spec_invalid"
        assert not marker.exists()

    def test_operator_may_allow_extra_classes(self, tmp_path):
        tb = Toolbox(tmp_path / "ws", allow_components=["tests.test_replay.DeterministicOracle"])
        allowed = tb.components.allowed("oracle")
        assert "tests.test_replay.DeterministicOracle" in allowed

    def test_validate_spec_reports_operating_mode(self, agent):
        r = agent("validate_spec", spec=external_spec(driver="external"))["result"]
        assert r["operator_proposes"] and r["evaluated_externally"]


# ---------------------------------------------------------------- idempotency & resume
class TestIdempotency:
    def test_repeated_request_returns_original_response(self, agent):
        agent("create_campaign", spec=external_spec())
        first = agent("reserve_evaluation", request_id="r1")
        again = agent("reserve_evaluation", request_id="r1")
        assert again["replayed"] and again["result"]["evaluation_id"] == first["result"]["evaluation_id"]
        assert len(agent("get_campaign_status")["result"]["pending"]) == 1

    def test_reused_request_id_with_other_args_conflicts(self, agent):
        agent("create_campaign", spec=external_spec())
        agent("reserve_evaluation", request_id="r1")
        r = agent("reserve_evaluation", request_id="r1", tag="different")
        assert r["error"]["code"] == "idempotency_conflict"

    def test_crash_between_commit_and_journal_is_recovered(self, agent, tb):
        agent("create_campaign", spec=external_spec())
        first = agent("reserve_evaluation", request_id="r1")
        (tb.store.path("c") / "requests.jsonl").unlink()  # the crash window
        again = agent("reserve_evaluation", request_id="r1")
        assert again["reconstructed"]
        assert again["result"]["evaluation_id"] == first["result"]["evaluation_id"]

    def test_create_is_idempotent_and_collisions_are_reported(self, agent):
        assert agent("create_campaign", request_id="c1", spec=external_spec())["ok"]
        assert agent("create_campaign", request_id="c1", spec=external_spec())["replayed"]
        assert agent("create_campaign", spec=external_spec())["error"]["code"] == "campaign_exists"


class TestResume:
    def test_a_new_session_continues_the_same_campaign(self, tmp_path):
        a = Agent(Toolbox(tmp_path / "ws"))
        a("create_campaign", spec=external_spec())
        ev = a("reserve_evaluation")["result"]["evaluation_id"]
        b = Agent(Toolbox(tmp_path / "ws"), actor=HUMAN)  # fresh process, other operator
        assert b("settle_evaluation", evaluation_id=ev, outcome="completed", value=1.0)["ok"]
        events = b("get_events")["result"]["events"]
        assert events[-1]["actor"] == HUMAN

    def test_rebuilding_from_disk_every_call_matches_one_live_driver(self, tmp_path):
        # Each tool call resumes the campaign from disk; the proposals must
        # equal those of one in-memory driver that never stopped (ADR-0012).
        spec = external_spec(seconds=1e6, tariff=1.0)
        a, via_tools = Agent(Toolbox(tmp_path / "ws")), []
        a("create_campaign", spec=spec)
        for i in range(6):
            if i % 2 == 0:
                a = Agent(Toolbox(tmp_path / "ws"))  # and a new session now and then
            r = a("reserve_evaluation", request_id=f"r{i}")["result"]
            via_tools.append(r["params"])
            a("settle_evaluation", request_id=f"s{i}", evaluation_id=r["evaluation_id"],
              outcome="completed", value=r["params"]["x"])

        live = CampaignSpec.loads(json.dumps({"schema": "cadaques.spec/1", **spec})).build()
        in_memory = []
        for _ in range(6):
            q = live.driver.propose(live.history, live.budget.view())
            in_memory.append(dict(q.params))
        assert via_tools == in_memory


# ---------------------------------------------------------------- the kernel owns the budget
class TestBudgetAuthority:
    def test_reservations_count_against_admission(self, agent):
        agent("create_campaign", spec=external_spec(seconds=35.0))
        for _ in range(3):
            assert agent("reserve_evaluation")["ok"]
        r = agent("reserve_evaluation")
        assert r["error"]["code"] == "budget_exhausted"
        assert r["error"]["details"]["campaign_stopped"] is False  # work is still in flight

    def test_exhaustion_without_pending_work_stops_the_campaign(self, agent):
        agent("create_campaign", spec=external_spec(seconds=15.0))
        ev = agent("reserve_evaluation")["result"]["evaluation_id"]
        agent("settle_evaluation", evaluation_id=ev, outcome="completed", value=0.5)
        assert agent("reserve_evaluation")["error"]["code"] == "budget_exhausted"
        status = agent("get_campaign_status")["result"]
        assert status["stop_reason"] == "budget_exhausted" and not status["accepting_reservations"]
        assert agent("reserve_evaluation")["error"]["code"] == "campaign_stopped"

    def test_overrun_is_charged_and_recorded(self, agent):
        agent("create_campaign", spec=external_spec())
        ev = agent("reserve_evaluation")["result"]["evaluation_id"]
        r = agent("settle_evaluation", evaluation_id=ev, outcome="completed", value=0.1,
                  settled_cost={"seconds": 25.0}, timings={"queue_seconds": 20.0, "run_seconds": 5.0})
        assert r["result"]["overrun"]["seconds"] == 15.0
        assert r["result"]["status"]["overrun"]["seconds"] == 15.0

    def test_failures_and_cancellations_still_cost(self, agent):
        agent("create_campaign", spec=external_spec())
        e1 = agent("reserve_evaluation")["result"]["evaluation_id"]
        e2 = agent("reserve_evaluation")["result"]["evaluation_id"]
        agent("settle_evaluation", evaluation_id=e1, outcome="failed",
              failure={"kind": "scf_not_converged", "retryable": True}, settled_cost={"seconds": 4.0})
        agent("cancel_evaluation", evaluation_id=e2, reason="preempted")
        s = agent("get_campaign_status")["result"]
        oracle_spent = s["budget"]["spent"]["seconds"]
        assert oracle_spent >= 14.0 and s["n_failures"] == 1 and s["n_cancelled"] == 1
        assert agent("cancel_evaluation", evaluation_id=e2)["error"]["code"] == "already_settled"

    def test_concurrent_operators_cannot_overspend(self, tmp_path):
        tb = Toolbox(tmp_path / "ws")
        Agent(tb)("create_campaign", spec=external_spec(seconds=55.0))
        results = []

        def worker(i):
            results.append(Agent(Toolbox(tmp_path / "ws"))("reserve_evaluation", request_id=f"w{i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        granted = [r["result"]["evaluation_id"] for r in results if r["ok"]]
        assert len(granted) == 5 == len(set(granted))
        assert all(r["error"]["code"] == "budget_exhausted" for r in results if not r["ok"])

    def test_operator_decision_cost_is_metered(self, agent):
        agent("create_campaign", spec=external_spec(driver="external"))
        r = agent("reserve_evaluation", params={"x": 0.3}, decision_cost={"tokens": 1200.0})
        assert r["ok"] and r["result"]["budget"]["spent"]["tokens"] == 1200.0
        assert agent("reserve_evaluation")["error"]["code"] == "invalid_arguments"

    def test_out_of_space_points_are_rejected_on_the_record(self, agent):
        agent("create_campaign", spec=external_spec(driver="external"))
        r = agent("reserve_evaluation", params={"x": 7.0})
        assert r["error"]["code"] == "invalid_query"
        assert agent("get_campaign_status")["result"]["n_rejected"] == 1


# ---------------------------------------------------------------- lifecycle
class TestLifecycle:
    def test_success_stops_admission_but_not_settlement(self, agent):
        agent("create_campaign", spec=external_spec(success=0.9))
        e1 = agent("reserve_evaluation")["result"]["evaluation_id"]
        e2 = agent("reserve_evaluation")["result"]["evaluation_id"]
        agent("settle_evaluation", evaluation_id=e1, outcome="completed", value=0.95)
        assert agent("get_campaign_status")["result"]["stop_reason"] == "success"
        assert agent("reserve_evaluation")["error"]["code"] == "campaign_stopped"
        assert agent("settle_evaluation", evaluation_id=e2, outcome="completed", value=0.2)["ok"]

    def test_stop_is_idempotent(self, agent):
        agent("create_campaign", spec=external_spec())
        assert not agent("stop_campaign", reason="enough")["result"]["already_stopped"]
        assert agent("stop_campaign")["result"]["already_stopped"]

    def test_run_campaign_refuses_external_and_pending_work(self, agent):
        agent("create_campaign", spec=external_spec())
        assert agent("run_campaign", max_new_queries=2)["error"]["code"] == "unsupported_operation"
        a2 = Agent(agent.tb, cid="ising")
        a2("create_campaign", spec=ising_spec())
        a2("reserve_evaluation")
        assert a2("run_campaign", max_new_queries=2)["error"]["code"] == "pending_evaluations"


# ---------------------------------------------------------------- same kernel, same record
class TestKernelEquivalence:
    def test_tool_run_matches_library_run(self, tmp_path):
        a = Agent(Toolbox(tmp_path / "ws"))
        a("create_campaign", spec=ising_spec(seconds=1e6))
        a("run_campaign", max_new_queries=4)
        a("run_campaign", max_new_queries=3)
        tool_points = [e["payload"]["params"] for e in
                       a("get_events", kinds=["oracle_result"])["result"]["events"]]

        library = CampaignSpec.loads(json.dumps({"schema": "cadaques.spec/1",
                                                 **ising_spec(seconds=1e6)})).build()
        out = library.run(max_queries=7)
        assert tool_points == [dict(r.query.params) for r in out.history]

    def test_replay_of_an_agent_operated_campaign(self, agent, tb):
        agent("create_campaign", spec=external_spec())
        e1 = agent("reserve_evaluation")["result"]["evaluation_id"]
        e2 = agent("reserve_evaluation")["result"]["evaluation_id"]
        agent("settle_evaluation", evaluation_id=e1, outcome="completed", value=0.7)
        agent("cancel_evaluation", evaluation_id=e2)
        path = tb.store.path("c")
        state = replay(CampaignSpec.load(path / "spec.json"), path / "events.jsonl")
        assert state.n_queries == 1 and state.n_cancelled == 1 and state.pending == ()
        ledger = Ledger.from_events(EventLog.from_jsonl(path / "events.jsonl"))
        assert [t.meta.get("status") for t in ledger.transactions if t.kind == "oracle"] == [
            "completed", "cancelled"]
        assert tb.store.load("c").budget.spent == state.spent


# ---------------------------------------------------------------- attribution
class TestAttribution:
    def test_every_tool_event_names_actor_and_request(self, agent):
        agent("create_campaign", spec=external_spec())
        ev = agent("reserve_evaluation", request_id="r-9")["result"]["evaluation_id"]
        agent("settle_evaluation", request_id="s-9", evaluation_id=ev, outcome="completed", value=1.0)
        events = agent("get_events")["result"]["events"]
        assert all(e["actor"] == AGENT and e["request_id"] for e in events)
        assert {e["request_id"] for e in events if e["kind"] == "evaluation_reserved"} == {"r-9"}

    def test_library_runs_can_attribute_too(self):
        c = Campaign(Ising2DOracle(default_L=6, default_sweeps=10, default_equilibration=5),
                     RandomDriver(space={"T": (1.5, 3.5)}, seed=0), Budget(total=Cost(seconds=100)),
                     task=Task.from_bounds({"T": (1.5, 3.5)}))
        out = c.run(max_queries=2, actor=HUMAN)
        assert all(e.actor == HUMAN for e in out.events)

    def test_schema_1_logs_still_load(self, tmp_path):
        p = tmp_path / "old.jsonl"
        p.write_text(json.dumps({"schema": "cadaques.events/1", "seq": 0, "kind": "campaign_started",
                                 "timestamp": 0.0, "payload": {"maximize": True}}) + "\n")
        log = EventLog.from_jsonl(p)
        assert log.events[0].actor is None and reduce(log).started


# ---------------------------------------------------------------- ExternalOracle pricing
class TestExternalOracle:
    def test_power_law_pricing(self):
        from cadaques import Query

        o = ExternalOracle(tariff={"seconds": 30.0}, scaling={"resolution": 2.0},
                           reference={"resolution": 16})
        assert o.price(Query(params={}, fidelity={"resolution": 32})) == Cost(seconds=120.0)
        assert o.price(Query(params={})) == Cost(seconds=30.0)

    def test_misconfigured_scaling_fails_at_construction(self):
        with pytest.raises(ValueError):
            ExternalOracle(scaling={"resolution": 2.0})


# ---------------------------------------------------------------- front ends
class TestCommandLine:
    def test_call_prints_envelope_and_sets_exit_code(self, tmp_path, capsys):
        root = str(tmp_path / "ws")
        assert cli_main(["--root", root, "call", "list_campaigns"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"]
        assert cli_main(["--root", root, "call", "get_campaign_status", '{"campaign_id": "x"}']) == 1
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_campaign"
        assert cli_main(["--root", root, "call", "list_campaigns", "{not json"]) == 1

    def test_schemas_command(self, tmp_path, capsys):
        assert cli_main(["--root", str(tmp_path), "schemas"]) == 0
        assert {t["name"] for t in json.loads(capsys.readouterr().out)} == {t.name for t in TOOLS}


class TestMCP:
    def test_tools_are_served_over_mcp(self, tmp_path):
        mcp = pytest.importorskip("mcp")
        import anyio

        from cadaques.agent.mcp_server import build_server

        server = build_server(Toolbox(tmp_path / "ws"))

        async def scenario():
            async with mcp.Client(server) as client:
                listed = await client.list_tools()
                assert {t.name for t in listed.tools} == {t.name for t in TOOLS}
                ok = await client.call_tool("describe_capabilities", {})
                assert not ok.is_error and ok.structured_content["ok"]
                bad = await client.call_tool("get_campaign_status", {"campaign_id": "nope"})
                assert bad.is_error
                assert bad.structured_content["error"]["code"] == "unknown_campaign"

        anyio.run(scenario)
