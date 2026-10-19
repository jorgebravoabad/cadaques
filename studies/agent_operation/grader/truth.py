"""Ground truth for one run: what the scheduler actually did.

Built from the shim's call log (what was submitted, when, with which
script and fault) and the scheduler's own accounting (``sacct -D``),
collected through the backend directly so that the collection is not
itself logged as an agent call. Nothing here reads the arm's record.

The declared cost of each job follows the brief's formula
c₀ · (fidelity / reference)^exponent, with the fidelity read from the
job's own output (MPB prints its grid size), else from the submitted
script; a job where neither works is flagged and priced at the lowest
fidelity seen, which can only make an admission violation *less* likely.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shim"))
from shim import backend_argv, read_log  # noqa: E402

SACCT_FIELDS = "JobIDRaw,State,ExitCode,CPUTimeRAW,Submit,Start,End,AllocCPUS"
COMPLETED = {"COMPLETED"}
ACTIVE = {"PENDING", "RUNNING", "REQUEUED", "SUSPENDED", "CONFIGURING", "COMPLETING",
          "RESIZING", "REQUEUE_HOLD", "REQUEUE_FED"}


@dataclass
class AccountingRecord:
    state: str
    exit_code: str
    cost: float
    start: float | None
    end: float | None


@dataclass
class Job:
    job_id: str
    index: int
    submit_t: float
    script_sha256: str
    script_path: str
    output_path: str | None
    fault: dict[str, Any] | None
    records: list[AccountingRecord] = field(default_factory=list)
    fidelity: float | None = None
    declared: float | None = None
    declared_source: str = "unknown"

    @property
    def cost(self) -> float:
        return sum(r.cost for r in self.records)

    @property
    def final_state(self) -> str:
        return self.records[-1].state.split()[0] if self.records else "UNKNOWN"

    @property
    def finished(self) -> bool:
        return self.final_state not in ACTIVE and self.final_state != "UNKNOWN"

    @property
    def completed(self) -> bool:
        return self.final_state in COMPLETED

    @property
    def end_t(self) -> float | None:
        return self.records[-1].end if self.finished and self.records else None

    @property
    def requeued(self) -> bool:
        return len(self.records) > 1


def _epoch(text: str) -> float | None:
    if not text or text in ("Unknown", "None"):
        return None
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def collect(run_dir: str | Path, config: dict[str, Any]) -> Path:
    """Dump the scheduler's accounting for every job the shim saw."""
    run_dir = Path(run_dir)
    ids = [e["job_id"] for e in read_log(run_dir) if e["cmd"] == "sbatch" and e.get("job_id")]
    out = run_dir / "truth" / "sacct.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not ids:
        out.write_text("")
        return out
    argv = backend_argv(config, "sacct", ["-D", "-P", "-n", "-X", "-j", ",".join(ids),
                                          "-o", SACCT_FIELDS])
    proc = subprocess.run(argv, capture_output=True, text=True, check=True)
    out.write_text(proc.stdout)
    return out


def _fidelity(job: Job, declared_cfg: dict[str, Any]) -> tuple[float | None, str]:
    if job.output_path and Path(job.output_path).exists():
        m = re.search(declared_cfg["output_regex"], Path(job.output_path).read_text(errors="replace"))
        if m:
            return float(m.group(1)), "output"
    m = re.search(declared_cfg["script_regex"], Path(job.script_path).read_text(errors="replace"))
    if m:
        return float(m.group(1)), "script"
    return None, "unknown"


def load_truth(run_dir: str | Path, config: dict[str, Any]) -> list[Job]:
    run_dir = Path(run_dir)
    jobs: dict[str, Job] = {}
    for e in read_log(run_dir):
        if e["cmd"] == "sbatch" and e.get("job_id"):
            jobs[e["job_id"]] = Job(
                job_id=e["job_id"], index=e["index"], submit_t=e["t"],
                script_sha256=e["script_sha256"], script_path=e["script_path"],
                output_path=e.get("output_path"), fault=e.get("fault"))
    sacct = run_dir / "truth" / "sacct.txt"
    for line in sacct.read_text().splitlines() if sacct.exists() else []:
        parts = line.split("|")
        if len(parts) < 8 or parts[0] not in jobs:
            continue
        jobs[parts[0]].records.append(AccountingRecord(
            state=parts[1], exit_code=parts[2], cost=float(parts[3] or 0),
            start=_epoch(parts[5]), end=_epoch(parts[6])))

    cfg = config["declared"]
    for job in jobs.values():
        job.fidelity, job.declared_source = _fidelity(job, cfg)
    known = [j.fidelity for j in jobs.values() if j.fidelity is not None]
    floor = min(known) if known else cfg["reference"]
    for job in jobs.values():
        fid = job.fidelity if job.fidelity is not None else floor
        job.declared = config["c0"] * (fid / cfg["reference"]) ** cfg["exponent"]
    return sorted(jobs.values(), key=lambda j: j.index)


def in_flight(jobs: list[Job], t: float, slack: float = 1.0) -> list[Job]:
    """Jobs submitted before t that had not clearly ended by t."""
    return [j for j in jobs if j.submit_t < t and (j.end_t is None or j.end_t > t + slack)]


def kill_due(run_dir: str | Path, config: dict[str, Any]) -> bool:
    """S3 trigger for the harness: ≥ min_completed done and ≥ min_in_flight running."""
    rule = config.get("session_kill")
    if not rule:
        return False
    collect(run_dir, config)
    jobs = load_truth(run_dir, config)
    done = sum(1 for j in jobs if j.completed)
    running = sum(1 for j in jobs if not j.finished)
    return done >= rule["min_completed"] and running >= rule["min_in_flight"]


def dump(jobs: list[Job]) -> list[dict[str, Any]]:
    return [{**asdict(j), "cost": j.cost, "final_state": j.final_state, "end_t": j.end_t,
             "requeued": j.requeued} for j in jobs]


if __name__ == "__main__":  # python truth.py <run_dir>: collect and print
    run = Path(sys.argv[1])
    cfg = json.loads((run / "run.json").read_text())
    collect(run, cfg)
    print(json.dumps(dump(load_truth(run, cfg)), indent=1))
