from pathlib import Path

import accounts


# -- load_cookies / load_accounts --------------------------------------------


def test_load_cookies_skips_blank_lines_and_comments(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "cookie-one\n\n# a comment\n  \ncookie-two\n#cookie-three-disabled\n",
        encoding="utf-8",
    )

    assert accounts.load_cookies(cookies_file) == ["cookie-one", "cookie-two"]


def test_load_cookies_strips_utf8_bom(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_bytes("﻿cookie-one\ncookie-two\n".encode("utf-8"))

    assert accounts.load_cookies(cookies_file) == ["cookie-one", "cookie-two"]


def test_load_cookies_returns_empty_list_when_file_missing(tmp_path: Path):
    assert accounts.load_cookies(tmp_path / "missing.txt") == []


def test_load_accounts_builds_indexed_accounts(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\ncookie-b\n", encoding="utf-8")

    loaded = accounts.load_accounts(cookies_file)

    assert [a.index for a in loaded] == [0, 1]
    assert [a.cookie for a in loaded] == ["cookie-a", "cookie-b"]
    assert loaded[0].label == "Account #1"  # no username resolved yet


def test_account_label_prefers_username_once_resolved():
    account = accounts.Account(index=4, cookie="c", username="SolaraStore01")
    assert account.label == "SolaraStore01"


# -- parse_cookie_line / per-account place id + private server ---------


def test_parse_cookie_line_plain_cookie_has_no_overrides():
    assert accounts.parse_cookie_line("cookie-a") == ("cookie-a", None, None)


def test_parse_cookie_line_splits_off_trailing_numeric_place_id():
    assert accounts.parse_cookie_line("cookie-a 16732694052") == ("cookie-a", "16732694052", None)


def test_parse_cookie_line_ignores_non_numeric_unrecognized_trailing_token():
    # A cookie can't actually contain whitespace, so parts[0] is always
    # the cookie; an unrecognized trailing token (not digits, not sv=...)
    # is simply not treated as any override rather than corrupting the
    # cookie itself.
    assert accounts.parse_cookie_line("cookie-a not-a-place-id") == ("cookie-a", None, None)


def test_parse_cookie_line_skips_blank_and_comment_lines():
    assert accounts.parse_cookie_line("") is None
    assert accounts.parse_cookie_line("   ") is None
    assert accounts.parse_cookie_line("# disabled cookie-a 123") is None


def test_parse_cookie_line_strips_surrounding_whitespace():
    assert accounts.parse_cookie_line("  cookie-a 123  ") == ("cookie-a", "123", None)


def test_parse_cookie_line_parses_sv_token_as_bare_access_code():
    assert accounts.parse_cookie_line("cookie-a sv=abc123") == ("cookie-a", None, "abc123")


def test_parse_cookie_line_parses_sv_token_case_insensitively():
    assert accounts.parse_cookie_line("cookie-a SV=abc123") == ("cookie-a", None, "abc123")


def test_parse_cookie_line_extracts_code_from_full_private_server_url():
    url = "https://www.roblox.com/games/16732694052/Game?privateServerLinkCode=999888777"
    assert accounts.parse_cookie_line(f"cookie-a sv={url}") == ("cookie-a", None, "999888777")


def test_parse_cookie_line_place_id_and_private_server_together_any_order():
    assert accounts.parse_cookie_line("cookie-a 16732694052 sv=abc123") == (
        "cookie-a",
        "16732694052",
        "abc123",
    )
    assert accounts.parse_cookie_line("cookie-a sv=abc123 16732694052") == (
        "cookie-a",
        "16732694052",
        "abc123",
    )


def test_extract_private_server_code_passes_through_a_bare_code():
    assert accounts.extract_private_server_code("abc123") == "abc123"


def test_extract_private_server_code_pulls_code_out_of_a_full_url():
    url = "https://www.roblox.com/games/123/Name?privateServerLinkCode=555444333"
    assert accounts.extract_private_server_code(url) == "555444333"


def test_extract_private_server_code_leaves_a_share_link_unchanged():
    # Deliberate - a share link must stay recognizable to
    # is_unsupported_share_link, not get silently "extracted" into
    # something that looks like a normal, usable code.
    url = "https://www.roblox.com/share?code=8177d15ce9d50c4d9ad6ae513e6d33fd&type=Server"
    assert accounts.extract_private_server_code(url) == url


# -- is_unsupported_share_link -------------------------------------------


def test_is_unsupported_share_link_true_for_the_newer_share_format():
    url = "https://www.roblox.com/share?code=8177d15ce9d50c4d9ad6ae513e6d33fd&type=Server"
    assert accounts.is_unsupported_share_link(url) is True


def test_is_unsupported_share_link_false_for_a_bare_code():
    assert accounts.is_unsupported_share_link("abc123") is False


def test_is_unsupported_share_link_false_for_the_older_link_format():
    url = "https://www.roblox.com/games/123/Name?privateServerLinkCode=555444333"
    assert accounts.is_unsupported_share_link(url) is False


def test_load_cookies_returns_bare_cookie_even_when_place_id_present(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a 16732694052\ncookie-b\n", encoding="utf-8")

    assert accounts.load_cookies(cookies_file) == ["cookie-a", "cookie-b"]


def test_load_accounts_parses_per_line_place_id_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "cookie-a 16732694052\ncookie-b\ncookie-c 4924922222\n", encoding="utf-8"
    )

    loaded = accounts.load_accounts(cookies_file)

    assert [a.place_id for a in loaded] == ["16732694052", None, "4924922222"]
    assert [a.cookie for a in loaded] == ["cookie-a", "cookie-b", "cookie-c"]
    # index still counts real accounts only, unaffected by the place id suffix
    assert [a.index for a in loaded] == [0, 1, 2]


def test_load_accounts_parses_per_line_private_server_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a sv=abc123\ncookie-b 16732694052 sv=xyz789\n", encoding="utf-8")

    loaded = accounts.load_accounts(cookies_file)

    assert [a.private_server for a in loaded] == ["abc123", "xyz789"]
    assert [a.place_id for a in loaded] == [None, "16732694052"]


# -- mark_cookie_dead ---------------------------------------------------


def test_mark_cookie_dead_removes_only_the_matching_line(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\n# a comment\ncookie-b\ncookie-c\n", encoding="utf-8")

    moved = accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-b", reason="401")

    assert moved is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == [
        "cookie-a",
        "# a comment",
        "cookie-c",
    ]


def test_mark_cookie_dead_appends_timestamped_reason(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a", reason="Cookie expired")

    line = dead_file.read_text(encoding="utf-8").strip()
    assert line.endswith("cookie-a - Cookie expired")
    assert line.startswith("[")  # "[<ISO timestamp>] cookie-a - Cookie expired"
    assert "T" in line and "Z]" in line


def test_mark_cookie_dead_works_without_a_reason(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a")

    line = dead_file.read_text(encoding="utf-8").strip()
    assert line.endswith("cookie-a")  # no trailing " - " with nothing after it


def test_mark_cookie_dead_returns_false_when_cookie_not_present(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\ncookie-b\n", encoding="utf-8")

    moved = accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-does-not-exist")

    assert moved is False
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a", "cookie-b"]
    assert not dead_file.exists()


def test_mark_cookie_dead_returns_false_when_cookies_file_missing(tmp_path: Path):
    moved = accounts.mark_cookie_dead(tmp_path / "missing.txt", tmp_path / "dead.txt", "cookie-a")
    assert moved is False


def test_mark_cookie_dead_creates_dead_file_parent_directory(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "nested" / "dir" / "dead.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    moved = accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a")

    assert moved is True
    assert dead_file.exists()


def test_mark_cookie_dead_appends_across_multiple_calls_instead_of_overwriting(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\ncookie-b\n", encoding="utf-8")

    accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a", reason="first")
    accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-b", reason="second")

    lines = dead_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("cookie-a - first")
    assert lines[1].endswith("cookie-b - second")


def test_mark_cookie_dead_matches_cookie_even_with_a_place_id_suffix(tmp_path: Path):
    # A line's place id override must not stop mark_cookie_dead from
    # recognizing/removing it - it matches parse_cookie_line's cookie part,
    # not the raw line.
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a 16732694052\ncookie-b\n", encoding="utf-8")

    moved = accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a")

    assert moved is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-b"]
    assert dead_file.read_text(encoding="utf-8").strip().endswith("cookie-a")


def test_mark_cookie_dead_leaves_cookies_file_empty_when_last_cookie_removed(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    dead_file = tmp_path / "dead.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    accounts.mark_cookie_dead(cookies_file, dead_file, "cookie-a")

    assert cookies_file.read_text(encoding="utf-8") == ""


# -- remove_account ---------------------------------------------------


def test_remove_account_removes_only_the_matching_line(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n# a comment\ncookie-b\ncookie-c\n", encoding="utf-8")

    removed = accounts.remove_account(cookies_file, "cookie-b")

    assert removed is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == [
        "cookie-a",
        "# a comment",
        "cookie-c",
    ]


def test_remove_account_writes_no_dead_cookies_audit_trail():
    # remove_account is a deliberate user action, not a liveness verdict -
    # unlike mark_cookie_dead it takes no dead_cookies_path at all.
    import inspect

    assert "dead_cookies_path" not in inspect.signature(accounts.remove_account).parameters


def test_remove_account_returns_false_when_cookie_not_present(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\ncookie-b\n", encoding="utf-8")

    removed = accounts.remove_account(cookies_file, "cookie-does-not-exist")

    assert removed is False
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a", "cookie-b"]


def test_remove_account_returns_false_when_cookies_file_missing(tmp_path: Path):
    removed = accounts.remove_account(tmp_path / "missing.txt", "cookie-a")
    assert removed is False


def test_remove_account_matches_cookie_even_with_a_place_id_suffix(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a 16732694052\ncookie-b\n", encoding="utf-8")

    removed = accounts.remove_account(cookies_file, "cookie-a")

    assert removed is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-b"]


def test_remove_account_leaves_cookies_file_empty_when_last_cookie_removed(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    accounts.remove_account(cookies_file, "cookie-a")

    assert cookies_file.read_text(encoding="utf-8") == ""


# -- set_place_id ---------------------------------------------------


def test_set_place_id_adds_an_override_to_a_plain_line(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\ncookie-b\n", encoding="utf-8")

    updated = accounts.set_place_id(cookies_file, "cookie-a", "16732694052")

    assert updated is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == [
        "cookie-a 16732694052",
        "cookie-b",
    ]


def test_set_place_id_replaces_an_existing_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a 16732694052\n", encoding="utf-8")

    accounts.set_place_id(cookies_file, "cookie-a", "4924922222")

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a 4924922222"]


def test_set_place_id_none_clears_an_existing_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a 16732694052\ncookie-b\n", encoding="utf-8")

    accounts.set_place_id(cookies_file, "cookie-a", None)

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a", "cookie-b"]


def test_set_place_id_preserves_line_position_and_other_lines(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n# a comment\ncookie-b\ncookie-c\n", encoding="utf-8")

    accounts.set_place_id(cookies_file, "cookie-b", "123")

    assert cookies_file.read_text(encoding="utf-8").splitlines() == [
        "cookie-a",
        "# a comment",
        "cookie-b 123",
        "cookie-c",
    ]


def test_set_place_id_returns_false_when_cookie_not_present(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    updated = accounts.set_place_id(cookies_file, "cookie-does-not-exist", "123")

    assert updated is False
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a"]


def test_set_place_id_returns_false_when_cookies_file_missing(tmp_path: Path):
    updated = accounts.set_place_id(tmp_path / "missing.txt", "cookie-a", "123")
    assert updated is False


def test_set_place_id_preserves_an_existing_private_server_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a sv=abc123\n", encoding="utf-8")

    accounts.set_place_id(cookies_file, "cookie-a", "16732694052")

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a 16732694052 sv=abc123"]


# -- set_private_server ---------------------------------------------------


def test_set_private_server_adds_a_bare_code_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    updated = accounts.set_private_server(cookies_file, "cookie-a", "abc123")

    assert updated is True
    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a sv=abc123"]


def test_set_private_server_extracts_code_from_a_full_url(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")
    url = "https://www.roblox.com/games/123/Name?privateServerLinkCode=555444333"

    accounts.set_private_server(cookies_file, "cookie-a", url)

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a sv=555444333"]


def test_set_private_server_none_clears_an_existing_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a sv=abc123\n", encoding="utf-8")

    accounts.set_private_server(cookies_file, "cookie-a", None)

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a"]


def test_set_private_server_preserves_an_existing_place_id_override(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a 16732694052\n", encoding="utf-8")

    accounts.set_private_server(cookies_file, "cookie-a", "abc123")

    assert cookies_file.read_text(encoding="utf-8").splitlines() == ["cookie-a 16732694052 sv=abc123"]


def test_set_private_server_returns_false_when_cookie_not_present(tmp_path: Path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("cookie-a\n", encoding="utf-8")

    updated = accounts.set_private_server(cookies_file, "cookie-does-not-exist", "abc123")

    assert updated is False
