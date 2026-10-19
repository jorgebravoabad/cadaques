"""Read an arm's own campaign record into one comparable form.

Arm A keeps its record as a CADAQUES event log (Slurm ids in each
settlement's ``artifacts``); arm B as ``ledger.jsonl`` with the fields its
brief specifies; arm C may do either. Each becomes a list of
:class:`Entry`: one evaluation as the agent recorded it. Parsing is
lenient about spelling (``"Completed"``, ``"canceled"``, ids as numbers)
and strict about substance: a line that cannot be read is reported, not
skipped silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_STATUS = {
    "completed": "completed", "complete": "completed", "success": "completed",
    "succeeded": "completed", "ok": "completed", "done": "completed", "finished": "completed",
    "failed": "failed", "failure": "failed", "error": "failed", "fail": "failed",
    "node_fail": "failed", "timeout": "failed", "timed_out": "failed",
    "cancelled": "cancelled", "canceled": "cancelled", "preempted": "cancelled",
    "pending": "open", "running": "open", "submitted": "open", "reserved": "open",
    "queued": "open", "in_flight": "open",
}


@dataclass
class Entry:
    job_ids: list[str]
    status: str  # completed | failed | cancelled | open | unknown
    settled: float | None
    declared: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass
class Record:
    kind: str
    entries: list[Entry]
    problems: list[str]
    found: bool


def normalize_status(value: Any) -> str:
    return _STATUS.get(str(value).strip().lower().replace(" ", "_"), "unknown")


def _ids(values: Any) -> list[str]:
    """Slurm job ids from an id field or a list of artifact strings."""
    if values is None:
        return []
    if isinstance(values, (int, float)) and not isinstance(values, bool):
        return [str(int(values))]
    if isinstance(values, str):
        values = [values]
    ids: list[str] = []
    for v in values:
        s = str(v).strip()
        if re.fullmatch(r"\d+", s):
            ids.append(s)
        else:
            ids += re.findall(r"slurm\D{0,10}?(\d+)", s, flags=re.I)
    return list(dict.fromkeys(ids))


def _num(value: Any) -> float | None:
    if isinstance(value, dict):  # a CADAQUES cost dict
        value = value.get("seconds")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_events(path: Path) -> Record:
    entries: dict[str, Entry] = {}
    problems: list[str] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"events line {n}: not JSON")
            continue
        p, kind = event.get("payload", {}), event.get("kind")
        eid = p.get("evaluation_id")
        if kind == "evaluation_reserved":
            entries[eid] = Entry(job_ids=[], status="open", settled=None,
                                 declared=_num(p.get("declared")), params=p.get("params", {}),
                                 source=f"events:{eid}")
        elif kind in ("oracle_result", "oracle_failure", "evaluation_cancelled") and eid:
            entry = entries.setdefault(eid, Entry(job_ids=[], status="open", settled=None,
                                                  source=f"events:{eid}"))
            entry.status = {"oracle_result": "completed", "oracle_failure": "failed",
                            "evaluation_cancelled": "cancelled"}[kind]
            entry.settled = _num(p.get("settled"))
            entry.job_ids = _ids(p.get("artifacts", [])) + _ids(p.get("reason", "") if
                                                                 kind == "evaluation_cancelled" else [])
            entry.job_ids = list(dict.fromkeys(entry.job_ids))
    return Record("cadaques_events", list(entries.values()), problems, True)


def read_ledger(path: Path) -> Record:
    entries: list[Entry] = []
    problems: list[str] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            problems.append(f"ledger line {n}: not a JSON object")
            continue
        entries.append(Entry(
            job_ids=_ids(row.get("slurm_job_id", row.get("job_id"))),
            status=normalize_status(row.get("status")),
            settled=_num(row.get("settled_cost")),
            declared=_num(row.get("declared_cost")),
            params={k: row[k] for k in ("d_H", "resolution", "value") if k in row},
            source=f"ledger:{n}",
        ))
    return Record("ledger", entries, problems, True)


def load_record(run_dir: str | Path, config: dict[str, Any]) -> Record:
    run_dir = Path(run_dir)
    spec = config["record"]
    candidates = []
    if spec["kind"] in ("cadaques_events", "auto"):
        candidates.append(("events", run_dir / (spec["path"] if spec["kind"] == "cadaques_events"
                                                else spec["events_path"])))
    if spec["kind"] in ("ledger", "auto"):
        candidates.append(("ledger", run_dir / spec["path"]))
    for kind, path in candidates:
        if path.exists():
            return read_events(path) if kind == "events" else read_ledger(path)
    return Record("missing", [], [f"no campaign record at {[str(p) for _, p in candidates]}"], False)
