"""Install the shim for one run: ``python install.py <run_dir>``.

Creates ``<run_dir>/bin`` with one wrapper per scheduler command. The
harness puts that directory first on the agent's PATH. The run directory
is baked into each wrapper, so the shim does not depend on the agent's
environment surviving (a resumed session, a job's environment).
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

COMMANDS = ("sbatch", "squeue", "sacct", "scancel", "scontrol", "srun")
SHIM = Path(__file__).resolve().parent / "shim.py"


def install(run_dir: str | Path, python: str = sys.executable) -> Path:
    run_dir = Path(run_dir).resolve()
    bin_dir = run_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "shim" / "submissions").mkdir(parents=True, exist_ok=True)
    for command in COMMANDS:
        wrapper = bin_dir / command
        wrapper.write_text(
            "#!/bin/sh\n"
            f"CADAQUES_STUDY_RUN={shlex.quote(str(run_dir))} "
            f"exec {shlex.quote(python)} -I {shlex.quote(str(SHIM))} {command} \"$@\"\n"
        )
        wrapper.chmod(0o755)
    return bin_dir


if __name__ == "__main__":
    print(install(sys.argv[1]))
