from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .io_utils import ensure_dir


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: str
    exit_code: int | None
    timed_out: bool
    duration_sec: float
    stdout_path: str
    stderr_path: str


def run_logged_command(
    command: str,
    cwd: Path,
    log_dir: Path,
    name: str,
    timeout_sec: int,
    env: dict[str, str] | None = None,
) -> CommandResult:
    ensure_dir(log_dir)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"
    start = time.time()
    timed_out = False
    exit_code: int | None

    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_sec,
                env=env,
                text=True,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None

    return CommandResult(
        command=command,
        cwd=str(cwd),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_sec=round(time.time() - start, 3),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )

