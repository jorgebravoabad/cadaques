import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

STUDY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDY))
sys.path.insert(0, str(STUDY / "shim"))

from install import install  # noqa: E402
from schedule import make_run_config, write_run_config  # noqa: E402

C0 = 2.0
JOB = """#!/bin/bash
#SBATCH -J pc1d
#SBATCH -o out-%j.txt
echo "Working in 1 dimensions. Grid size is {res}x1x1."
sleep {secs}
echo done {tag}
"""


class Run:
    """A run directory with the shim installed over fakeslurm."""

    def __init__(self, root: Path, scenario="S0", arm="B", **overrides):
        self.dir = root / f"run-{scenario}-{arm}"
        config = make_run_config(arm=arm, scenario=scenario, seed=0, model="test", c0=C0,
                                 t_ref_s=2.0, backend={"kind": "fake",
                                                       "state_dir": str(self.dir / "fakeslurm")})
        config.update(overrides)
        write_run_config(self.dir, config)
        self.config = config
        self.bin = install(self.dir)
        self.work = self.dir / "work"
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "FAKESLURM_QUEUE_DELAY": "0"}
        self.n = 0

    def sh(self, *argv: str) -> str:
        return subprocess.run(argv, cwd=self.work, env=self.env, capture_output=True,
                              text=True, check=True).stdout

    def submit(self, secs=1, res=16, tag=None) -> str:
        self.n += 1
        script = self.work / f"job{self.n}.sh"
        script.write_text(JOB.format(res=res, secs=secs, tag=tag if tag is not None else self.n))
        return self.sh("sbatch", "--parsable", str(script)).strip()

    def wait(self, timeout=30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.sh("squeue", "-h", "-o", "%i").strip():
                time.sleep(0.3)  # let the runner write final accounting
                return
            time.sleep(0.2)
        raise TimeoutError("jobs did not finish")

    def costs(self) -> dict[str, float]:
        # -D: a requeued job has one accounting record per run; all of them cost.
        out = self.sh("sacct", "-D", "-P", "-n", "-o", "JobIDRaw,CPUTimeRAW")
        totals: dict[str, float] = {}
        for line in out.splitlines():
            job_id, cost = line.split("|")
            totals[job_id] = totals.get(job_id, 0.0) + float(cost)
        return totals

    def states(self) -> dict[str, str]:
        out = self.sh("sacct", "-P", "-n", "-o", "JobIDRaw,State")
        return dict(line.split("|") for line in out.splitlines())


@pytest.fixture
def make_run(tmp_path):
    def factory(**kw):
        return Run(tmp_path, **kw)
    return factory
