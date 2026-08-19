import disconnect_watcher as dw

# Real-shaped sample lines (timestamp prefix trimmed since classify_line
# only searches for the bracketed marker onward, same as RobloxProcess.cs's
# regexes which are anchored to the timestamp prefix but only the
# `[FLog::...]` portion matters for classification here).
CONNECTED_LINE = (
    "2026-08-19T07:51:34.123Z,1,ClientAppSettings "
    "[FLog::Output] ! Joining game 'abcd1234-5678-90ab-cdef-1234567890ab' "
    "place 16732694052 at 1.2.3.4"
)
DISCONNECTED_LINE = (
    "2026-08-19T07:55:10.456Z,1,Network "
    "[FLog::Network] Sending disconnect with reason: 5"
)
UNRELATED_LINE = "2026-08-19T07:52:00.000Z,1,Something [FLog::Foo] irrelevant line"


def test_classify_line_connected():
    assert dw.classify_line(CONNECTED_LINE) == dw.LineEvent.CONNECTED


def test_classify_line_disconnected():
    assert dw.classify_line(DISCONNECTED_LINE) == dw.LineEvent.DISCONNECTED


def test_classify_line_none_for_unrelated_lines():
    assert dw.classify_line(UNRELATED_LINE) == dw.LineEvent.NONE


def test_watcher_state_tracks_connect_then_disconnect():
    state = dw.WatcherState()
    assert state.is_connected is False

    state.apply_line(CONNECTED_LINE, now=1000.0)
    assert state.is_connected is True
    assert state.seconds_since_disconnect() is None

    state.apply_line(DISCONNECTED_LINE, now=1010.0)
    assert state.is_connected is False
    assert state.seconds_since_disconnect(now=1015.0) == 5.0


def test_watcher_state_reconnect_clears_disconnect_timer():
    state = dw.WatcherState()
    state.apply_line(DISCONNECTED_LINE, now=1000.0)
    assert state.seconds_since_disconnect(now=1005.0) == 5.0

    state.apply_line(CONNECTED_LINE, now=1010.0)
    assert state.is_connected is True
    assert state.seconds_since_disconnect() is None


def test_watcher_state_ignores_unrelated_lines():
    state = dw.WatcherState()
    state.apply_line(CONNECTED_LINE, now=1000.0)
    state.apply_line(UNRELATED_LINE, now=1001.0)
    assert state.is_connected is True


def test_log_tailer_reads_appended_bytes_incrementally(tmp_path):
    log_path = tmp_path / "sample.log"
    log_path.write_text(CONNECTED_LINE + "\n", encoding="utf-8")

    tailer = dw.LogTailer(log_path)
    tailer.poll()
    assert tailer.state.is_connected is True

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(DISCONNECTED_LINE + "\n")

    tailer.poll()
    assert tailer.state.is_connected is False


def test_log_tailer_noop_when_no_new_bytes(tmp_path):
    log_path = tmp_path / "sample.log"
    log_path.write_text(CONNECTED_LINE + "\n", encoding="utf-8")

    tailer = dw.LogTailer(log_path)
    tailer.poll()
    position_after_first_poll = tailer.state.last_position

    tailer.poll()
    assert tailer.state.last_position == position_after_first_poll


def test_find_log_path_parses_handle_output(monkeypatch, tmp_path):
    fake_stdout = (
        "cmd.exe        pid: 1234 type: File            2F4: "
        "C:\\Users\\Admin\\AppData\\Local\\Roblox\\logs\\"
        "0.612.0.1234_20260819T075100Z_Player_ABCDEF12_last.log"
        "\n"
    )

    class FakeCompletedProcess:
        stdout = fake_stdout

    def fake_run(args, capture_output, text, timeout, **_kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr(dw.subprocess, "run", fake_run)

    result = dw.find_log_path("handle.exe", 1234)

    assert result is not None
    assert result.name == "0.612.0.1234_20260819T075100Z_Player_ABCDEF12_last.log"


def test_find_log_path_returns_none_when_no_match(monkeypatch):
    class FakeCompletedProcess:
        stdout = "no handles found"

    def fake_run(args, capture_output, text, timeout, **_kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr(dw.subprocess, "run", fake_run)

    assert dw.find_log_path("handle.exe", 1234) is None
