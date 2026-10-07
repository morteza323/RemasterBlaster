"""
Safe subprocess management (spec §33-34): real-time stdout/stderr
streaming, cooperative cancellation, timeout, and never leaving an
orphan process behind.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class ProcessResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    start_time: float
    end_time: float
    duration_seconds: float
    command: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    timed_out: bool = False
    cancelled: bool = False


def _terminate_gracefully(process: subprocess.Popen, grace_seconds: float) -> None:
    """Terminate, wait, then kill (spec §33 steps 1-4)."""
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass  # OS-level zombie reap is out of our hands at this point


def run_process(
    command: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout_seconds: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
    on_stdout_line: Optional[Callable[[str], None]] = None,
    on_stderr_line: Optional[Callable[[str], None]] = None,
    poll_interval: float = 0.1,
    termination_grace_seconds: float = 5.0,
) -> ProcessResult:
    start = time.time()
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        # Explicit UTF-8 + replace: on Windows the default text-mode
        # encoding is the console's locale codepage (often cp1252),
        # which raises UnicodeDecodeError the moment an AI tool prints
        # anything outside that codepage. Decode as UTF-8 and replace
        # anything invalid rather than crashing the reader thread.
        encoding="utf-8",
        errors="replace",
    )

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    def _reader(pipe, sink: List[str], callback: Optional[Callable[[str], None]]) -> None:
        try:
            for raw_line in iter(pipe.readline, ""):
                line = raw_line.rstrip("\n")
                sink.append(line)
                if callback is not None:
                    callback(line)
        finally:
            pipe.close()

    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, stdout_lines, on_stdout_line), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, stderr_lines, on_stderr_line), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    cancelled = False

    while True:
        try:
            process.wait(timeout=poll_interval)
            break  # process exited on its own
        except subprocess.TimeoutExpired:
            pass

        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            _terminate_gracefully(process, termination_grace_seconds)
            break

        if timeout_seconds is not None and (time.time() - start) > timeout_seconds:
            timed_out = True
            _terminate_gracefully(process, termination_grace_seconds)
            break

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    end = time.time()

    return ProcessResult(
        exit_code=process.poll(),
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
        start_time=start,
        end_time=end,
        duration_seconds=end - start,
        command=command,
        cwd=cwd,
        timed_out=timed_out,
        cancelled=cancelled,
    )
