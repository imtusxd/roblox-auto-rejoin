from __future__ import annotations

import json

import app_config


def test_load_config_generates_and_persists_an_api_key_on_first_run(tmp_path):
    path = tmp_path / "config.json"

    config = app_config.load_config(path)

    assert config.api_key  # non-empty
    assert len(config.api_key) >= 32
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["api_key"] == config.api_key


def test_load_config_does_not_regenerate_an_existing_api_key(tmp_path):
    path = tmp_path / "config.json"
    app_config.save_config(app_config.AppConfig(api_key="already-set-key"), path)

    config = app_config.load_config(path)

    assert config.api_key == "already-set-key"


def test_load_config_fills_in_a_key_for_a_hand_edited_file_that_blanked_it(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"place_id": "123", "api_key": ""}), encoding="utf-8")

    config = app_config.load_config(path)

    assert config.api_key
    assert config.place_id == "123"


def test_load_config_generates_a_key_even_on_corrupt_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")

    config = app_config.load_config(path)

    assert config.api_key


def test_two_generated_keys_are_different(tmp_path):
    config_a = app_config.load_config(tmp_path / "a.json")
    config_b = app_config.load_config(tmp_path / "b.json")

    assert config_a.api_key != config_b.api_key


def test_save_and_load_round_trip_preserves_fields(tmp_path):
    path = tmp_path / "config.json"
    original = app_config.AppConfig(
        place_id="999", target_fps=30, api_port=9000, api_key="preset-key"
    )
    app_config.save_config(original, path)

    loaded = app_config.load_config(path)

    assert loaded.place_id == "999"
    assert loaded.target_fps == 30
    assert loaded.api_port == 9000
    assert loaded.api_key == original.api_key  # not regenerated, was already set


def test_from_dict_ignores_unknown_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"place_id": "1", "some_removed_setting": "leftover"}), encoding="utf-8"
    )

    config = app_config.load_config(path)

    assert config.place_id == "1"
    assert not hasattr(config, "some_removed_setting")


def test_default_api_host_is_lan_reachable():
    # Confirmed user choice - 0.0.0.0, not 127.0.0.1-only.
    assert app_config.AppConfig().api_host == "0.0.0.0"
