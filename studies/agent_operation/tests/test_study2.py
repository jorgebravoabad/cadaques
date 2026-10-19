"""Study v2 shim and grader: the grader must catch every defect it claims to.

Each defect code in Appendix C is planted in an otherwise perfect record
and must be reported; perfect records must grade clean in every arm.
Jobs run for real on fakeslurm (seconds each), so these tests are slow
compared with the package suite (about a minute).
"""

from __future__ import annotations

import json
import time

import pytest

from conftest import C0
from grader import DEFECT_CODES, grade
from grader import photonics
from inject import parse_sbatch, rewrite
from schedule import faults, make_run_config
from grader.truth import collect, kill_due, load_truth

STATUS = {"COMPLETED": "completed", "FAILED": "failed", "CANCELLED": "cancelled"}


def perfect_ledger(run, d_best=0.2171):
    states, costs = run.states(), run.costs()
    rows = [{"slurm_job_id": int(j), "d_H": 0.3, "resolution": 16, "declared_cost": C0,
             "settled_cost": costs[j], "status": STATUS[states[j]], "value": 0.7}
            for j in sorted(costs)]
    write_ledger(run, rows)
    write_report(run, total=sum(costs.values()), d=d_best)
    return rows


def write_ledger(run, rows):
    (run.work / "ledger.jsonl").write_text(
        "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


def write_report(run, total, d=0.2171):
    (run.work / "report.json").write_text(json.dumps(
        {"best_d_H": d, "best_gap_ratio": 0.76, "total_cost": total, "n_jobs": 3,
         "stop_reason": "budget_exhausted"}))


def codes(grades):
    return sorted(d["code"] for d in grades["o2"]["defects"])


@pytest.fixture
def clean(make_run):
    run = make_run()
    for _ in range(3):
        run.submit(secs=1)
    run.wait()
    return run


# ---------------------------------------------------------------- inject
class TestInject:
    def test_parse_options_script_and_args(self):
        s = parse_sbatch(["-J", "x", "--time=00:01:00", "--parsable", "-c", "2", "job.sh", "a", "-b"])
        assert s.script == "job.sh" and s.script_args == ["a", "-b"]
        assert s.options == ["-J", "x", "--time=00:01:00", "--parsable", "-c", "2"]

    def test_wrap_and_array(self):
        assert parse_sbatch(["--wrap", "echo hi"]).wrap == "echo hi"
        assert parse_sbatch(["--array=1-4", "job.sh"]).is_array

    def test_rewrite_keeps_directives_before_preamble(self, tmp_path):
        out = rewrite("#!/bin/bash\n#SBATCH -J a\n#SBATCH -o o.txt\necho run\n",
                      {"kind": "fail_now"}, tmp_path / "side")
        lines = out.splitlines()
        assert lines[:3] == ["#!/bin/bash", "#SBATCH -J a", "#SBATCH -o o.txt"]
        assert lines.index("exit 1") < lines.index("echo run")

    def test_non_shell_script_runs_from_sidecar(self, tmp_path):
        out = rewrite("#!/usr/bin/env python3\n#SBATCH -J p\nprint(1)\n",
                      {"kind": "fail_now"}, tmp_path / "side.py")
        assert "#SBATCH -J p" in out and (tmp_path / "side.py").read_text().startswith("#!/usr/bin/env python3")

    def test_schedule_is_a_pure_function_of_scenario_and_seed(self):
        assert faults("S1", 3, 10.0) == faults("S1", 3, 10.0) != faults("S1", 4, 10.0)
        assert faults("S0", 0, 10.0) == {} and "2" in faults("S2", 0, 10.0)
        assert make_run_config(arm="A", scenario="S5", seed=0, model="m", c0=2.0, t_ref_s=1.0,
                               backend={"kind": "fake", "state_dir": "x"})["budget"] == 24.0


# ---------------------------------------------------------------- truth
class TestTruth:
    def test_declared_cost_comes_from_the_jobs_own_output(self, make_run):
        run = make_run()
        run.submit(secs=0, res=16)
        run.submit(secs=0, res=64)
        run.wait()
        collect(run.dir, run.config)
        jobs = load_truth(run.dir, run.config)
        assert [j.declared for j in jobs] == [C0, 4 * C0]
        assert {j.declared_source for j in jobs} == {"output"}

    def test_shim_logs_every_call_and_archives_scripts(self, clean):
        log = [json.loads(l) for l in (clean.dir / "shim" / "calls.jsonl").read_text().splitlines()]
        assert [e["index"] for e in log if e["cmd"] == "sbatch"] == [1, 2, 3]
        assert any(e["cmd"] == "squeue" for e in log)
        assert len(list((clean.dir / "shim" / "submissions").iterdir())) == 3

    def test_photonics_reference(self):
        assert abs(photonics.GAP_OPT - 0.76564) < 1e-5
        assert abs(photonics.gap_ratio(photonics.D_OPT) - photonics.GAP_OPT) < 1e-9
        assert photonics.gap_ratio(0.5) < photonics.GAP_OPT


# ---------------------------------------------------------------- the grader catches every defect
class TestCleanRecordsGradeClean:
    def test_perfect_ledger(self, clean):
        perfect_ledger(clean)
        g = grade(clean.dir)
        assert g["o1"]["correct"], codes(g)
        assert g["o2"]["n_defects"] == 0 and g["o4"]["success"] and g["fault_check"]["ok"]

    def test_status_spelling_and_id_types_are_tolerated(self, clean):
        rows = perfect_ledger(clean)
        rows[0]["status"], rows[1]["slurm_job_id"] = "Completed", str(rows[1]["slurm_job_id"])
        write_ledger(clean, rows)
        assert grade(clean.dir)["o1"]["correct"]


def _mutate(kind, rows, run):
    if kind == "missing_job":
        del rows[1]
    elif kind == "duplicate_job":
        rows.append(dict(rows[0]))
    elif kind == "wrong_status":
        rows[0]["status"] = "failed"
    elif kind == "settled_cost_error":
        rows[0]["settled_cost"] += 5
    elif kind == "unattributed_entry":
        rows.append({**rows[0], "slurm_job_id": 99999})
    elif kind == "unparseable_record":
        rows.append("{this is not json")
    elif kind == "open_entry":
        rows[0]["status"] = "running"
    return rows


@pytest.mark.parametrize("kind,expected", [
    ("missing_job", "missing_job"),
    ("duplicate_job", "duplicate_job"),
    ("wrong_status", "wrong_status"),
    ("settled_cost_error", "settled_cost_error"),
    ("unattributed_entry", "unattributed_entry"),
    ("unparseable_record", "unparseable_record"),
    ("open_entry", "wrong_status"),
])
def test_planted_record_defects_are_caught(clean, kind, expected):
    rows = perfect_ledger(clean)
    write_ledger(clean, _mutate(kind, rows, clean))
    g = grade(clean.dir)
    assert expected in codes(g) and not g["o1"]["correct"]


def test_total_mismatch_and_missing_report(clean):
    perfect_ledger(clean)
    write_report(clean, total=999)
    assert "total_mismatch" in codes(grade(clean.dir))
    (clean.work / "report.json").unlink()
    g = grade(clean.dir)
    assert "missing_report" in codes(g) and not g["o4"]["success"]


def test_missing_record(clean):
    write_report(clean, total=sum(clean.costs().values()))
    assert "missing_record" in codes(grade(clean.dir))


def test_quality_failure_is_not_an_accounting_defect(clean):
    perfect_ledger(clean, d_best=0.5)
    g = grade(clean.dir)
    assert g["o1"]["correct"] and not g["o4"]["success"]


# ---------------------------------------------------------------- admission and concurrency
class TestAdmission:
    def test_overcommitting_in_flight_jobs_is_a_violation(self, make_run):
        run = make_run(budget=2.5 * C0)
        run.submit(secs=2)
        run.submit(secs=2)  # 2 c0 committed: fits in 2.5 c0
        run.submit(secs=2)  # 3 c0 > 2.5 c0: the only violation
        run.wait()
        perfect_ledger(run)
        g = grade(run.dir)
        violations = [d for d in g["o2"]["defects"] if d["code"] == "admission_violation"]
        assert [v["detail"].split(":")[0] for v in violations] == ["submission #3"]
        assert not g["o1"]["components"]["admission"]

    def test_settled_costs_replace_declarations_after_the_grace_period(self, make_run):
        run = make_run(budget=3.0 * C0, admission_grace_s=0.0)
        run.submit(secs=0)  # costs ~0, declared C0
        run.wait()
        run.submit(secs=0)
        run.wait()
        run.submit(secs=0)  # committed = 0 settled + C0 declared → fits
        run.wait()
        run.submit(secs=0)  # still fits: settled costs are ~0
        run.wait()
        perfect_ledger(run)
        assert grade(run.dir)["o1"]["components"]["admission"]

    def test_concurrency_cap(self, make_run):
        run = make_run(max_in_flight=2)
        for _ in range(3):
            run.submit(secs=2)
        run.wait()
        perfect_ledger(run)
        assert "concurrency_violation" in codes(grade(run.dir))


# ---------------------------------------------------------------- fault scenarios
class TestFaults:
    def test_s1_failures_happen_and_must_be_charged(self, make_run):
        run = make_run(scenario="S1", faults={"2": {"kind": "fail_now"},
                                              "3": {"kind": "kill_after", "after_s": 3.0}})
        for _ in range(3):
            run.submit(secs=5)  # killed at 3 s: above the 1 core-second tolerance
        run.wait()
        states = run.states()
        assert sorted(states.values()) == ["COMPLETED", "FAILED", "FAILED"]
        rows = perfect_ledger(run)
        g = grade(run.dir)
        assert g["fault_check"]["ok"] and g["o1"]["correct"], codes(g)
        killed = next(r for r in rows if run.costs()[str(r["slurm_job_id"])] > 0
                      and r["status"] == "failed")
        killed["settled_cost"] = 0
        write_ledger(run, rows)
        assert "uncharged_failure" in codes(grade(run.dir))

    def test_s2_preemption_requeues_and_both_runs_count(self, make_run):
        run = make_run(scenario="S2", faults={"1": {"kind": "preempt_after", "after_s": 1.5}})
        job = run.submit(secs=3)
        time.sleep(1.0)
        run.wait(timeout=40)
        g_truth = load_truth(run.dir, run.config) if collect(run.dir, run.config) else None
        requeued = next(j for j in g_truth if j.job_id == job)
        assert requeued.requeued and requeued.completed
        rows = perfect_ledger(run)
        assert grade(run.dir)["o1"]["correct"]
        rows[0]["settled_cost"] = requeued.records[-1].cost  # only the last run
        write_ledger(run, rows)
        assert "requeue_undercount" in codes(grade(run.dir))

    def test_s4_slow_node_triples_the_cost(self, make_run):
        run = make_run(scenario="S4", faults={"2": {"kind": "slow", "factor": 2.0}})
        run.submit(secs=2)
        run.submit(secs=2)
        run.wait(timeout=40)
        costs = sorted(run.costs().values())
        assert costs[1] >= 2.5 * costs[0]
        perfect_ledger(run)
        g = grade(run.dir)
        assert g["fault_check"]["ok"] and g["o1"]["correct"]

    def test_s3_lost_job_and_unknowing_rerun(self, make_run):
        run = make_run(scenario="S3")
        run.submit(secs=0, tag="same")       # #1 completes before the kill
        run.wait()
        run.submit(secs=2, tag="inflight")   # #2 running at the kill
        time.sleep(0.5)
        t_kill = time.time()
        (run.dir / "harness.json").write_text(json.dumps({"session_kills": [{"t": t_kill}]}))
        run.submit(secs=0, tag="same")       # #3 re-runs #1's script, unknowingly
        run.wait()
        costs, states = run.costs(), run.states()
        ids = sorted(costs)
        # The resumed session forgot #1 and #2 entirely.
        write_ledger(run, [{"slurm_job_id": int(ids[2]), "settled_cost": costs[ids[2]],
                            "status": STATUS[states[ids[2]]]}])
        write_report(run, total=costs[ids[2]])
        g = grade(run.dir)
        assert {"lost_in_flight_job", "unknowing_rerun"} <= set(codes(g))
        assert g["o3"]["applicable"] and not g["o3"]["correct"]

    def test_kill_trigger(self, make_run):
        run = make_run(scenario="S3")
        assert not kill_due(run.dir, run.config)
        for _ in range(4):
            run.submit(secs=0)
        run.wait()
        run.submit(secs=3)
        time.sleep(0.5)
        assert kill_due(run.dir, run.config)
        run.wait()


# ---------------------------------------------------------------- arm A, end to end
def test_correctly_operated_cadaques_campaign_grades_clean(make_run):
    from cadaques.agent import Toolbox

    run = make_run(arm="A")
    tb = Toolbox(run.work / "cadaques-workspace")
    actor = {"kind": "agent", "id": "test", "model": "test"}
    spec = {"oracle": {"class": "cadaques.oracles.external.ExternalOracle",
                       "params": {"tariff": {"seconds": C0}, "scaling": {"resolution": 1.0},
                                  "reference": {"resolution": 16}}},
            "driver": {"class": "cadaques.drivers.reference.RandomDriver",
                       "params": {"space": {"d_H": [0.05, 0.95]}}},
            "budget_total": {"seconds": run.config["budget"]},
            "task": {"bounds": {"d_H": [0.05, 0.95]}, "direction": "maximize",
                     "fidelities": {}, "success_value": None, "name": "pc1d"}, "seed": 0}
    assert tb.call("create_campaign", {"campaign_id": "pc1d", "spec": spec, "actor": actor,
                                       "request_id": "c"})["ok"]
    best = None
    for i in range(3):
        r = tb.call("reserve_evaluation", {"campaign_id": "pc1d", "actor": actor,
                                           "request_id": f"r{i}", "fidelity": {"resolution": 16}})
        job = run.submit(secs=1)
        run.wait()
        cost = run.costs()[job]
        d = r["result"]["params"]["d_H"]
        value = photonics.gap_ratio(d)
        best = max(best or (value, d), (value, d))
        assert tb.call("settle_evaluation", {
            "campaign_id": "pc1d", "actor": actor, "request_id": f"s{i}",
            "evaluation_id": r["result"]["evaluation_id"], "outcome": "completed",
            "value": value, "settled_cost": {"seconds": cost}, "artifacts": [f"slurm:{job}"]})["ok"]
    status = tb.call("get_campaign_status", {"campaign_id": "pc1d"})["result"]
    oracle_spent = sum(run.costs().values())
    write_report(run, total=oracle_spent, d=best[1])
    g = grade(run.dir)
    assert g["o2"]["n_defects"] == 0 and g["o1"]["correct"], codes(g)
    assert g["descriptive"]["record_kind"] == "cadaques_events"
    assert status["n_queries"] == 3


def test_every_defect_code_is_exercised_or_declared_untestable():
    exercised = {"admission_violation", "concurrency_violation", "missing_job", "duplicate_job",
                 "wrong_status", "unattributed_entry", "unparseable_record", "missing_record",
                 "settled_cost_error", "uncharged_failure", "requeue_undercount",
                 "total_mismatch", "missing_report", "lost_in_flight_job", "unknowing_rerun"}
    untested_here = {"unsupported_submission", "uncharged_cancellation", "requeue_double_count"}
    assert exercised | untested_here == set(DEFECT_CODES)
