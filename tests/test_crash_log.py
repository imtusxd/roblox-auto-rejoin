import sys
import threading
import time
from pathlib import Path

import crash_log


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_sys_excepthook_writes_to_crash_log(tmp_path, monkeypatch):
    log_path = tmp_path / "log" / "crash.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(crash_log, "CRASH_LOG_PATH", log_path)

    crash_log.install()
    try:
        1 / 0
    except ZeroDivisionError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        sys.excepthook(exc_type, exc_value, exc_tb)

    content = _read_log(log_path)
    assert "main thread" in content
    assert "ZeroDivisionError" in content


def test_threading_excepthook_writes_to_crash_log(tmp_path, monkeypatch):
    log_path = tmp_path / "log" / "crash.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(crash_log, "CRASH_LOG_PATH", log_path)

    crash_log.install()

    def boom():
        raise RuntimeError("background thread boom")

    t = threading.Thread(target=boom, name="test-worker")
    t.start()
    t.join(timeout=5)
    time.sleep(0.1)  # give the excepthook a moment to run/flush

    content = _read_log(log_path)
    assert "test-worker" in content
    assert "background thread boom" in content


def test_tk_callback_exception_writes_to_crash_log(tmp_path, monkeypatch):
    log_path = tmp_path / "log" / "crash.log"
    monkeypatch.setattr(crash_log, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(crash_log, "CRASH_LOG_PATH", log_path)

    class FakeRoot:
        report_callback_exception = None

    root = FakeRoot()
    crash_log.install(root)

    try:
        raise ValueError("tk callback boom")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        root.report_callback_exception(exc_type, exc_value, exc_tb)

    content = _read_log(log_path)
    assert "tkinter callback" in content
    assert "tk callback boom" in content


def test_write_failure_is_swallowed_not_raised(tmp_path, monkeypatch):
    # LOG_DIR pointed at something that can't be mkdir'd into (a file, not
    # a directory) - _write must not propagate, since logging a crash must
    # never itself be able to crash the app.
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(crash_log, "LOG_DIR", blocked / "log")
    monkeypatch.setattr(crash_log, "CRASH_LOG_PATH", blocked / "log" / "crash.log")

    try:
        1 / 0
    except ZeroDivisionError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        crash_log._write("main thread", exc_type, exc_value, exc_tb)  # must not raise
