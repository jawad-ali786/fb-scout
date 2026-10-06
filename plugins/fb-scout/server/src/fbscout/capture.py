"""Element screenshots with the keyword highlighted, and optionally names blurred.

Highlighting uses the CSS Custom Highlight API, so Facebook's DOM is never
modified (no risk of breaking the page's React state). Blurring only adds a
temporary data attribute to profile links, removed right after the screenshot.
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import ElementHandle, Page
from playwright.async_api import Error as PlaywrightError

from .config import VIEWPORT
from .urls import link_role

# Applied only while the screenshot is taken: hide the fixed top bar so it
# can't cover the post, paint the keyword highlight, blur marked profile links.
SCREENSHOT_CSS = """
[role="banner"] { visibility: hidden !important; }
::highlight(fbscout) { background-color: #ffe600; color: #000000; }
[data-fbscout-blur] { filter: blur(7px) !important; }
"""

LINK_HREFS_JS = "(el) => Array.from(el.querySelectorAll('a')).map(a => a.href || '')"

MARK_BLUR_JS = """(el, indices) => {
  const links = el.querySelectorAll('a');
  for (const i of indices) if (links[i]) links[i].setAttribute('data-fbscout-blur', '1');
  return indices.length;
}"""

CLEAR_BLUR_JS = "(el) => el.querySelectorAll('[data-fbscout-blur]').forEach(n => n.removeAttribute('data-fbscout-blur'))"

HIGHLIGHT_JS = """(el, terms) => {
  if (!window.CSS || !CSS.highlights || typeof Highlight === 'undefined') return -1;
  const needles = terms.map(t => t.toLowerCase()).filter(Boolean);
  const ranges = [];
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const value = node.nodeValue || '';
    const hay = value.toLowerCase();
    if (hay.length !== value.length) continue;   // rare case-mapping length change
    for (const n of needles) {
      let i = hay.indexOf(n);
      while (i !== -1) {
        const r = new Range();
        r.setStart(node, i);
        r.setEnd(node, i + n.length);
        ranges.push(r);
        i = hay.indexOf(n, i + n.length);
      }
    }
  }
  CSS.highlights.set('fbscout', new Highlight(...ranges));
  return ranges.length;
}"""

CLEAR_HIGHLIGHT_JS = "() => { if (window.CSS && CSS.highlights) CSS.highlights.delete('fbscout'); }"

WAIT_IMAGES_JS = """async (el) => {
  const imgs = Array.from(el.querySelectorAll('img')).filter(i => !i.complete);
  const loads = imgs.map(i => new Promise(r => { i.addEventListener('load', r, {once: true}); i.addEventListener('error', r, {once: true}); }));
  await Promise.race([Promise.all(loads), new Promise(r => setTimeout(r, 3000))]);
  return imgs.length;
}"""


async def highlight(el: ElementHandle, terms: list[str]) -> int:
    try:
        return await el.evaluate(HIGHLIGHT_JS, terms)
    except PlaywrightError:
        return -1


async def mark_names(el: ElementHandle) -> int:
    """Mark every link to a person's or page's profile (names and profile pictures) for blurring."""
    hrefs = await el.evaluate(LINK_HREFS_JS)
    indices = [i for i, href in enumerate(hrefs) if link_role(href) == "profile"]
    return await el.evaluate(MARK_BLUR_JS, indices)


async def capture_element(page: Page, el: ElementHandle, path: Path, terms: list[str], with_highlight: bool = True,
                          blur_names: bool = False) -> None:
    """Screenshot just this element. Raises PlaywrightError on failure."""
    if with_highlight and terms:
        await highlight(el, terms)
    try:
        if blur_names:
            await mark_names(el)
        # Park the mouse in a corner so no hover card/tooltip covers the post.
        await page.mouse.move(VIEWPORT["width"] - 5, VIEWPORT["height"] - 5)
        await el.scroll_into_view_if_needed(timeout=5000)
        try:
            await el.evaluate(WAIT_IMAGES_JS)
        except PlaywrightError:
            pass
        await page.wait_for_timeout(300)
        await el.screenshot(path=str(path), style=SCREENSHOT_CSS, animations="disabled", timeout=20000)
    finally:
        try:
            await page.evaluate(CLEAR_HIGHLIGHT_JS)
            if blur_names:
                await el.evaluate(CLEAR_BLUR_JS)
        except PlaywrightError:
            pass
