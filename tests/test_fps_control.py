import json

import fps_control


def test_apply_target_fps_writes_the_flag(tmp_path):
    path = tmp_path / "ClientAppSettings.json"

    ok = fps_control.apply_target_fps(30, path=path)

    assert ok is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {fps_control.FPS_FLAG_NAME: 30}


def test_apply_target_fps_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "ClientAppSettings.json"

    ok = fps_control.apply_target_fps(60, path=path)

    assert ok is True
    assert path.exists()


def test_apply_target_fps_merges_with_existing_flags(tmp_path):
    path = tmp_path / "ClientAppSettings.json"
    path.write_text(json.dumps({"SomeOtherFlag": True}), encoding="utf-8")

    fps_control.apply_target_fps(144, path=path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"SomeOtherFlag": True, fps_control.FPS_FLAG_NAME: 144}


def test_apply_target_fps_zero_removes_the_flag(tmp_path):
    path = tmp_path / "ClientAppSettings.json"
    path.write_text(json.dumps({fps_control.FPS_FLAG_NAME: 30, "Other": 1}), encoding="utf-8")

    fps_control.apply_target_fps(0, path=path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"Other": 1}


def test_apply_target_fps_recovers_from_corrupt_existing_file(tmp_path):
    path = tmp_path / "ClientAppSettings.json"
    path.write_text("{not valid json", encoding="utf-8")

    ok = fps_control.apply_target_fps(30, path=path)

    assert ok is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {fps_control.FPS_FLAG_NAME: 30}


def test_client_settings_path_uses_localappdata(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Someone\AppData\Local")
    path = fps_control.client_settings_path()
    assert str(path) == r"C:\Users\Someone\AppData\Local\Roblox\ClientSettings\ClientAppSettings.json"
