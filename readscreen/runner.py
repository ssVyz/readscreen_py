"""Running the pipeline binaries: own process group, logs, cancellation, process-tree kill."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

IS_WINDOWS = os.name == "nt"
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class Cancelled(Exception):
    """The running tool was stopped on request."""


class ToolError(RuntimeError):
    """A tool exited with an error."""


def child_popen_kwargs() -> dict:
    """Start a tool in its own process group, without a console window on Windows."""
    if IS_WINDOWS:
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def pid_alive(pid: int | None) -> bool:
    """Whether a process exists. (On Windows os.kill(pid, 0) would terminate it.)"""
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: exists, owned by someone else
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    """Kill a process and its children (the process group on Linux)."""
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def tail(path: Path, lines: int = 15) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 8192))
            text = fh.read().decode(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def run(
    cmd: list,
    *,
    log_path: Path,
    stdout_path: Path | None = None,
    cwd: Path | None = None,
    env: dict | None = None,
    cancel_check: Callable[[], bool] | None = None,
    on_start: Callable[[int | None], None] | None = None,
    poll: float = 0.5,
) -> None:
    """Run a tool to completion. stderr (and stdout unless captured) goes to `log_path`.

    Raises Cancelled when `cancel_check()` turns true (the process tree is killed first) and
    ToolError on a non-zero exit code.
    """
    args = [str(c) for c in cmd]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update({k: str(v) for k, v in env.items()})
    with open(log_path, "ab") as log:
        log.write(f"\n$ {subprocess.list2cmdline(args)}\n".encode())
        log.flush()
        out = open(stdout_path, "wb") if stdout_path else log
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=out, stderr=log, cwd=cwd, env=full_env,
                **child_popen_kwargs(),
            )
            if on_start:
                on_start(proc.pid)
            try:
                while True:
                    try:
                        rc = proc.wait(timeout=poll)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if cancel_check is not None and cancel_check():
                        kill_tree(proc.pid)
                        proc.wait()
                        raise Cancelled()
            except BaseException:
                if proc.poll() is None:
                    kill_tree(proc.pid)
                    proc.wait()
                raise
            finally:
                if on_start:
                    on_start(None)
        finally:
            if stdout_path:
                out.close()
    if rc != 0:
        name = Path(args[0]).stem
        raise ToolError(f"{name} failed with exit code {rc}. Last log lines:\n{tail(log_path)}")
