from pathlib import Path

import paths


def test_app_dir_uses_script_location_when_not_frozen(monkeypatch):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    result = paths.app_dir()
    assert result == Path(paths.__file__).resolve().parent


def test_app_dir_uses_exe_location_when_frozen(monkeypatch):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", r"C:\Tools\roblox-auto-rejoin.exe", raising=False)
    result = paths.app_dir()
    assert result == Path(r"C:\Tools")


def test_bundled_resource_dir_uses_meipass_when_frozen(monkeypatch):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", r"C:\Tools\roblox-auto-rejoin.exe", raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", r"C:\Temp\_MEI12345", raising=False)
    result = paths.bundled_resource_dir()
    assert result == Path(r"C:\Temp\_MEI12345")


def test_bundled_resource_dir_falls_back_to_exe_dir_when_meipass_missing(monkeypatch):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", r"C:\Tools\roblox-auto-rejoin.exe", raising=False)
    monkeypatch.delattr(paths.sys, "_MEIPASS", raising=False)
    result = paths.bundled_resource_dir()
    assert result == Path(r"C:\Tools")


def test_bundled_resource_dir_uses_script_location_when_not_frozen(monkeypatch):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    result = paths.bundled_resource_dir()
    assert result == Path(paths.__file__).resolve().parent
