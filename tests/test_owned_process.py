"""Real local subprocess checks: never affect unrelated apps or contact APIs."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app.ingest.process import run_bounded, _stop_tree, ProcessCleanupError


def _alive(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only, no termination rights.
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_successful_owned_python_process_preserves_result_and_closes():
    result = run_bounded([sys.executable, "-c", "print('owned process ok')"],
        timeout=10, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0 and result.stdout.strip() == "owned process ok"


def test_timeout_stops_actual_python_worker_not_only_venv_launcher(tmp_path):
    record = tmp_path / "owned-worker-pid.json"
    source = ("import json,os,sys,time;from pathlib import Path;"
              "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid()}));"
              "time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable, "-c", source, str(record)], timeout=3, capture_output=True)
    assert time.monotonic() - started < 12
    saved = json.loads(record.read_text())
    assert not _alive(saved["pid"]), "The actual worker survived its timeout"
    if os.name == "nt":
        assert not _alive(saved["parent"]), "The Windows venv launcher survived its timeout"


@pytest.mark.skipif(os.name != "nt", reason="Windows PID tree cleanup contract")
def test_uncertain_windows_cleanup_never_claims_all_children_stopped():
    with pytest.raises(ProcessCleanupError, match="无法确认"):
        _stop_tree(SimpleNamespace(poll=lambda: 0))


def test_nonpositive_timeout_does_not_start_process():
    with pytest.raises(ValueError):
        run_bounded([sys.executable, "-c", "raise RuntimeError('must not run')"], timeout=0)
