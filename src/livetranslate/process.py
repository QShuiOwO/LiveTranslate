"""Bounded subprocess calls. No shell interpolation and no global environment edits."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from .errors import LiveTranslateError


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float


def run_command(args: list[str], *, timeout: float, env: dict | None = None,
                cwd: Path | None = None, check: bool = True) -> ProcessResult:
    start = perf_counter()
    child_env = dict(os.environ if env is None else env)
    child_env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8")
    try:
        proc = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=child_env, cwd=cwd, check=False)
    except FileNotFoundError as exc:
        raise LiveTranslateError("executable_missing", f"Executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        detail = exc.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise LiveTranslateError("process_timeout", f"{args[0]} timed out after {timeout}s: {detail[-2000:]}") from exc
    except OSError as exc:
        raise LiveTranslateError("process_start", f"Cannot start {args[0]}: {exc}") from exc
    result = ProcessResult(proc.returncode, proc.stdout, proc.stderr, (perf_counter() - start) * 1000)
    if check and proc.returncode:
        raise LiveTranslateError("process_failed", f"{args[0]} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-4000:]}")
    return result
