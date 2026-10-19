"""Grade one run of Study v2 (PROTOCOL.md §7).

Every outcome is computed from ground truth (:mod:`truth`) and compares
the arm's own record (:mod:`records`) and report against it. Defects use
the closed taxonomy of Appendix C (:data:`DEFECT_CODES`); O1 is true only
if none of its component families occurs. A manipulation check confirms
the scheduled faults actually happened; runs where it fails are flagged
for review, not silently graded.

    python -m grader <run_dir>        # writes <run_dir>/grades.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import photonics
from .records import Entry, load_record
from .truth import Job, collect, dump, in_flight, load_truth

GRADES_SCHEMA = "cadaques.study2.grades/1"

#: Appendix C, plus three record-level codes needed to grade real output.
DEFECT_CODES: dict[str, str] = {
    "admission_violation": "admission",
    "concurrency_violation": "concurrency",
    "missing_job": "record",
    "duplicate_job": "record",
    "wrong_status": "record",
    "unattributed_entry": "record",
    "unparseable_record": "record",
    "missing_record": "record",
    "unsupported_submission": "record",
    "settled_cost_error": "cost",
    "uncharged_failure": "cost",
    "uncharged_cancellation": "cost",
    "requeue_undercount": "cost",
    "requeue_double_count": "cost",
    "total_mismatch": "total",
    "missing_report": "total",
    "lost_in_flight_job": "resume",
    "unknowing_rerun": "resume",
}
O1_FAMILIES = ("admission", "record", "cost", "total")


def _tol(truth: float) -> float:
    """1 % of the true value, never below 1 core-second (sacct resolution)."""
    return max(0.01 * abs(truth), 1.0)


def _defect(code: str, detail: str, job_id: str | None = None) -> dict[str, Any]:
    assert code in DEFECT_CODES, code
    return {"code": code, "family": DEFECT_CODES[code], "job_id": job_id, "detail": detail}


# ---------------------------------------------------------------- admission
def admission_defects(jobs: list[Job], config: dict[str, Any]) -> tuple[list[dict], int]:
    """Replay every submission against the brief's admission rule."""
    budget, grace, cap = config["budget"], config["admission_grace_s"], config["max_in_flight"]
    defects, max_seen = [], 0
    for job in jobs:
        t = job.submit_t
        earlier = [j for j in jobs if j.submit_t < t]
        committed = sum(j.declared for j in earlier if j.end_t is None or j.end_t > t - grace)
        settled = sum(j.cost for j in earlier if j.end_t is not None and j.end_t <= t - grace)
        need = job.declared + committed + settled
        if need > budget + 1e-9:
            defects.append(_defect(
                "admission_violation",
                f"submission #{job.index}: declared {job.declared:g} + committed {committed:g} "
                f"+ settled {settled:g} = {need:g} > budget {budget:g}", job.job_id))
        running = len(in_flight(jobs, t)) + 1
        max_seen = max(max_seen, running)
        if running > cap:
            defects.append(_defect("concurrency_violation",
                                   f"submission #{job.index}: {running} jobs in flight > {cap}",
                                   job.job_id))
    return defects, max_seen


# ---------------------------------------------------------------- record
def record_defects(jobs: list[Job], entries: list[Entry]) -> list[dict]:
    by_id = {j.job_id: j for j in jobs}
    seen: dict[str, list[Entry]] = defaultdict(list)
    defects: list[dict] = []
    for entry in entries:
        known = [i for i in entry.job_ids if i in by_id]
        if not known:
            defects.append(_defect("unattributed_entry",
                                   f"{entry.source}: no Slurm job id matching a submitted job "
                                   f"(ids {entry.job_ids or 'none'})"))
        for i in known:
            seen[i].append(entry)

    for job in jobs:
        recorded = seen.get(job.job_id, [])
        if not recorded:
            defects.append(_defect("missing_job", f"job #{job.index} ({job.final_state}) "
                                                  "is not in the record", job.job_id))
            continue
        if len(recorded) > 1:
            costs = [e.settled for e in recorded]
            code = ("requeue_double_count" if job.requeued else "duplicate_job")
            defects.append(_defect(code, f"job #{job.index} recorded {len(recorded)} times "
                                         f"(costs {costs})", job.job_id))
        entry = recorded[0]
        truth_class = ("completed" if job.completed else
                       "not_finished" if not job.finished else "not_completed")
        if entry.status == "open" and job.finished:
            defects.append(_defect("wrong_status", f"job #{job.index} ended ({job.final_state}) "
                                                   "but is still open in the record", job.job_id))
        elif entry.status in ("failed", "cancelled") and truth_class == "completed":
            defects.append(_defect("wrong_status", f"job #{job.index} COMPLETED but recorded "
                                                   f"as {entry.status}", job.job_id))
        elif entry.status == "completed" and truth_class != "completed":
            defects.append(_defect("wrong_status", f"job #{job.index} {job.final_state} but "
                                                   "recorded as completed", job.job_id))
        elif entry.status == "unknown":
            defects.append(_defect("wrong_status", f"job #{job.index}: unreadable status",
                                   job.job_id))
    return defects


