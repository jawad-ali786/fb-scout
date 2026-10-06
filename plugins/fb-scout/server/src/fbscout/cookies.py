"""Read a Facebook login (cookies) from your normal browser, to copy it into the FB Scout profile.

Sources:
- Firefox: facebook.com cookies are read from your Firefox profile, which stores
  them unencrypted. Firefox can stay open.
- Cookie file: a cookies.txt (Netscape) or JSON export from a browser extension
  ("Get cookies.txt LOCALLY", "Cookie-Editor", ...). Works for any browser.

Only facebook.com cookies are ever read or returned. Chrome/Edge profiles are not
read directly: Chrome encrypts cookies so only Chrome itself can read them. For
those, use the normal login window or a cookie-export extension.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

SESSION_COOKIES = ("c_user", "xs")  # both present = logged in

_FF_SAMESITE = {0: "None", 1: "Lax", 2: "Strict"}
_STR_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def is_facebook_domain(domain: str | None) -> bool:
    d = (domain or "").lstrip(".").lower()
    return d == "facebook.com" or d.endswith(".facebook.com")


def make_cookie(name, value, domain, path="/", expires=None, secure=True, http_only=False, same_site=None) -> dict:
    """Cookie in Playwright's add_cookies() format."""
    try:
        exp = float(expires) if expires not in (None, "", 0, "0") else -1
    except (TypeError, ValueError):
        exp = -1
    if exp > 1e11:  # milliseconds → seconds
        exp /= 1000
    cookie = {
        "name": str(name),
        "value": str(value),
        "domain": str(domain),
        "path": path or "/",
        "expires": exp if exp > 0 else -1,
        "httpOnly": bool(http_only),
        "secure": bool(secure),
    }
    if same_site:
        cookie["sameSite"] = "Lax" if same_site == "None" and not cookie["secure"] else same_site
    return cookie


def has_session(cookies: list[dict]) -> bool:
    names = {c["name"] for c in cookies if is_facebook_domain(c.get("domain"))}
    return all(n in names for n in SESSION_COOKIES)


# ---------------------------------------------------------------- Firefox

def firefox_profile_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Mozilla/Firefox/Profiles"]
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Firefox/Profiles"]
    return [home / ".mozilla/firefox", home / "snap/firefox/common/.mozilla/firefox",
            home / ".var/app/org.mozilla.firefox/.mozilla/firefox"]


def firefox_profiles() -> list[Path]:
    """Firefox profiles that have cookies, most recently used first."""
    found = [p for root in firefox_profile_roots() if root.is_dir() for p in root.iterdir()
             if (p / "cookies.sqlite").is_file()]
    return sorted(found, key=lambda p: (p / "cookies.sqlite").stat().st_mtime, reverse=True)


def read_firefox_cookies(profile: Path | None = None) -> list[dict]:
    profiles = [Path(profile)] if profile else firefox_profiles()
    if not profiles:
        raise FileNotFoundError("No Firefox profile with cookies was found on this computer.")
    db = profiles[0] / "cookies.sqlite"
    with tempfile.TemporaryDirectory(prefix="fbscout-ff-") as tmp:
        # Copy (with the write-ahead log) so a running Firefox is never touched.
        for suffix in ("", "-wal"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / ("cookies.sqlite" + suffix))
        con = sqlite3.connect(Path(tmp) / "cookies.sqlite")
        try:
            rows = con.execute(
                "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
                "FROM moz_cookies WHERE host LIKE '%facebook.com'"
            ).fetchall()
        finally:
            con.close()
    return [
        make_cookie(name, value, host, path, expiry, secure, http_only, _FF_SAMESITE.get(same_site))
        for name, value, host, path, expiry, secure, http_only, same_site in rows
        if is_facebook_domain(host)
    ]


# ---------------------------------------------------------------- cookie files

def _same_site_from_str(value) -> str | None:
    if not value:
        return None
    v = str(value).strip()
    return _STR_SAMESITE.get(v.lower()) or (v if v in ("Lax", "Strict", "None") else None)


def parse_cookie_file(path: Path | str) -> list[dict]:
    """facebook.com cookies from a Netscape cookies.txt or a JSON cookie export."""
    text = Path(path).read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    cookies: list[dict] = []
    if stripped.startswith(("[", "{")):
        data = json.loads(stripped)
        if isinstance(data, dict):
            data = data.get("cookies", [])
        for c in data:
            domain = c.get("domain") or c.get("host") or ""
            if not is_facebook_domain(domain) or "name" not in c:
                continue
            cookies.append(make_cookie(
                c["name"], c.get("value", ""), domain, c.get("path", "/"),
                c.get("expirationDate", c.get("expires", c.get("expiry"))),
                c.get("secure", True), c.get("httpOnly", False), _same_site_from_str(c.get("sameSite")),
            ))
        return cookies

    for line in text.splitlines():
        http_only = False
        if line.startswith("#HttpOnly_"):
            line, http_only = line[len("#HttpOnly_"):], True
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 7:
            continue
        domain, _subdomains, cpath, secure, expiry, name, value = parts[:7]
        if is_facebook_domain(domain):
            cookies.append(make_cookie(name, value, domain, cpath, expiry, secure.upper() == "TRUE", http_only))
    return cookies
