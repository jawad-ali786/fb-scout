"""Browser session: real Chrome (or Edge / bundled Chromium) with a persistent profile.

You log in once by hand; the cookies stay in the profile folder under
~/.fbscout (outside the code folder), so sharing the code never shares your login.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .config import VIEWPORT, browser_channels, profile_dir
from .errors import BrowserUnavailable, CheckpointHit, NotLoggedIn, ProfileInUse, TemporarilyBlocked

log = logging.getLogger("fbscout")

FB_HOME = "https://www.facebook.com/"
FB_LOGIN = "https://www.facebook.com/login/"

LAUNCH_ARGS = [
    "--disable-notifications",
    "--no-default-browser-check",
    "--no-first-run",
    "--hide-crash-restore-bubble",
    f"--window-size={VIEWPORT['width'] + 20},{VIEWPORT['height'] + 140}",
]

_PROFILE_LOCK_HINTS = ("processsingleton", "already in use", "existing browser session", "profile appears to be in use")

BLOCK_PHRASES = (
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "you can't use this feature right now",
    "you can’t use this feature right now",
)
CHECKPOINT_PHRASES = ("your account has been locked", "we suspended your account", "confirm your identity")

# Look for notices in dialogs, or in the whole page only when there is no feed
# (so a post that happens to contain these words doesn't stop the run).
_NOTICE_TEXT_JS = """() => {
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
    .map(d => d.innerText || '').join('\\n');
  const hasContent = !!document.querySelector('[role="feed"], [role="article"], [aria-posinset]');
  const body = hasContent ? '' : (document.body ? document.body.innerText : '').slice(0, 4000);
  return (dialogs.slice(0, 4000) + '\\n' + body).toLowerCase();
}"""


@dataclass
class Session:
    context: BrowserContext
    channel: str

    async def page(self) -> Page:
        pages = [p for p in self.context.pages if not p.is_closed()]
        return pages[0] if pages else await self.context.new_page()


@asynccontextmanager
async def open_browser(headless: bool = False) -> AsyncIterator[Session]:
    pw = await async_playwright().start()
    context: BrowserContext | None = None
    tried: list[str] = []
    try:
        for channel in browser_channels():
            user_dir = profile_dir(channel)
            user_dir.mkdir(parents=True, exist_ok=True)
            try:
                context = await pw.chromium.launch_persistent_context(
                    str(user_dir),
                    channel=None if channel == "chromium" else channel,
                    headless=headless,
                    viewport=VIEWPORT,
                    locale="en-US",
                    args=LAUNCH_ARGS,
                )
            except PlaywrightError as exc:
                msg = str(exc)
                if any(h in msg.lower() for h in _PROFILE_LOCK_HINTS):
                    raise ProfileInUse() from exc
                tried.append(f"{channel}: {msg.strip().splitlines()[0][:200]}")
                continue
            log.info("browser started: %s (profile %s)", channel, user_dir)
            yield Session(context, channel)
            break
        else:
            raise BrowserUnavailable(
                "Could not start a browser. Tried: " + " | ".join(tried),
                hint="Install Google Chrome, or run `uv run playwright install chromium` in the plugin's server folder.",
            )
    finally:
        if context is not None:
            try:
                await context.close()
            except PlaywrightError:
                pass
        await pw.stop()


async def is_logged_in(context: BrowserContext) -> bool:
    cookies = await context.cookies([FB_HOME])
    names = {c["name"] for c in cookies}
    return "c_user" in names and "xs" in names


async def check_page(page: Page) -> None:
    """Raise if Facebook shows a login page, a checkpoint or a block notice."""
    path = urlsplit(page.url).path.lower()
    if path.startswith("/checkpoint"):
        raise CheckpointHit()
    if path.startswith("/login") or path.startswith("/login.php"):
        raise NotLoggedIn("Facebook redirected to the login page (session expired?).")
    try:
        text = await page.evaluate(_NOTICE_TEXT_JS)
    except PlaywrightError:
        return
    if any(p in text for p in BLOCK_PHRASES):
        raise TemporarilyBlocked()
    if any(p in text for p in CHECKPOINT_PHRASES):
        raise CheckpointHit()


async def interactive_login(timeout_seconds: int = 300) -> dict:
    """Open a visible browser on the login page and wait until the user has logged in."""
    async with open_browser(headless=False) as session:
        if await is_logged_in(session.context):
            return {"ok": True, "logged_in": True, "already_logged_in": True, "browser": session.channel}
        closed = asyncio.Event()
        session.context.on("close", lambda _: (log.info("login: browser closed"), closed.set()))
        page = await session.page()
        page.on("crash", lambda _: log.warning("login: page crashed"))
        page.on("close", lambda _: log.info("login: tab closed"))
        page.on("framenavigated", lambda f: f == page.main_frame and log.info("login: at %s", f.url.split("?")[0]))
        await page.goto(FB_LOGIN, wait_until="domcontentloaded")
        await page.bring_to_front()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(30, timeout_seconds)
        while loop.time() < deadline:
            # Only the whole browser closing ends the wait; a closed tab is fine.
            if closed.is_set() or not session.context.pages:
                return {"ok": False, "error": "browser_closed",
                        "message": "The browser window was closed before login finished.",
                        "hint": "Run login again and keep the window open until your Facebook feed is shown."}
            try:
                logged_in = await is_logged_in(session.context)
            except PlaywrightError:
                logged_in = False
            on_checkpoint = any("/checkpoint" in p.url for p in session.context.pages)
            if logged_in and not on_checkpoint:
                await asyncio.sleep(3)  # let Facebook finish setting cookies
                return {"ok": True, "logged_in": True, "already_logged_in": False, "browser": session.channel}
            await asyncio.sleep(2)
        return {
            "ok": False,
            "error": "timeout",
            "message": f"Not logged in after {timeout_seconds}s.",
            "hint": "Run fb_login again and finish logging in (including any 2FA or checkpoint) in the Chrome window.",
        }
