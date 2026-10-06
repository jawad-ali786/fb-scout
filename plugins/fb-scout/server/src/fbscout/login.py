"""Getting a Facebook login into the FB Scout profile.

Methods:
- "browser" (default): opens your real Chrome (or Edge) as a normal window, not
  automated and without the "controlled by automated test software" bar, on the
  FB Scout profile. You log in, then close the window, and the login is checked.
- "firefox": copies your existing Facebook login from Firefox.
- "cookie_file": imports a cookies.txt / JSON export from any browser.

Searches then run in Chrome's headless mode on the same profile. They must use
the same browser: Chrome encrypts saved logins so only Chrome can read them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from .browser import FB_HOME, FB_LOGIN, check_page, interactive_login, is_logged_in, open_browser
from .config import browser_channels, profile_dir
from .cookies import has_session, is_facebook_domain, parse_cookie_file, read_firefox_cookies
from .errors import FBScoutError, ProfileInUse

log = logging.getLogger("fbscout")

_FB_COOKIE_DOMAIN = re.compile(r"(^|\.)facebook\.com$")

METHODS = ("browser", "firefox", "cookie_file")


def find_browser_executable(channel: str) -> str | None:
    """Path of the installed Chrome / Edge binary (None for 'chromium' or if not installed)."""
    if channel not in ("chrome", "msedge"):
        return None
    if sys.platform == "win32":
        rel = {"chrome": r"Google\Chrome\Application\chrome.exe", "msedge": r"Microsoft\Edge\Application\msedge.exe"}[channel]
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base and (Path(base) / rel).is_file():
                return str(Path(base) / rel)
        return None
    if sys.platform == "darwin":
        app = {"chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "msedge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"}[channel]
        return app if Path(app).is_file() else None
    names = {"chrome": ("google-chrome", "google-chrome-stable"), "msedge": ("microsoft-edge", "microsoft-edge-stable")}[channel]
    return next((p for n in names if (p := shutil.which(n))), None)


async def _logged_in_state(attempts: int = 8) -> tuple[bool, str]:
    """(logged_in, channel). Retries while a just-closed browser still holds the profile."""
    for i in range(attempts):
        try:
            async with open_browser(headless=True) as session:
                return await is_logged_in(session.context), session.channel
        except ProfileInUse:
            if i == attempts - 1:
                raise
            await asyncio.sleep(1.5)
    raise ProfileInUse()


async def real_browser_login(timeout_seconds: int = 600, force: bool = False) -> dict:
    logged_in, channel = await _logged_in_state(attempts=1)
    if logged_in and not force:
        return {"ok": True, "logged_in": True, "already_logged_in": True, "browser": channel}

    exe = next((e for ch in browser_channels() if ch == channel and (e := find_browser_executable(ch))), None)
    if exe is None:  # only bundled Chromium available: fall back to the automated window
        log.info("no installed Chrome/Edge found; using the automated login window")
        return await interactive_login(timeout_seconds)

    args = [exe, f"--user-data-dir={profile_dir(channel)}", "--no-first-run", "--no-default-browser-check",
            "--hide-crash-restore-bubble", "--new-window", FB_LOGIN]
    log.info("login: opening %s as a normal window", channel)
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=max(60, timeout_seconds))
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "browser": channel,
                "message": f"The login window is still open after {timeout_seconds}s.",
                "hint": "Finish logging in, close that window, then run fb_status (CLI: fbscout status)."}

    if time.monotonic() - started < 5:
        # Chrome handed the URL to an already-open window of this profile and exited.
        return {"ok": False, "error": "profile_in_use", "browser": channel,
                "message": "An FB Scout browser window is already open (the login page opened there).",
                "hint": "Log in in that window, close it, then run fb_status."}

    logged_in, channel = await _logged_in_state()
    if logged_in:
        return {"ok": True, "logged_in": True, "already_logged_in": False, "browser": channel}
    return {"ok": False, "error": "not_logged_in", "browser": channel,
            "message": "The window was closed but Facebook is not logged in.",
            "hint": "Run login again, log in until you see your Facebook feed, then close the window."}


async def import_login(cookies: list[dict], source: str, verify: bool = True) -> dict:
    """Copy facebook.com cookies into the FB Scout profile and check the session works."""
    fb = [c for c in cookies if is_facebook_domain(c.get("domain"))]
    if not has_session(fb):
        return {"ok": False, "error": "no_facebook_login",
                "message": f"No logged-in Facebook session found in {source}.",
                "hint": f"Log in to facebook.com in {source} first (use the research account), then retry."}
    async with open_browser(headless=True) as session:
        await session.context.clear_cookies(domain=_FB_COOKIE_DOMAIN)
        await session.context.add_cookies(fb)
        if verify:
            page = await session.page()
            try:
                await page.goto(FB_HOME, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                await check_page(page)
            except FBScoutError as exc:
                await session.context.clear_cookies(domain=_FB_COOKIE_DOMAIN)  # don't keep a dead session
                result = exc.to_dict()
                result["message"] = f"The login copied from {source} did not work: {exc}"
                return result
        ok = await is_logged_in(session.context)
        return {"ok": ok, "logged_in": ok, "source": source, "imported_cookies": len(fb), "browser": session.channel}


async def login(method: str = "browser", cookie_file: str | None = None, timeout_seconds: int = 600,
                force: bool = False) -> dict:
    try:
        if method == "browser":
            return await real_browser_login(timeout_seconds, force)
        if method == "firefox":
            return await import_login(read_firefox_cookies(), "Firefox")
        if method == "cookie_file":
            if not cookie_file:
                return {"ok": False, "error": "invalid_argument", "message": "cookie_file path is required."}
            return await import_login(parse_cookie_file(cookie_file), "the cookie file")
        return {"ok": False, "error": "invalid_argument", "message": f"method must be one of {METHODS}"}
    except FileNotFoundError as exc:
        return {"ok": False, "error": "source_not_found", "message": str(exc)}
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        return {"ok": False, "error": "source_unreadable", "message": f"Could not read the login source: {exc}"}
    except FBScoutError as exc:
        return exc.to_dict()
