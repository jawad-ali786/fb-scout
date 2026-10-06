import asyncio
import json
import sqlite3

import pytest

from fbscout.cookies import has_session, make_cookie, parse_cookie_file, read_firefox_cookies
from fbscout.login import find_browser_executable, import_login

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t100012345\n"
    "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\t12%3Aabc\n"
    ".google.com\tTRUE\t/\tTRUE\t1893456000\tSID\tsecret-google\n"
)


def test_parse_netscape_keeps_only_facebook(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(NETSCAPE, encoding="utf-8")
    cookies = parse_cookie_file(f)
    assert [c["name"] for c in cookies] == ["c_user", "xs"]
    xs = cookies[1]
    assert xs["httpOnly"] is True and xs["secure"] is True and xs["expires"] == 1893456000
    assert has_session(cookies)


def test_parse_json_export(tmp_path):
    f = tmp_path / "cookies.json"
    f.write_text(json.dumps([
        {"domain": ".facebook.com", "name": "c_user", "value": "1", "path": "/", "secure": True,
         "httpOnly": False, "sameSite": "no_restriction", "expirationDate": 1893456000.5},
        {"domain": ".facebook.com", "name": "xs", "value": "2", "secure": True, "httpOnly": True, "sameSite": "lax"},
        {"domain": "example.com", "name": "other", "value": "x"},
    ]), encoding="utf-8")
    cookies = parse_cookie_file(f)
    assert {c["name"] for c in cookies} == {"c_user", "xs"}
    assert cookies[0]["sameSite"] == "None" and cookies[1]["sameSite"] == "Lax"
    assert cookies[1]["expires"] == -1     # session cookie


def test_make_cookie_ms_expiry_and_samesite_none_needs_secure():
    c = make_cookie("a", "b", ".facebook.com", expires=1893456000000, secure=False, same_site="None")
    assert c["expires"] == 1893456000 and c["sameSite"] == "Lax"


def test_has_session_requires_both():
    assert not has_session([make_cookie("c_user", "1", ".facebook.com")])


def test_read_firefox_cookies(tmp_path):
    profile = tmp_path / "abc.default-release"
    profile.mkdir()
    con = sqlite3.connect(profile / "cookies.sqlite")
    con.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, name TEXT, value TEXT, host TEXT, path TEXT, "
                "expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER)")
    con.executemany("INSERT INTO moz_cookies (name, value, host, path, expiry, isSecure, isHttpOnly, sameSite) "
                    "VALUES (?,?,?,?,?,?,?,?)", [
                        ("c_user", "100", ".facebook.com", "/", 1893456000, 1, 0, 0),
                        ("xs", "y", ".facebook.com", "/", 1893456000000, 1, 1, 0),
                        ("SID", "g", ".google.com", "/", 1893456000, 1, 1, 0),
                        ("fake", "z", ".notfacebook.com", "/", 1893456000, 1, 1, 0),
                    ])
    con.commit()
    con.close()
    cookies = read_firefox_cookies(profile)
    assert sorted(c["name"] for c in cookies) == ["c_user", "xs"]
    assert all(c["expires"] == 1893456000 for c in cookies)


def test_missing_cookie_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_cookie_file(tmp_path / "nope.txt")


def test_find_browser_executable_unknown_channel():
    assert find_browser_executable("chromium") is None


def test_import_login_into_profile(tmp_path, monkeypatch):
    """Imported cookies land in the FB Scout profile and survive a browser restart (no network)."""
    monkeypatch.setenv("FBSCOUT_HOME", str(tmp_path / "home"))
    cookies = parse_cookie_file_text(tmp_path, NETSCAPE)

    from fbscout.browser import is_logged_in, open_browser
    from fbscout.errors import BrowserUnavailable

    async def main():
        try:
            result = await import_login(cookies, "the cookie file", verify=False)
        except BrowserUnavailable:
            pytest.skip("no browser available")
        async with open_browser(headless=True) as session:
            return result, await is_logged_in(session.context)

    result, still_logged_in = asyncio.run(main())
    assert result["ok"] and result["imported_cookies"] == 2
    assert still_logged_in


def test_import_login_rejects_logged_out_source(tmp_path):
    result = asyncio.run(import_login([make_cookie("datr", "x", ".facebook.com")], "Firefox", verify=False))
    assert result["error"] == "no_facebook_login"


def parse_cookie_file_text(tmp_path, text):
    f = tmp_path / "c.txt"
    f.write_text(text, encoding="utf-8")
    return parse_cookie_file(f)