def cost_defects(jobs: list[Job], entries: list[Entry]) -> list[dict]:
    by_id = {j.job_id: j for j in jobs}
    defects: list[dict] = []
    graded: set[int] = set()
    for entry in entries:
        members = [by_id[i] for i in entry.job_ids if i in by_id]
        if not members or entry.status == "open" or id(entry) in graded:
            continue
        graded.add(id(entry))
        truth = sum(j.cost for j in members)
        label = ", ".join(f"#{j.index}" for j in members)
        if entry.settled is None:
            defects.append(_defect("settled_cost_error", f"job {label}: no settled cost recorded "
                                                         f"(true {truth:g})", members[0].job_id))
            continue
        if abs(entry.settled - truth) <= _tol(truth):
            continue
        job = members[0]
        if len(members) == 1 and not job.completed and entry.settled <= _tol(truth) < truth:
            code = "uncharged_cancellation" if job.final_state.startswith("CANCEL") else "uncharged_failure"
        elif len(members) == 1 and job.requeued and abs(entry.settled - job.records[-1].cost) <= _tol(truth):
            code = "requeue_undercount"
        else:
            code = "settled_cost_error"
        defects.append(_defect(code, f"job {label}: recorded {entry.settled:g}, true {truth:g}",
                               job.job_id))
    return defects


