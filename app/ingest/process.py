"""Run only processes owned by this caller, with bounded tree cleanup."""

import os
from pathlib import Path
import signal
import subprocess


class ProcessCleanupError(RuntimeError):
    """Safe failure: do not claim all children stopped when cleanup is uncertain."""


def _stop_tree(process):
    if os.name == "nt":
        if process.poll() is not None:
            raise ProcessCleanupError("工作进程已退出但输出仍未关闭，无法确认所有子进程状态。")
        # Popen's live process handle prevents confusing this with a recycled PID.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            cleanup = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise ProcessCleanupError("超时后的进程树清理未确认，请检查本地任务后再重试。") from None
        if cleanup.returncode != 0 and process.poll() is None:
            raise ProcessCleanupError("无法确认超时进程树已停止，请检查本地任务后再重试。")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise ProcessCleanupError("工作进程停止未确认，请检查本地任务后再重试。") from None


def run_bounded(command, timeout, capture_output=False, check=False, **kwargs):
    """subprocess.run subset; timeout stops the live owned tree before parent.

    Windows venv launchers can have a separate worker process. Killing only
    the launcher is not a portable guarantee that the worker stopped as well.
    Non-Windows children use a fresh session, never the caller's process group.
    """
    if timeout <= 0:
        raise ValueError("Process timeout must be positive")
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)
    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            raise ProcessCleanupError("进程已停止但输出通道未关闭，请检查残留任务。") from None
        # Do not attach provider/worker output to the exception or terminal.
        raise subprocess.TimeoutExpired(command, timeout) from None
    except BaseException:
        if process.poll() is None:
            _stop_tree(process)
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result
