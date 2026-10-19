"""The Study v2 scheduler shim (PROTOCOL.md §5).

Installed as ``sbatch``, ``squeue``, ``sacct``, ``scancel``, ``scontrol``
and ``srun`` on the agent's PATH (``install.py``). Every call is
forwarded to the backend (real Slurm, or ``fakeslurm.py``) and logged to
``<run>/shim/calls.jsonl``; this log, not the agent's record, is the
ground truth of what was submitted and when.

On ``sbatch`` the shim numbers the submission (1, 2, ...), looks up the
fault scheduled for that number in ``run.json``, rewrites the job script
to carry it (``inject.py``) and archives both the original and the
submitted script. A ``preempt_after`` fault does not touch the script:
a detached injector waits for the job to start running, then requeues it.

Output is passed through unchanged, so the agent sees what Slurm says.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from inject import directive_value, option_value, parse_sbatch, rewrite  # noqa: E402
from schedule import load_run_config  # noqa: E402

LOG_SCHEMA = "cadaques.study2.calls/1"


def backend_argv(config: dict[str, Any], command: str, args: list[str]) -> list[str]:
    backend = config["backend"]
    if backend["kind"] == "fake":
        return [sys.executable, "-I", str(HERE / "fakeslurm.py"), backend["state_dir"], command, *args]
    return [str(Path(backend["bin"]) / command), *args]


@contextmanager
def _locked(run_dir: Path) -> Iterator[None]:
    with (run_dir / "shim" / "lock").open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _log(run_dir: Path, entry: dict[str, Any]) -> None:
    path = run_dir / "shim" / "calls.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": LOG_SCHEMA, **entry}) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_log(run_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "shim" / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _forward(config: dict[str, Any], command: str, args: list[str],
             stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(backend_argv(config, command, args), input=stdin,
                          capture_output=True, text=True)


def _output_path(options: list[str], text: str, cwd: Path, job_id: str, name: str) -> str:
    chdir = option_value(options, "-D", "--chdir") or directive_value(text, "-D", "--chdir")
    base = (cwd / chdir) if chdir else cwd
    pattern = (option_value(options, "-o", "--output")
               or directive_value(text, "-o", "--output") or "slurm-%j.out")
    path = pattern.replace("%%", "\0").replace("%j", job_id).replace("%x", name).replace("\0", "%")
    return str((base / path).resolve())


# ---------------------------------------------------------------- sbatch
def sbatch(run_dir: Path, config: dict[str, Any], argv: list[str]) -> int:
    cwd = Path.cwd()
    sub = parse_sbatch(argv)
    stdin_text = None
    if sub.wrap is not None:
        text, basename = "#!/bin/sh\n" + sub.wrap + "\n", "wrap.sh"
    elif sub.script is not None:
        text, basename = Path(sub.script).read_text(), Path(sub.script).name
    else:
        stdin_text = sys.stdin.read()
        text, basename = stdin_text, "stdin.sh"

    with _locked(run_dir):
        index = 1 + sum(1 for e in read_log(run_dir) if e["cmd"] == "sbatch" and e.get("job_id"))
        fault = None if sub.is_array else config["faults"].get(str(index))
        rewrite_fault = fault if fault and fault["kind"] != "preempt_after" else None
        folder = run_dir / "shim" / "submissions" / f"{index:04d}"
        (folder / "original").mkdir(parents=True, exist_ok=True)
        (folder / "original" / basename).write_text(text)
        submitted = folder / basename
        submitted.write_text(rewrite(text, rewrite_fault, folder / f"sidecar-{basename}"))
        submitted.chmod(0o755)

        if sub.is_array:
            new_argv = argv  # forwarded untouched; graded as unsupported_submission
        else:
            new_argv = [*sub.options, str(submitted), *sub.script_args]
        t = time.time()
        proc = _forward(config, "sbatch", new_argv, stdin=stdin_text if sub.is_array else None)
        match = re.search(r"(\d+)", proc.stdout) if proc.returncode == 0 else None
        job_id = match.group(1) if match else None
        name = (option_value(sub.options, "-J", "--job-name")
                or directive_value(text, "-J", "--job-name") or basename)
        _log(run_dir, {
            "cmd": "sbatch", "t": t, "argv": argv, "cwd": str(cwd), "rc": proc.returncode,
            "job_id": job_id, "index": index if job_id else None, "array": sub.is_array,
            "fault": fault if job_id else None, "script_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "script_path": str(folder / "original" / basename),
            "output_path": _output_path(sub.options, text, cwd, job_id, name) if job_id else None,
            "inside_job": bool(os.environ.get("SLURM_JOB_ID")),
        })
    if job_id and fault and fault["kind"] == "preempt_after":
        subprocess.Popen([sys.executable, "-I", str(HERE / "shim.py"), "_preempt", str(run_dir),
                          job_id, str(fault["after_s"])],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def _preempt(run_dir: Path, config: dict[str, Any], job_id: str, after_s: float) -> int:
    deadline = time.time() + 86400
    while time.time() < deadline:
        state = _forward(config, "squeue", ["-h", "-j", job_id, "-o", "%T"]).stdout.strip()
        if state == "RUNNING":
            break
        if state == "":  # finished before it could be preempted
            _log(run_dir, {"cmd": "injected_requeue", "t": time.time(), "job_id": job_id,
                           "rc": None, "skipped": "job ended before preemption"})
            return 0
        time.sleep(0.5)
    time.sleep(after_s)
    proc = _forward(config, "scontrol", ["requeue", job_id])
    _log(run_dir, {"cmd": "injected_requeue", "t": time.time(), "job_id": job_id,
                   "rc": proc.returncode, "stderr": proc.stderr[-500:]})
    return 0


# ---------------------------------------------------------------- everything else
def passthrough(run_dir: Path, config: dict[str, Any], command: str, argv: list[str]) -> int:
    t = time.time()
    proc = _forward(config, command, argv)
    _log(run_dir, {"cmd": command, "t": t, "argv": argv, "cwd": os.getcwd(), "rc": proc.returncode,
                   "inside_job": bool(os.environ.get("SLURM_JOB_ID"))})
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def main(argv: list[str]) -> int:
    if argv[0] == "_preempt":
        run_dir = Path(argv[1])
        return _preempt(run_dir, load_run_config(run_dir), argv[2], float(argv[3]))
    run_dir = Path(os.environ["CADAQUES_STUDY_RUN"])
    config = load_run_config(run_dir)
    command, args = argv[0], argv[1:]
    if command == "sbatch":
        return sbatch(run_dir, config, args)
    if command == "srun" and os.environ.get("SLURM_JOB_ID") and config["backend"]["kind"] == "fake":
        return passthrough(run_dir, config, "srun", args)
    return passthrough(run_dir, config, command, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
