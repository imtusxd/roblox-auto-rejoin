import json

import launcher


class FakeKernel32:
    def __init__(self, create_result: int, wait_result: int) -> None:
        self.create_result = create_result
        self.wait_result = wait_result
        self.closed_handles: list[int] = []
        self.create_calls: list[tuple] = []

    def CreateMutexW(self, security, initial_owner, name):
        self.create_calls.append((security, initial_owner, name))
        return self.create_result

    def WaitForSingleObject(self, handle, timeout_ms):
        return self.wait_result

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)
        return True


class FakeWindll:
    def __init__(self, kernel32: FakeKernel32) -> None:
        self.kernel32 = kernel32


def _reset():
    launcher._mutex_handle = None  # noqa: SLF001 - resetting module-level cache between tests


def test_enable_multi_instance_success(monkeypatch):
    _reset()
    fake = FakeKernel32(create_result=1234, wait_result=launcher._WAIT_OBJECT_0)
    monkeypatch.setattr(launcher.ctypes, "windll", FakeWindll(fake))

    assert launcher.enable_multi_instance() is True
    assert fake.create_calls[0][2] == "ROBLOX_singletonMutex"
    assert launcher._mutex_handle == 1234
    _reset()


def test_enable_multi_instance_is_idempotent(monkeypatch):
    _reset()
    fake = FakeKernel32(create_result=1234, wait_result=launcher._WAIT_OBJECT_0)
    monkeypatch.setattr(launcher.ctypes, "windll", FakeWindll(fake))

    assert launcher.enable_multi_instance() is True
    assert launcher.enable_multi_instance() is True
    assert len(fake.create_calls) == 1  # second call is a no-op, already held
    _reset()


def test_enable_multi_instance_fails_when_ownership_not_granted(monkeypatch):
    _reset()
    fake = FakeKernel32(create_result=1234, wait_result=0x00000102)  # WAIT_TIMEOUT
    monkeypatch.setattr(launcher.ctypes, "windll", FakeWindll(fake))

    assert launcher.enable_multi_instance() is False
    assert fake.closed_handles == [1234]
    assert launcher._mutex_handle is None
    _reset()


def test_enable_multi_instance_fails_when_create_returns_null(monkeypatch):
    _reset()
    fake = FakeKernel32(create_result=0, wait_result=launcher._WAIT_OBJECT_0)
    monkeypatch.setattr(launcher.ctypes, "windll", FakeWindll(fake))

    assert launcher.enable_multi_instance() is False
    _reset()


# -- _is_real_game_client / list_roblox_pids ---------------------------------


def test_is_real_game_client_accepts_ticket_and_join_args():
    assert launcher._is_real_game_client('"RobloxPlayerBeta.exe" -t abc123 -j joinScript') is True


def test_is_real_game_client_accepts_protocol_uri_command_line():
    # The shape actually observed live on this machine: RobloxPlayerBeta.exe
    # re-exec'd with the whole roblox-player: URI as a single argument.
    assert (
        launcher._is_real_game_client(
            '"RobloxPlayerBeta.exe" roblox-player:1+launchmode:play+gameinfo:abc123+...'
        )
        is True
    )


def test_is_real_game_client_rejects_empty_command_line():
    assert launcher._is_real_game_client("") is False
    assert launcher._is_real_game_client(None) is False


def test_is_real_game_client_rejects_second_process_prefix():
    assert launcher._is_real_game_client(r"\??\C:\Program Files\Roblox\RobloxPlayerBeta.exe") is False


def test_is_real_game_client_rejects_missing_launch_args():
    assert launcher._is_real_game_client('"RobloxPlayerBeta.exe" --some-other-flag') is False


def test_is_real_game_client_rejects_launch_to_tray_only():
    # The stuck/zombie shape confirmed live: no ticket info at all on the
    # command line, just a bare tray-launch flag - never actually joined.
    assert launcher._is_real_game_client('"RobloxPlayerBeta.exe" --launch-to-tray') is False


class FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_list_roblox_pids_filters_out_helper_process(monkeypatch):
    entries = [
        {"ProcessId": 1111, "CommandLine": '"RobloxPlayerBeta.exe" -t abc -j join'},
        {"ProcessId": 2222, "CommandLine": ""},  # the helper process
    ]

    def fake_run(args, capture_output, text, timeout, **_kwargs):
        return FakeCompletedProcess(json.dumps(entries))

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher.list_roblox_pids() == {1111}


def test_list_roblox_pids_handles_single_process_as_dict_not_list(monkeypatch):
    # PowerShell's ConvertTo-Json emits a bare object (not a list) when
    # there's exactly one matching process.
    entry = {"ProcessId": 5555, "CommandLine": '"RobloxPlayerBeta.exe" -t abc -j join'}

    def fake_run(args, capture_output, text, timeout, **_kwargs):
        return FakeCompletedProcess(json.dumps(entry))

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher.list_roblox_pids() == {5555}


def test_list_roblox_pids_returns_empty_set_when_no_processes(monkeypatch):
    def fake_run(args, capture_output, text, timeout, **_kwargs):
        return FakeCompletedProcess("")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher.list_roblox_pids() == set()


# -- build_launch_uri / browser tracker id -----------------------------------


def test_new_browser_tracker_id_is_reasonably_unique():
    ids = {launcher.new_browser_tracker_id() for _ in range(50)}
    assert len(ids) == 50  # collisions would mean the RNG range is too small


def test_build_launch_uri_embeds_the_given_tracker_id_not_a_shared_zero():
    uri_a = launcher.build_launch_uri("ticket", "16732694052", browser_tracker_id=111)
    uri_b = launcher.build_launch_uri("ticket", "16732694052", browser_tracker_id=222)

    assert "+browsertrackerid:111" in uri_a
    assert "+browsertrackerid:222" in uri_b
    assert uri_a != uri_b
    # The encoded placelauncherurl also carries its own copy of the id.
    assert "browsertrackerid%3d111" in uri_a.lower()


def test_build_launch_uri_url_encodes_the_place_launcher_url():
    uri = launcher.build_launch_uri("ticket", "123", browser_tracker_id=1)
    assert "%3A%2F%2F" in uri  # "://" percent-encoded, i.e. it wasn't left as a raw URL
    assert "&browserTrackerId=" not in uri  # raw "&" would mean it wasn't encoded


def test_build_launch_uri_includes_launch_exp_in_app():
    uri = launcher.build_launch_uri("ticket", "123", browser_tracker_id=1)
    assert uri.endswith("+LaunchExp:InApp")


# -- private server launch target --------------------------------------------


def test_build_launch_uri_without_private_server_code_uses_request_game():
    uri = launcher.build_launch_uri("ticket", "123", browser_tracker_id=1)
    assert "RequestGame" in uri
    assert "RequestPrivateGame" not in uri
    assert "accessCode" not in uri


def test_build_launch_uri_with_private_server_code_uses_request_private_game():
    uri = launcher.build_launch_uri(
        "ticket", "123", browser_tracker_id=1, private_server_code="abc123"
    )
    assert "RequestPrivateGame" in uri
    assert "RequestGame%26" not in uri  # not accidentally still the public request too
    assert "accessCode%3Dabc123" in uri  # embedded (and percent-encoded) in the launcher url


def test_build_launch_uri_private_server_code_is_url_encoded_into_the_launcher_url():
    uri = launcher.build_launch_uri(
        "ticket", "123", browser_tracker_id=1, private_server_code="abc123"
    )
    assert "placelauncherurl:" in uri
    # sanity: the raw (unencoded) PlaceLauncher URL never appears verbatim
    assert "PlaceLauncher.ashx?request=RequestPrivateGame&browserTrackerId=1" not in uri
