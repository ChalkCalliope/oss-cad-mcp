"""Async subprocess execution with process-group cleanup and output caps."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProcessResult:
    argv: list[str]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    truncated: bool = False
    tail: list[str] = field(default_factory=list)


class AsyncRunner:
    """Small asyncio subprocess wrapper with bounded in-memory output."""

    def __init__(self, *, default_timeout_s: float = 300.0, max_output_bytes: int = 65_536) -> None:
        self.default_timeout_s = default_timeout_s
        self.max_output_bytes = max_output_bytes

    async def run_capture(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float | None = None,
        stdin_text: str | None = None,
    ) -> ProcessResult:
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_task = asyncio.create_task(self._collect(proc.stdout, self.max_output_bytes))
        stderr_task = asyncio.create_task(self._collect(proc.stderr, self.max_output_bytes))
        timed_out = False
        cancelled = False
        try:
            if stdin_text is not None and proc.stdin is not None:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout_s or self.default_timeout_s)
            except TimeoutError:
                timed_out = True
                await self._terminate(proc)
            except asyncio.CancelledError:
                cancelled = True
                await self._terminate(proc)
                raise
        finally:
            stdout_text, stdout_bytes, stdout_truncated = await stdout_task
            stderr_text, stderr_bytes, stderr_truncated = await stderr_task
            if proc.returncode is None:
                await proc.wait()
        duration = time.monotonic() - started
        tail = [line for line in (stdout_text + "\n" + stderr_text).splitlines() if line][-100:]
        return ProcessResult(
            argv=list(argv),
            exit_code=proc.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_s=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            truncated=stdout_truncated or stderr_truncated,
            tail=tail,
        )

    async def run_streaming(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
        timeout_s: float | None = None,
        stdin_text: str | None = None,
        on_line: Callable[[str, str], None] | None = None,
        tail_lines: int = 200,
    ) -> ProcessResult:
        started = time.monotonic()
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        tail: deque[str] = deque(maxlen=tail_lines)
        stdout_task = asyncio.create_task(self._pump(proc.stdout, stdout_path, tail, "stdout", on_line))
        stderr_task = asyncio.create_task(self._pump(proc.stderr, stderr_path, tail, "stderr", on_line))
        timed_out = False
        cancelled = False
        try:
            if stdin_text is not None and proc.stdin is not None:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout_s or self.default_timeout_s)
            except TimeoutError:
                timed_out = True
                await self._terminate(proc)
            except asyncio.CancelledError:
                cancelled = True
                await self._terminate(proc)
                raise
        finally:
            stdout_bytes, stdout_lines = await stdout_task
            stderr_bytes, stderr_lines = await stderr_task
            if proc.returncode is None:
                await proc.wait()
        duration = time.monotonic() - started
        return ProcessResult(
            argv=list(argv),
            exit_code=proc.returncode,
            duration_s=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout=f"\n{stdout_lines} lines written to {stdout_path}\n",
            stderr=f"\n{stderr_lines} lines written to {stderr_path}\n",
            tail=list(tail),
        )

    @staticmethod
    async def _collect(stream: asyncio.StreamReader | None, cap: int) -> tuple[str, int, bool]:
        if stream is None:
            return "", 0, False
        chunks: list[bytes] = []
        total = 0
        kept = 0
        truncated = False
        while True:
            chunk = await stream.read(65_536)
            if not chunk:
                break
            total += len(chunk)
            if kept < cap:
                remaining = cap - kept
                chunks.append(chunk[:remaining])
                kept += min(len(chunk), remaining)
            if kept >= cap:
                truncated = True
        return b"".join(chunks).decode("utf-8", errors="replace"), total, truncated

    @staticmethod
    async def _pump(
        stream: asyncio.StreamReader | None,
        path: Path,
        tail: deque[str],
        stream_name: str,
        on_line: Callable[[str, str], None] | None,
    ) -> tuple[int, int]:
        if stream is None:
            path.write_text("", encoding="utf-8")
            return 0, 0
        total = 0
        lines = 0
        with path.open("w", encoding="utf-8", errors="replace") as fh:
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    break
                total += len(line_bytes)
                text = line_bytes.decode("utf-8", errors="replace")
                fh.write(text)
                lines += 1
                tail.append(text.rstrip("\n"))
                if on_line is not None:
                    on_line(stream_name, text.rstrip("\n"))
        return total, lines

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        if os.name == "nt":
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                subprocess_kill = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await subprocess_kill.wait()
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
