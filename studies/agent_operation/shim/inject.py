"""Read an ``sbatch`` command line and rewrite its script to carry a fault.

The rewritten script keeps the original shebang and every ``#SBATCH``
directive (Slurm only reads directives before the first command), then
inserts the fault preamble, then runs the original body. Scripts in other
languages are kept verbatim in a sidecar file and run by a bash wrapper
that carries the same directives.

Faults:
    fail_now     exit 1 before the payload runs
    kill_after   SIGKILL the job's process group after ``after_s`` seconds
    slow         after the payload ends, sleep ``factor`` × its runtime
                 (CPUTimeRAW counts allocated time, so this is a slow node
                 as far as the accounting is concerned)
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# sbatch options that take a value when written as a separate argument.
_SHORT_WITH_VALUE = set("AabcDdeFGiJLMmNnopqStwxC")
_LONG_NO_VALUE = {
    "--contiguous", "--exclusive", "--get-user-env", "--hold", "--ignore-pbs", "--no-kill",
    "--no-requeue", "--overcommit", "--oversubscribe", "--parsable", "--quiet", "--requeue",
    "--spread-job", "--test-only", "--use-min-nodes", "--verbose", "--wait", "--help",
    "--usage", "--version", "--reboot",
}
_SHELLS = re.compile(r"^#!\s*(/usr)?/bin/(env\s+)?(ba|z|k|da)?sh\b")


@dataclass
class Submission:
    """An sbatch command line, split into options, script and script args."""

    options: list[str]
    script: str | None
    script_args: list[str]
    wrap: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)

    @property
    def is_array(self) -> bool:
        return any(o == "-a" or o.startswith(("--array", "-a")) for o in self.options)


def parse_sbatch(argv: list[str]) -> Submission:
    options: list[str] = []
    wrap: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("--wrap"):
            if "=" in arg:
                wrap = arg.split("=", 1)[1]
            else:
                wrap, i = argv[i + 1], i + 1
            i += 1
            continue
        if arg.startswith("--"):
            options.append(arg)
            if "=" not in arg and arg not in _LONG_NO_VALUE and i + 1 < len(argv):
                options.append(argv[i + 1])
                i += 1
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            options.append(arg)
            if len(arg) == 2 and arg[1] in _SHORT_WITH_VALUE and i + 1 < len(argv):
                options.append(argv[i + 1])
                i += 1
            i += 1
            continue
        break
    rest = argv[i:]
    script = None if wrap is not None or not rest else rest[0]
    script_args = rest[1:] if script is not None else []
    return Submission(options=options, script=script, script_args=script_args, wrap=wrap)


def option_value(options: list[str], short: str | None, long: str) -> str | None:
    """Last value of an option in a parsed option list."""
    value = None
    for i, opt in enumerate(options):
        if opt.startswith(long + "="):
            value = opt.split("=", 1)[1]
        elif opt == long or (short is not None and opt == short):
            if i + 1 < len(options):
                value = options[i + 1]
        elif short is not None and opt.startswith(short) and len(opt) > 2:
            value = opt[2:]
    return value


def directive_value(script_text: str, short: str | None, long: str) -> str | None:
    """Value of an option given as an #SBATCH directive in the script."""
    opts: list[str] = []
    for line in _header(script_text.splitlines())[1]:
        opts += shlex.split(line[len("#SBATCH"):], comments=False)
    return option_value(opts, short, long)


def _header(lines: list[str]) -> tuple[str | None, list[str], list[str]]:
    """(shebang, directive/comment/blank header lines, body lines)."""
    shebang = lines[0] if lines and lines[0].startswith("#!") else None
    rest = lines[1:] if shebang is not None else lines
    header: list[str] = []
    for j, line in enumerate(rest):
        stripped = line.strip()
        if stripped.startswith("#SBATCH") or stripped == "" or (
                stripped.startswith("#") and not stripped.startswith("#SBATCH")):
            header.append(line)
            continue
        return shebang, [h for h in header if h.strip().startswith("#SBATCH")], rest[j:]
    return shebang, [h for h in header if h.strip().startswith("#SBATCH")], []


def _preamble(fault: dict[str, Any]) -> list[str]:
    kind = fault["kind"]
    if kind == "fail_now":
        return ["# --- injected (study2) ---", "exit 1"]
    if kind == "kill_after":
        return ["# --- injected (study2) ---",
                f"( sleep {float(fault['after_s'])}; kill -KILL 0 ) >/dev/null 2>&1 &"]
    if kind == "slow":
        factor = float(fault["factor"])
        return ["# --- injected (study2) ---",
                "__s2_t0=$(date +%s)",
                "__s2_slow() { __s2_rc=$?; __s2_d=$(( $(date +%s) - __s2_t0 )); "
                f"sleep $(awk \"BEGIN{{print {factor}*$__s2_d + 1}}\"); exit $__s2_rc; }}",
                "trap __s2_slow EXIT"]
    raise ValueError(f"Unknown fault kind {kind!r}")


def rewrite(script_text: str, fault: dict[str, Any] | None, sidecar: Path) -> str:
    """Return the script to submit; may write ``sidecar`` for non-shell scripts."""
    if fault is None:
        return script_text
    lines = script_text.splitlines()
    shebang, directives, body = _header(lines)
    preamble = _preamble(fault)
    if shebang is None or _SHELLS.match(shebang):
        return "\n".join([shebang or "#!/bin/bash", *directives, *preamble, *body]) + "\n"
    # A non-shell script: run it verbatim from a sidecar, under a bash wrapper.
    sidecar.write_text(script_text)
    sidecar.chmod(0o755)
    interpreter = shebang[2:].strip()
    run = f'{interpreter} {shlex.quote(str(sidecar))} "$@"'
    return "\n".join(["#!/bin/bash", *directives, *preamble, run, "exit $?"]) + "\n"
