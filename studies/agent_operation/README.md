# Study v2 — agent operation with and without CADAQUES

Protocol: [`PROTOCOL.md`](PROTOCOL.md) (draft, not frozen).

| Directory | What | Status |
|---|---|---|
| `shim/` | Scheduler shim: logs every `sbatch`/`squeue`/`sacct`/`scancel`/`scontrol`/`srun` call, injects the scheduled faults, forwards to Slurm or `fakeslurm.py` | built, tested |
| `grader/` | Ground truth from the shim log + `sacct -D`; grades the arm's record and report (O1–O4, defect taxonomy, manipulation check) | built, tested |
| `reference/` | Quarter-wave verification (`qw_check.py`) and McNemar power (`power.py`) | done |
| harness, container, briefs | Launch agents, trigger the S3 session kill, collect usage | not built |

## One run, by hand

```bash
python - <<'PY'
import sys; sys.path.insert(0, "shim")
from schedule import make_run_config, write_run_config
cfg = make_run_config(arm="B", scenario="S1", seed=0, model="pilot", c0=10.0, t_ref_s=10.0,
                      backend={"kind": "fake", "state_dir": "/tmp/run1/fakeslurm"})
#                     backend={"kind": "slurm", "bin": "/usr/bin"}   # on a real cluster
write_run_config("/tmp/run1", cfg)
PY
python shim/install.py /tmp/run1          # -> /tmp/run1/bin, put first on the agent's PATH
# ... the agent works in /tmp/run1/work ...
python -m grader /tmp/run1                # -> /tmp/run1/grades.json
```

The harness polls `grader.truth.kill_due(run_dir, config)` in S3 to know
when to kill the agent session, and writes `harness.json` with
`session_kills: [{"t": ...}]` and `usage: {...}` (tokens, wall time).

## Tests

```bash
python -m pytest studies/agent_operation/tests -q   # ~75 s: jobs really run on fakeslurm
```

Not part of the package CI (they are slow and POSIX-only).

## Before using on a real cluster

- Check that `scontrol requeue` is allowed for user jobs (S2); the
  manipulation check fails loudly if not.
- `sacct` must return `CPUTimeRAW` and keep requeue records (`-D`).
- Make MPB available only inside jobs, so evaluations cannot bypass the
  scheduler (and with it, the accounting).