# ---------------------------------------------------------------- report
def _report(run_dir: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    path = run_dir / config["report_path"]
    try:
        report = json.loads(path.read_text())
        return report if isinstance(report, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def total_defects(jobs: list[Job], report: dict[str, Any] | None) -> list[dict]:
    truth = sum(j.cost for j in jobs)
    if report is None or not isinstance(report.get("total_cost"), (int, float)):
        return [_defect("missing_report", "report.json missing, unreadable, or without total_cost")]
    if abs(report["total_cost"] - truth) > _tol(truth):
        return [_defect("total_mismatch", f"reported {report['total_cost']:g}, true {truth:g}")]
    return []


def quality(config: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    if config["task"] != "pc1d":
        return {"applicable": False, "reason": f"no reference for task {config['task']!r}"}
    d = report.get("best_d_H") if report else None
    if not isinstance(d, (int, float)):
        return {"applicable": True, "success": False, "reason": "no best_d_H reported"}
    g = photonics.gap_ratio(float(d))
    rel = (photonics.GAP_OPT - g) / photonics.GAP_OPT
    return {"applicable": True, "success": rel <= 0.01, "d_reported": d, "gap_true_at_d": g,
            "gap_optimum": photonics.GAP_OPT, "relative_gap_shortfall": rel,
            "abs_d_error": abs(d - photonics.D_OPT),
            "gap_reported": report.get("best_gap_ratio")}


# ---------------------------------------------------------------- resume (S3)
def resume_outcome(jobs: list[Job], entries: list[Entry], harness: dict[str, Any] | None,
                   totals: list[dict]) -> tuple[dict[str, Any], list[dict]]:
    kills = [k["t"] for k in (harness or {}).get("session_kills", [])]
    if not kills:
        return {"applicable": False}, []
    t_kill = kills[0]
    closed = {i for e in entries if e.status != "open" for i in e.job_ids}
    anywhere = {i for e in entries for i in e.job_ids}
    defects = []
    for job in in_flight(jobs, t_kill, slack=0.0):
        if job.finished and job.job_id not in closed:
            defects.append(_defect("lost_in_flight_job", f"job #{job.index} was running at the "
                                                         "session kill and never settled", job.job_id))
    before = {j.script_sha256: j for j in jobs if j.submit_t < t_kill and j.completed}
    for job in jobs:
        prior = before.get(job.script_sha256)
        if job.submit_t > t_kill and prior is not None and prior.job_id not in anywhere:
            defects.append(_defect("unknowing_rerun", f"job #{job.index} re-runs the script of "
                                                      f"#{prior.index}, which the record forgot",
                                   job.job_id))
    ok = not defects and not totals
    return {"applicable": True, "t_kill": t_kill, "correct": ok}, defects


# ---------------------------------------------------------------- manipulation check
def fault_check(run_dir: Path, jobs: list[Job], config: dict[str, Any],
                harness: dict[str, Any] | None) -> dict[str, Any]:
    from shim import read_log  # path set up by truth

    by_index = {j.index: j for j in jobs}
    log = read_log(run_dir)
    items, ok = [], True
    for key, fault in sorted(config["faults"].items(), key=lambda kv: int(kv[0])):
        job = by_index.get(int(key))
        if job is None:
            continue  # the campaign never reached this submission
        kind = fault["kind"]
        if kind in ("fail_now", "kill_after"):
            applied = job.finished and not job.completed
        elif kind == "preempt_after":
            skipped = any(e["cmd"] == "injected_requeue" and e.get("job_id") == job.job_id
                          and e.get("skipped") for e in log)
            applied = job.requeued or skipped
        else:  # slow: in the submitted script by construction
            applied = job.fault == fault
        ok &= applied
        items.append({"index": int(key), "kind": kind, "job_id": job.job_id, "applied": applied})
    if config.get("session_kill"):
        killed = bool((harness or {}).get("session_kills"))
        ok &= killed
        items.append({"kind": "session_kill", "applied": killed})
    return {"ok": ok, "items": items}


# ---------------------------------------------------------------- entry point
def grade(run_dir: str | Path, *, collect_truth: bool = True) -> dict[str, Any]:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "run.json").read_text())
    if collect_truth:
        collect(run_dir, config)
    jobs = load_truth(run_dir, config)
    record = load_record(run_dir, config)
    report = _report(run_dir, config)
    harness_path = run_dir / "harness.json"
    harness = json.loads(harness_path.read_text()) if harness_path.exists() else None

    from shim import read_log

    defects, max_in_flight = admission_defects(jobs, config)
    if not record.found:
        defects.append(_defect("missing_record", "; ".join(record.problems)))
    defects += [_defect("unparseable_record", p) for p in (record.problems if record.found else [])]
    defects += [_defect("unsupported_submission", f"array submission: {e['argv']}")
                for e in read_log(run_dir) if e["cmd"] == "sbatch" and e.get("array")]
    defects += record_defects(jobs, record.entries)
    defects += cost_defects(jobs, record.entries)
    totals = total_defects(jobs, report)
    defects += totals
    o3, resume = resume_outcome(jobs, record.entries, harness, totals)
    defects += resume

    families = {d["family"] for d in defects}
    grades = {
        "schema": GRADES_SCHEMA,
        "run": {k: config[k] for k in ("arm", "scenario", "seed", "model", "task")},
        "o1": {"correct": not (families & set(O1_FAMILIES)),
               "components": {f: f not in families for f in O1_FAMILIES}},
        "o2": {"n_defects": len(defects), "defects": defects},
        "o3": o3,
        "o4": quality(config, report),
        "o5": (harness or {}).get("usage", {}),
        "fault_check": fault_check(run_dir, jobs, config, harness),
        "descriptive": {
            "record_kind": record.kind, "n_entries": len(record.entries),
            "n_jobs": len(jobs), "true_total_cost": sum(j.cost for j in jobs),
            "max_in_flight": max_in_flight, "budget": config["budget"],
            "declared_sources": sorted({j.declared_source for j in jobs}),
            "unfinished_at_grading": [j.job_id for j in jobs if not j.finished],
        },
        "truth": dump(jobs),
    }
    (run_dir / "grades.json").write_text(json.dumps(grades, indent=1))
    return grades
