"""A minimal local Slurm for tests and the laptop pilot.

Jobs run as real local processes (one session each); accounting follows
Slurm's conventions closely enough for the grader and for an agent:
``CPUTimeRAW`` = elapsed seconds × allocated CPUs, requeued jobs keep
their id and leave one accounting record per run (``sacct -D``), a
cancelled pending job costs nothing. Supported:

    sbatch   [-J|--job-name] [-o|--output] [-D|--chdir] [-c|--cpus-per-task]
             [--parsable] [--wrap] script [args]      (+ #SBATCH directives)
    squeue   [-h] [-j ids] [-u user] [-o|--format %i %j %T %t %M %u %P %N]
    sacct    [-j ids] [-o|--format fields] [-P|-p] [-n] [-X] [-D] [--units, -S ignored]
    scancel  ids
    scontrol requeue ids | show job id

State lives in ``<state_dir>/state.json`` under an exclusive lock.
Usage: ``python fakeslurm.py <state_dir> <command> [args...]``.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).parent))
from inject import directive_value, option_value, parse_sbatch  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}


@contextmanager
def _state(state_dir: Path) -> Iterator[dict[str, Any]]:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = state_dir / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {"next_id": 1000, "jobs": {}}
        yield state
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _ts(t: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if t else "Unknown"


def _elapsed(run: dict[str, Any]) -> float:
    if run.get("start") is None:
        return 0.0
    return (run.get("end") or time.time()) - run["start"]


def _job_state(job: dict[str, Any]) -> str:
    # job["state"] is authoritative: a requeued job is PENDING between runs,
    # even though its last run record already says REQUEUED.
    return job["state"]


# ---------------------------------------------------------------- sbatch
def sbatch(state_dir: Path, argv: list[str]) -> int:
    sub = parse_sbatch(argv)
    if sub.wrap is not None:
        text = "#!/bin/sh\n" + sub.wrap + "\n"
    elif sub.script is not None:
        text = Path(sub.script).read_text()
    else:
        text = sys.stdin.read()

    def opt(short: str | None, long: str) -> str | None:
        return option_value(sub.options, short, long) or directive_value(text, short, long)

    workdir = Path(opt("-D", "--chdir") or os.getcwd()).resolve()
    with _state(state_dir) as state:
        job_id = str(state["next_id"])
        state["next_id"] += 1
        name = opt("-J", "--job-name") or (Path(sub.script).name if sub.script else "wrap")
        output = (opt("-o", "--output") or "slurm-%j.out").replace("%j", job_id).replace("%x", name)
        script_copy = state_dir / f"job-{job_id}.sh"
        script_copy.write_text(text)
        script_copy.chmod(0o755)
        state["jobs"][job_id] = {
            "id": job_id, "name": name, "script": str(script_copy), "args": sub.script_args,
            "workdir": str(workdir), "output": str(workdir / output),
            "ncpus": int(opt("-c", "--cpus-per-task") or 1),
            "submit": time.time(), "state": "PENDING", "runs": [],
        }
    _spawn_runner(state_dir, job_id)
    print(job_id if "--parsable" in sub.options else f"Submitted batch job {job_id}")
    return 0


def _spawn_runner(state_dir: Path, job_id: str) -> None:
    subprocess.Popen(
        [sys.executable, "-I", __file__, str(state_dir), "_run", job_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _run(state_dir: Path, job_id: str) -> int:
    delay = float(os.environ.get("FAKESLURM_QUEUE_DELAY", "0"))
    time.sleep(delay)
    with _state(state_dir) as state:
        job = state["jobs"][job_id]
        if job["state"] == "CANCELLED":
            return 0
        run = {"start": time.time(), "end": None, "state": "RUNNING", "exit_code": None}
        job["runs"].append(run)
        job["state"] = "RUNNING"
        index = len(job["runs"]) - 1
        env = {**os.environ, "SLURM_JOB_ID": job_id, "SLURM_JOB_NAME": job["name"],
               "SLURM_CPUS_PER_TASK": str(job["ncpus"]), "SLURM_SUBMIT_DIR": job["workdir"]}
        with open(job["output"], "ab") as out:
            proc = subprocess.Popen(["bash", job["script"], *job["args"]], cwd=job["workdir"],
                                    stdout=out, stderr=subprocess.STDOUT, env=env,
                                    start_new_session=True)
        run["pid"] = proc.pid
    rc = proc.wait()
    with _state(state_dir) as state:
        job = state["jobs"][job_id]
        run = job["runs"][index]
        if run["state"] == "RUNNING":  # not already ended by scancel/requeue
            run["end"] = time.time()
            run["exit_code"] = rc
            run["state"] = "COMPLETED" if rc == 0 else "FAILED"
            job["state"] = run["state"]
    return 0


def _kill(run: dict[str, Any]) -> None:
    try:
        os.killpg(run["pid"], signal.SIGKILL)
    except (ProcessLookupError, KeyError, PermissionError):
        pass


# ---------------------------------------------------------------- control
def scancel(state_dir: Path, argv: list[str]) -> int:
    with _state(state_dir) as state:
        for job_id in [a for a in argv if not a.startswith("-")]:
            job = state["jobs"].get(job_id)
            if job is None:
                print(f"scancel: error: Invalid job id {job_id}", file=sys.stderr)
                continue
            if job["runs"] and job["runs"][-1]["state"] == "RUNNING":
                run = job["runs"][-1]
                run.update(state="CANCELLED", end=time.time(), exit_code=-9)
                _kill(run)
            job["state"] = "CANCELLED"
    return 0


def scontrol(state_dir: Path, argv: list[str]) -> int:
    if argv[:1] == ["requeue"]:
        restart = []
        with _state(state_dir) as state:
            for job_id in argv[1].split(","):
                job = state["jobs"][job_id]
                if job["runs"] and job["runs"][-1]["state"] == "RUNNING":
                    run = job["runs"][-1]
                    run.update(state="REQUEUED", end=time.time(), exit_code=-9)
                    _kill(run)
                    job["state"] = "PENDING"
                    restart.append(job_id)
        for job_id in restart:
            _spawn_runner(state_dir, job_id)
        return 0
    if argv[:2] == ["show", "job"]:
        with _state(state_dir) as state:
            job = state["jobs"][argv[2]]
            print(f"JobId={job['id']} JobName={job['name']} JobState={_job_state(job)} "
                  f"NumCPUs={job['ncpus']} StdOut={job['output']} WorkDir={job['workdir']}")
        return 0
    print(f"scontrol: unsupported in fakeslurm: {' '.join(argv)}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- queries
def squeue(state_dir: Path, argv: list[str]) -> int:
    ids = option_value(argv, "-j", "--jobs")
    fmt = option_value(argv, "-o", "--format") or "%.10i %.9P %.20j %.8u %.2t %.10M %.6D %R"
    wanted = set(ids.split(",")) if ids else None
    with _state(state_dir) as state:
        jobs = [j for j in state["jobs"].values()
                if _job_state(j) in ("PENDING", "RUNNING") and (wanted is None or j["id"] in wanted)]
    if "-h" not in argv and "--noheader" not in argv:
        print(_squeue_line(fmt, None))
    for job in jobs:
        print(_squeue_line(fmt, job))
    return 0


def _squeue_line(fmt: str, job: dict[str, Any] | None) -> str:
    import re

    def field(match: re.Match) -> str:
        width, code = match.group(1), match.group(2)
        if job is None:
            text = {"i": "JOBID", "j": "NAME", "T": "STATE", "t": "ST", "M": "TIME", "u": "USER",
                    "P": "PARTITION", "D": "NODES", "R": "NODELIST(REASON)", "N": "NODELIST"}.get(code, code)
        else:
            state = _job_state(job)
            run = job["runs"][-1] if job["runs"] else {}
            secs = int(_elapsed(run)) if run else 0
            text = {"i": job["id"], "j": job["name"], "T": state, "t": {"RUNNING": "R", "PENDING": "PD"}.get(state, state[:2]),
                    "M": f"{secs // 60}:{secs % 60:02d}", "u": os.environ.get("USER", "user"),
                    "P": "fake", "D": "1", "R": "localhost" if state == "RUNNING" else "(None)",
                    "N": "localhost"}.get(code, "")
        if width:
            w = int(width.lstrip(".").lstrip("-") or 0)
            return text.rjust(w) if not width.startswith("-") else text.ljust(w)
        return text

    return re.sub(r"%([.\-]?\d*)([a-zA-Z])", field, fmt)


SACCT_DEFAULT = "JobID,JobName,Partition,Account,AllocCPUS,State,ExitCode"


def sacct(state_dir: Path, argv: list[str]) -> int:
    ids = option_value(argv, "-j", "--jobs")
    fields = (option_value(argv, "-o", "--format") or SACCT_DEFAULT).split(",")
    parsable2 = "-P" in argv or "--parsable2" in argv
    parsable = "-p" in argv or "--parsable" in argv
    dups = "-D" in argv or "--duplicates" in argv
    wanted = set(ids.split(",")) if ids else None
    rows = []
    with _state(state_dir) as state:
        for job in state["jobs"].values():
            if wanted is not None and job["id"] not in wanted:
                continue
            runs = job["runs"] or [{"start": None, "end": None, "state": job["state"], "exit_code": None}]
            if job["state"] == "CANCELLED" and not job["runs"]:
                runs = [{"start": None, "end": time.time(), "state": "CANCELLED", "exit_code": 0}]
            if job["runs"] and job["state"] == "PENDING":  # requeued, waiting to restart
                runs = runs + [{"start": None, "end": None, "state": "PENDING", "exit_code": None}]
            for run in (runs if dups else runs[-1:]):
                rows.append(_sacct_row(job, run, fields))
    sep = "|"
    if not ("-n" in argv or "--noheader" in argv):
        print(sep.join(fields) + (sep if parsable else "") if (parsable or parsable2)
              else "  ".join(f"{f:>12}" for f in fields))
    for row in rows:
        print(sep.join(row) + (sep if parsable else "") if (parsable or parsable2)
              else "  ".join(f"{v:>12}" for v in row))
    return 0


def _sacct_row(job: dict[str, Any], run: dict[str, Any], fields: list[str]) -> list[str]:
    elapsed = int(round(_elapsed(run)))
    rc = run.get("exit_code")
    values = {
        "jobid": job["id"], "jobidraw": job["id"], "jobname": job["name"], "partition": "fake",
        "account": "study", "alloccpus": str(job["ncpus"]), "ncpus": str(job["ncpus"]),
        "state": run["state"], "exitcode": "" if rc is None else (f"{rc}:0" if rc >= 0 else f"0:{-rc}"),
        "cputimeraw": str(elapsed * job["ncpus"]), "elapsedraw": str(elapsed),
        "elapsed": time.strftime("%H:%M:%S", time.gmtime(elapsed)),
        "cputime": time.strftime("%H:%M:%S", time.gmtime(elapsed * job["ncpus"])),
        "start": _ts(run.get("start")), "end": _ts(run.get("end")), "submit": _ts(job["submit"]),
    }
    return [values.get(f.strip().lower().split("%")[0], "") for f in fields]


def srun(state_dir: Path, argv: list[str]) -> int:
    """Inside a job, run the command in place; there is no allocation to make."""
    if not os.environ.get("SLURM_JOB_ID"):
        print("srun: error: fakeslurm supports srun only inside a batch job", file=sys.stderr)
        return 1
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        takes_value = len(argv[i]) == 2 and argv[i][1] in "nNcJotpw"
        i += 2 if takes_value else 1
    return subprocess.call(argv[i:])


COMMANDS = {"srun": srun, "sbatch": sbatch, "squeue": squeue, "sacct": sacct, "scancel": scancel,
            "scontrol": scontrol}


def main(argv: list[str]) -> int:
    state_dir, command, args = Path(argv[0]), argv[1], argv[2:]
    if command == "_run":
        return _run(state_dir, args[0])
    return COMMANDS[command](state_dir, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
