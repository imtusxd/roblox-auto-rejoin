from accounts import Account
from rejoin_controller import AccountRuntime, ControllerConfig, RejoinController


def _make_controller(error_retry_seconds: float = 15.0, on_log=None) -> RejoinController:
    cfg = ControllerConfig(
        place_id="123",
        handle_exe_path="handle.exe",
        potassium_path="potassium.exe",
        error_retry_seconds=error_retry_seconds,
    )
    return RejoinController(cfg, on_log=on_log)


def _runtime(consecutive_failures: int = 0) -> AccountRuntime:
    return AccountRuntime(account=Account(index=0, cookie="c"), consecutive_failures=consecutive_failures)


# -- _backoff_seconds ---------------------------------------------------


def test_backoff_seconds_is_the_base_interval_with_no_failures_yet():
    controller = _make_controller(error_retry_seconds=15.0)
    assert controller._backoff_seconds(_runtime(0)) == 15.0


def test_backoff_seconds_doubles_per_consecutive_failure():
    controller = _make_controller(error_retry_seconds=15.0)
    assert controller._backoff_seconds(_runtime(1)) == 30.0
    assert controller._backoff_seconds(_runtime(2)) == 60.0
    assert controller._backoff_seconds(_runtime(3)) == 120.0


def test_backoff_seconds_caps_at_the_hard_maximum():
    controller = _make_controller(error_retry_seconds=15.0)
    assert controller._backoff_seconds(_runtime(20)) == RejoinController._MAX_BACKOFF_SECONDS
    # a merely large-but-not-absurd streak should already be capped too
    assert controller._backoff_seconds(_runtime(10)) <= RejoinController._MAX_BACKOFF_SECONDS


def test_backoff_seconds_exponent_itself_is_capped_not_just_the_result():
    # Without capping the exponent, 2 ** huge_number would be an extremely
    # expensive/overflowing computation long before the min() ever kicks
    # in - the exponent must be clamped directly.
    controller = _make_controller(error_retry_seconds=15.0)
    assert controller._backoff_seconds(_runtime(10_000)) == RejoinController._MAX_BACKOFF_SECONDS


# -- _wait_after_failure ---------------------------------------------------


def test_wait_after_failure_increments_the_streak():
    controller = _make_controller(error_retry_seconds=0.0)
    runtime = _runtime(0)

    controller._wait_after_failure(runtime)

    assert runtime.consecutive_failures == 1


def test_wait_after_failure_logs_the_retry_count_and_wait_time():
    logged = []
    controller = _make_controller(error_retry_seconds=0.0, on_log=logged.append)
    runtime = _runtime(0)

    controller._wait_after_failure(runtime)

    assert any("retry #1" in message for message in logged)


def test_wait_after_failure_returns_true_when_stop_event_already_set():
    controller = _make_controller(error_retry_seconds=5.0)
    controller._stop_event.set()
    runtime = _runtime(0)

    assert controller._wait_after_failure(runtime) is True
    assert runtime.consecutive_failures == 1  # still counted, even though it returns immediately
