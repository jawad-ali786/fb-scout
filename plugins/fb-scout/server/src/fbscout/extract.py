"""In-page extraction. All Facebook DOM knowledge lives in this file.

Facebook's CSS class names are obfuscated and change often, so we only rely on
ARIA roles, a few data attributes and URL patterns. If extraction breaks after
a Facebook update, this is the file to fix (look at a run's debug/ folder).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.async_api import ElementHandle, Page
from playwright.async_api import Error as PlaywrightError

from .urls import classify_kind, clean_url, first_link, group_post_from_photo, link_role, pick_permalink

# Button labels (English UI). Add labels here for other UI languages.
SEE_MORE_LABELS = ["see more", "see more…", "see more...", "… see more", "...see more"]

# "See more" / "See less" button text that Facebook renders inside the message, at a line end.
BUTTON_TEXT_RE = re.compile(r"(?:[ \t]*\bSee (?:more|less)\b[….]*)+[ \t]*$", re.MULTILINE)

# Facebook scatters visually hidden "Facebook" text through posts (anti-scraping);
# a line that is only this word is treated as filler.
FILLER_LINES = {"Facebook"}

# Top-level post containers on a search/feed page (not comments).
FIND_POSTS_JS = """() => {
  const root = document.querySelector('[role="main"]') || document.body;
  const isComment = (el) => /^(comment|reply)\\b/i.test(el.getAttribute('aria-label') || '');
  let cands = Array.from(root.querySelectorAll('[role="article"], [aria-posinset]')).filter(el => !isComment(el));
  if (cands.length === 0) {
    const feed = root.querySelector('[role="feed"]');
    if (feed) cands = Array.from(feed.children);
  }
  // A candidate holding 2+ other candidates is a list wrapper, not a post.
  cands = cands.filter(el => cands.filter(o => o !== el && el.contains(o)).length < 2);
  // Keep the outermost container of each post.
  cands = cands.filter(el => !cands.some(o => o !== el && o.contains(el)));
  return cands.filter(el => {
    if (el.hasAttribute('data-fbscout-seen')) return false;
    const r = el.getBoundingClientRect();
    if (r.height < 40 || r.width < 200) return false;
    return (el.innerText || '').trim().length >= 25;   // skip loading skeletons
  });
}"""

# A post page opened from a link can show the post in a dialog on top of the
# home feed, whose posts have comment buttons and comments of their own. So all
# comment work happens inside the post's container, marked by find_post_scope.
SCOPE_ATTR = "data-fbscout-scope"
_SCOPE = "(document.querySelector('[data-fbscout-scope]') || document)"

# Marks the post's container: the top-most dialog, else the main column, whose
# text contains the start of the post's text (any dialog/main if there is no text).
POST_SCOPE_JS = """(snippet) => {
  const norm = (s) => (s || '').toLowerCase().replace(/[^\\p{L}\\p{N}]+/gu, '');
  const want = norm(snippet);
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'))
    .filter(d => d.getBoundingClientRect().height > 0).reverse();
  const main = document.querySelector('[role="main"]');
  const el = [...dialogs, main].filter(Boolean).find(c => !want || norm(c.innerText).includes(want));
  document.querySelectorAll('[data-fbscout-scope]').forEach(n => n.removeAttribute('data-fbscout-scope'));
  if (!el) return null;
  el.setAttribute('data-fbscout-scope', '1');
  return el.getAttribute('role') || el.tagName.toLowerCase();
}"""

# Comment containers in the post's container.
FIND_COMMENTS_JS = """() => {
  return Array.from(__SCOPE__.querySelectorAll('[role="article"]')).filter(el => {
    if (el.hasAttribute('data-fbscout-seen')) return false;
    if (/^(comment|reply) by/i.test(el.getAttribute('aria-label') || '')) return true;
    return Array.from(el.querySelectorAll('a[href*="comment_id="]')).some(a => a.closest('[role="article"]') === el);
  });
}"""

SEE_MORE_JS = """(el, labels) => {
  const want = new Set(labels);
  let n = 0;
  for (const b of el.querySelectorAll('[role="button"]')) {
    const t = (b.innerText || '').trim().toLowerCase();
    if (want.has(t) && !b.closest('a[href]')) { b.click(); n++; }
  }
  return n;
}"""

# Shared by posts and comments. With ownOnly=true, content of nested
# [role=article] elements (replies) is ignored.
EXTRACT_JS = """(el, ownOnly) => {
  const own = (n) => !ownOnly || n.closest('[role="article"]') === el;
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const links = Array.from(el.querySelectorAll('a')).map((a, i) => {
    const raw = a.getAttribute('href');
    const ok = raw && raw !== '#' && !raw.startsWith('#') && !raw.toLowerCase().startsWith('javascript');
    return { i, own: own(a), href: ok ? abs(raw) : null,
             text: (a.innerText || '').trim().slice(0, 300),
             aria: (a.getAttribute('aria-label') || '').slice(0, 300),
             img: !!a.querySelector('img, image, video') };
  }).filter(l => l.own);

  const MSG = '[data-ad-preview="message"], [data-ad-comet-preview="message"]';
  const message = Array.from(el.querySelectorAll(MSG))
    .filter(n => own(n) && !n.parentElement.closest(MSG))
    .map(n => (n.innerText || '').trim()).filter(Boolean).join('\\n\\n');

  const blocks = [];
  const seen = new Set();
  for (const n of el.querySelectorAll('div[dir="auto"], span[dir="auto"]')) {
    if (!own(n) || n.querySelector('div[dir="auto"], span[dir="auto"]')) continue;
    if (n.closest('a, [role="button"], h1, h2, h3, h4')) continue;
    const t = (n.innerText || '').trim();
    if (t && !seen.has(t)) { seen.add(t); blocks.push(t); }
  }

  let fullText = el.innerText || '';
  if (ownOnly) {
    const c = el.cloneNode(true);
    c.querySelectorAll('[role="article"]').forEach(x => x.remove());
    fullText = c.innerText || c.textContent || '';
  }
  // Facebook's automatic image descriptions, e.g. "May be an image of text that says '...'"
  // (its own OCR of posters/flyers). Emoji are <img> too, with a 1-2 character alt.
  const images = Array.from(el.querySelectorAll('img[alt]')).filter(own)
    .map(i => (i.getAttribute('alt') || '').trim()).filter(a => a.length >= 4);
  // Text of nested [role=article] elements: comment previews under a post.
  const nested = Array.from(el.querySelectorAll('[role="article"]'))
    .map(a => (a.innerText || '').trim()).filter(Boolean);
  return { message, blocks, full_text: fullText.trim(), links, images, nested,
           aria_label: el.getAttribute('aria-label') || '' };
}"""

# Image descriptions that say nothing about the content.
NO_IMAGE_TEXT = {"no photo description available.", "no photo description available"}

# Visible links whose real href Facebook fills in only on hover (the timestamp
# permalink): no href, "#", or a same-page placeholder such as "?__cft__[0]=...".
PLACEHOLDER_LINKS_JS = """(el) => {
  const page = (h) => { const u = new URL(h, location.href); return u.protocol + '//' + u.host + u.pathname; };
  const here = page(location.href);
  const placeholder = (a) => {
    const raw = (a.getAttribute('href') || '').trim();
    if (!raw || raw.startsWith('#') || raw.toLowerCase().startsWith('javascript')) return true;
    try { return page(raw) === here; } catch (e) { return false; }
  };
  return Array.from(el.querySelectorAll('a')).map((a, i) => ({ a, i })).filter(({ a }) => {
    const r = a.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && placeholder(a);
  }).map(({ i }) => i);
}"""

# Facebook's tooltips fade out slowly, so the previous post's date can still be on
# the page. Mark the current ones before hovering; TOOLTIP_JS skips them unless
# their text changed (Facebook may reuse the element).
MARK_TOOLTIPS_JS = """() => document.querySelectorAll('[role="tooltip"]')
  .forEach(t => t.setAttribute('data-fbscout-old', (t.innerText || '').trim()))"""

TOOLTIP_JS = """() => {
  const t = Array.from(document.querySelectorAll('[role="tooltip"]'))
    .map(x => [x.getAttribute('data-fbscout-old'), (x.innerText || '').trim()])
    .filter(([old, text]) => text && old !== text).map(([, text]) => text);
  return t.length ? t[t.length - 1] : null;
}"""

MORE_COMMENTS_JS = """() => {
  const re = /^(view (\\d+ )?(more|previous) (comments?|repl(y|ies))|view (all )?\\d+ (more )?(comments?|repl(y|ies))|\\d+ (more )?repl(y|ies)|(see|view) (all|more) (comments|replies)|.{1,80} replied\\s*[·•]\\s*\\d+ repl(y|ies))$/i;
  const btn = Array.from(__SCOPE__.querySelectorAll('[role="button"]')).find(b =>
    !b.hasAttribute('data-fbscout-clicked') && b.offsetParent !== null && re.test((b.innerText || '').trim()));
  if (!btn) return false;
  btn.setAttribute('data-fbscout-clicked', '1');
  btn.click();
  return true;
}"""

# A post page opened from a link may show the post with its comments collapsed.
# The comment count under the post ("11") is a button labelled "Leave a comment"
# that opens them; without a number there are no comments to open.
COMMENT_COUNT_LABELS = ["leave a comment", "comment", "comments"]

OPEN_COMMENTS_JS = """(labels) => {
  const want = new Set(labels);
  const loaded = __SCOPE__.querySelector('[role="article"][aria-label^="Comment by" i], [role="article"][aria-label^="Reply by" i]');
  if (loaded) return false;
  const btn = Array.from(__SCOPE__.querySelectorAll('[role="button"][aria-label]')).find(b =>
    b.offsetParent !== null && want.has(b.getAttribute('aria-label').trim().toLowerCase())
    && /\\d/.test(b.innerText || ''));
  if (!btn) return false;
  btn.click();
  return true;
}"""

COUNT_COMMENTS_JS = "() => (" + FIND_COMMENTS_JS + ")().length"

# The comment area has rendered: comments are there, or the comment button is.
COMMENTS_READY_JS = """(labels) => {
  const want = new Set(labels);
  return !!__SCOPE__.querySelector('[role="article"][aria-label^="Comment by" i]')
    || Array.from(__SCOPE__.querySelectorAll('[role="button"][aria-label]')).some(b =>
         b.offsetParent !== null && want.has(b.getAttribute('aria-label').trim().toLowerCase()));
}"""

# Facebook shows only "Most relevant" comments by default (filtered, possibly spam-hidden).
# The sort button opens a menu whose items start with the order's name.
COMMENT_SORT_LABELS = ["most relevant", "top comments", "newest", "most recent", "oldest"]
ALL_COMMENTS_LABEL = "all comments"

OPEN_COMMENT_SORT_JS = """(labels) => {
  const want = new Set(labels);
  const btn = Array.from(__SCOPE__.querySelectorAll('[role="button"]')).find(b =>
    b.offsetParent !== null && want.has((b.innerText || '').trim().split('\\n')[0].trim().toLowerCase()));
  if (!btn) return false;
  btn.click();
  return true;
}"""

PICK_COMMENT_SORT_JS = """(label) => {
  const item = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemradio"], [role="option"]'))
    .find(m => (m.innerText || '').trim().toLowerCase().startsWith(label));
  if (!item) return false;
  item.click();
  return true;
}"""

# Comment scripts look only inside the post's container (the sort menu is a page-level popup).
(FIND_COMMENTS_JS, MORE_COMMENTS_JS, OPEN_COMMENTS_JS, COUNT_COMMENTS_JS, COMMENTS_READY_JS,
 OPEN_COMMENT_SORT_JS) = (js.replace("__SCOPE__", _SCOPE) for js in (
    FIND_COMMENTS_JS, MORE_COMMENTS_JS, OPEN_COMMENTS_JS, COUNT_COMMENTS_JS, COMMENTS_READY_JS, OPEN_COMMENT_SORT_JS))

# Tab titles: "<group> | <post title> | Facebook", "(3) <group> | Facebook", "Search results | <group> | Facebook".
_TITLE_NOISE = {"facebook", "search", "search results", "groups", "group", "home", "log in", "log into facebook"}


def group_name_from_title(title: str | None) -> str | None:
    """The group's name from a group page's tab title (search results in a group don't repeat it)."""
    title = re.sub(r"^\(\d+\+?\)\s*", "", (title or "").strip())   # notification count
    for part in (p.strip() for p in title.split(" | ")):
        if part and part.lower() not in _TITLE_NOISE:
            return part[:200]
    return None


END_OF_RESULTS_JS = """() => /\\b(end of results|no more results|we didn't find any results|we didn’t find any results)\\b/i
  .test(document.body ? document.body.innerText.slice(-6000) : '')"""

RESULTS_READY_JS = """() => !!document.querySelector('[role="main"] [role="article"], [role="main"] [aria-posinset], [role="feed"]')
  || /didn't find any results|didn’t find any results|no results found/i.test(document.body ? document.body.innerText : '')"""

# DOM structure counts written to debug/ when nothing is found.
DOM_SUMMARY_JS = """() => ({
  url: location.href, title: document.title,
  main: !!document.querySelector('[role="main"]'),
  feeds: document.querySelectorAll('[role="feed"]').length,
  articles: document.querySelectorAll('[role="article"]').length,
  posinset: document.querySelectorAll('[aria-posinset]').length,
  message_blocks: document.querySelectorAll('[data-ad-preview="message"], [data-ad-comet-preview="message"]').length,
  dialogs: document.querySelectorAll('[role="dialog"]').length,
  body_text_start: (document.body ? document.body.innerText : '').slice(0, 1500)
})"""

# Page HTML without scripts (scripts carry session tokens; never save them).
SAFE_HTML_JS = """() => {
  const c = document.documentElement.cloneNode(true);
  c.querySelectorAll('script, style, link, noscript, iframe').forEach(n => n.remove());
  return '<!-- saved by fb-scout for debugging; scripts removed -->\\n' + c.outerHTML;
}"""


@dataclass
class Extracted:
    text: str
    full_text: str
    post_url: str | None
    kind: str
    author_name: str | None = None
    author_url: str | None = None
    group_name: str | None = None
    group_url: str | None = None
    comment_url: str | None = None
    time_text: str | None = None
    time_exact: str | None = None
    time_link_index: int | None = None   # index of the timestamp among el's <a> elements
    image_text: str | None = None
    is_post: bool = True
    name_text: str | None = None          # linked names of people/pages/groups (for include_name_matches)
    aria_label: str = ""
    links: list[dict] = field(default_factory=list)


async def find_handles(page: Page, js: str) -> list[ElementHandle]:
    array = await page.evaluate_handle(js)
    try:
        props = await array.get_properties()
        items = sorted(props.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 1 << 30)
        return [h for _, v in items if (h := v.as_element()) is not None]
    finally:
        await array.dispose()


async def mark_seen(el: ElementHandle) -> bool:
    try:
        await el.evaluate("el => el.setAttribute('data-fbscout-seen', '1')")
        return True
    except PlaywrightError:
        return False


async def expand_see_more(page: Page, el: ElementHandle) -> int:
    try:
        n = await el.evaluate(SEE_MORE_JS, SEE_MORE_LABELS)
    except PlaywrightError:
        return 0
    if n:
        await page.wait_for_timeout(600)
    return n


def clean_text(text: str) -> str:
    """Drop "See more" / "See less" button labels and hidden filler lines from the message text."""
    text = BUTTON_TEXT_RE.sub("", text)
    return "\n".join(line for line in text.split("\n") if line.strip() not in FILLER_LINES).strip()


def image_text(alts: list[str]) -> str | None:
    """Facebook's image descriptions, deduplicated; None when there are none worth keeping."""
    kept = dict.fromkeys(a for a in alts if a.strip().lower() not in NO_IMAGE_TEXT)
    return "\n".join(kept) or None


def post_snippet(text: str | None, length: int = 30) -> str:
    """The first letters and digits of a post's text, to recognise the post on its own page."""
    return re.sub(r"[\W_]+", "", clean_text(text or "").lower())[:length]


async def find_post_scope(page: Page, text: str | None, timeout_ms: int = 12000) -> str | None:
    """Mark the container that holds the post (dialog or main column). Returns its role, or None."""
    try:
        handle = await page.wait_for_function(POST_SCOPE_JS, arg=post_snippet(text), timeout=timeout_ms)
        return await handle.json_value()
    except PlaywrightError:
        return None


async def open_comments(page: Page) -> int:
    """Open collapsed comments if needed. Returns how many comments are now on the page."""
    try:
        try:
            await page.wait_for_function(COMMENTS_READY_JS, arg=COMMENT_COUNT_LABELS, timeout=10000)
        except PlaywrightError:
            pass
        if await page.evaluate(OPEN_COMMENTS_JS, COMMENT_COUNT_LABELS):
            try:
                await page.wait_for_function(f"() => ({COUNT_COMMENTS_JS})() > 0", timeout=8000)
            except PlaywrightError:
                pass
        return await page.evaluate(COUNT_COMMENTS_JS)
    except PlaywrightError:
        return 0


async def show_all_comments(page: Page) -> bool:
    """Switch the post's comment order from "Most relevant" to "All comments". True if switched."""
    try:
        if not await page.evaluate(OPEN_COMMENT_SORT_JS, COMMENT_SORT_LABELS):
            return False
        await page.wait_for_timeout(900)   # menu animation
        picked = await page.evaluate(PICK_COMMENT_SORT_JS, ALL_COMMENTS_LABEL)
        if not picked:
            await page.keyboard.press("Escape")
            return False
        await page.wait_for_timeout(1500)  # comments reload
        return True
    except PlaywrightError:
        return False


async def _hover_tooltip_of(page: Page, anchor: ElementHandle, timeout_ms: int = 1500) -> str | None:
    """Hover a link and return the new tooltip it opens (the full date for a timestamp).

    Raises PlaywrightError if the link can't be hovered.
    """
    await page.evaluate(MARK_TOOLTIPS_JS)
    await anchor.hover(timeout=1500)
    try:
        handle = await page.wait_for_function(TOOLTIP_JS, timeout=timeout_ms)
        tip = await handle.json_value()
    except PlaywrightError:
        return None
    return tip if isinstance(tip, str) and any(ch.isdigit() for ch in tip) and len(tip) < 120 else None


async def _reveal_timestamp(page: Page, el: ElementHandle, limit: int = 4) -> tuple[int | None, str | None]:
    """Hover placeholder links until one becomes a content permalink (the timestamp).

    Facebook fills in the timestamp's href only on hover and shows the full date
    in a tooltip. Returns (anchor index, tooltip), or (None, None).
    """
    try:
        indices = await el.evaluate(PLACEHOLDER_LINKS_JS)
        anchors = await el.query_selector_all("a")
    except PlaywrightError:
        return None, None
    for i in indices[:limit]:
        if i >= len(anchors):
            break
        try:
            tip = await _hover_tooltip_of(page, anchors[i])
            href = await anchors[i].evaluate("a => a.href")
        except PlaywrightError:
            continue
        if link_role(href) == "content":
            return i, tip
    return None, None


async def hover_time_exact(page: Page, el: ElementHandle, index: int | None) -> str | None:
    """Hover the timestamp link (index among el's <a> elements) and read Facebook's tooltip (full date)."""
    if index is None:
        return None
    try:
        anchors = await el.query_selector_all("a")
        if index >= len(anchors):
            return None
        return await _hover_tooltip_of(page, anchors[index])
    except PlaywrightError:
        return None


def _names(links: list[dict]) -> list[str]:
    """Names of the people, pages and groups linked in the post (author, group, mentions)."""
    return list(dict.fromkeys(l["text"] for l in links
                              if l.get("text") and link_role(l.get("href")) in ("profile", "group")))


def _body_text(full_text: str, links: list[dict], nested: list[str]) -> str:
    """Everything in the post's container except names and comment previews.

    Facebook search also matches the names of the group, page or person, so a
    keyword found only there doesn't mean the post mentions it. Attachments
    (link previews, a sale listing's title) stay in.
    """
    body = full_text
    for chunk in nested:
        body = body.replace(chunk, "\n")
    for name in sorted(_names(links), key=len, reverse=True):
        body = body.replace(name, "\n")
    return body


def _link_by_index(links: list[dict], index: int | None) -> dict | None:
    return next((l for l in links if l.get("i") == index), None) if index is not None else None


def _time_text(link: dict | None) -> str | None:
    """Visible timestamp text ("3d", "2 hours ago"); never an image's description."""
    if not link or link.get("img"):
        return None
    t = (link.get("text") or link.get("aria") or "").strip()
    return t if t and len(t) <= 60 else None


async def extract_post(page: Page, el: ElementHandle, see_more: bool = True) -> Extracted:
    if see_more:
        await expand_see_more(page, el)
    ts_index, time_exact = await _reveal_timestamp(page, el)
    raw = await el.evaluate(EXTRACT_JS, False)

    links = raw["links"]
    perma = pick_permalink(links)
    author = first_link(links, "profile")
    group = first_link(links, "group")
    if ts_index is None and perma:
        ts_index = perma["index"]
        time_exact = await hover_time_exact(page, el, ts_index)

    post_url = perma["url"] if perma else None
    kind = perma["kind"] if perma else "unknown"
    if kind == "photo" and group:
        derived = group_post_from_photo(post_url, group["url"])
        if derived:
            post_url, kind = derived, "group_post"

    text = raw["message"] or "\n".join(raw["blocks"]) or raw["full_text"]
    time_text = _time_text(_link_by_index(links, ts_index))
    return Extracted(
        text=clean_text(text),
        full_text=clean_text(_body_text(raw["full_text"], links, raw["nested"])),
        post_url=post_url,
        kind=kind,
        # People/page cards in search results have no permalink, no time and no message.
        is_post=bool(perma or time_text or time_exact or raw["message"]),
        author_name=author["name"] if author else None,
        author_url=author["url"] if author else None,
        group_name=group["name"] if group else None,
        group_url=group["url"] if group else None,
        time_text=time_text,
        time_exact=time_exact,
        time_link_index=ts_index,
        image_text=image_text(raw["images"]),
        name_text="\n".join(_names(links)) or None,
        aria_label=raw["aria_label"],
        links=links,
    )


async def extract_comment(page: Page, el: ElementHandle) -> Extracted:
    """Comment text, links and author. The exact time costs a hover, so the caller
    reads it with hover_time_exact(..., time_link_index) only for comments it keeps."""
    await expand_see_more(page, el)
    raw = await el.evaluate(EXTRACT_JS, True)
    links = raw["links"]
    comment_link = next((l for l in links if link_role(l["href"]) == "comment"), None)
    comment_url = clean_url(comment_link["href"]) if comment_link else None
    author = first_link(links, "profile")
    label = raw["aria_label"]
    if comment_url:
        kind = classify_kind(comment_url)
    else:
        kind = "reply" if label.lower().startswith("reply") else "comment"
    text = "\n".join(raw["blocks"]) or raw["full_text"]
    return Extracted(
        text=clean_text(text),
        full_text=raw["full_text"],
        post_url=None,
        kind=kind,
        comment_url=comment_url,
        author_name=author["name"] if author else None,
        author_url=author["url"] if author else None,
        time_text=(comment_link["text"] or None) if comment_link else None,
        time_link_index=comment_link["i"] if comment_link else None,
        image_text=image_text(raw["images"]),
        aria_label=label,
        links=links,
    )


# ---- Marketplace -----------------------------------------------------------
# Search results are a grid of links to /marketplace/item/<id>/ whose text is
# "PRICE\nTITLE\nLOCATION"; the image's alt text is "TITLE in LOCATION".

FIND_LISTINGS_JS = """() => Array.from(document.querySelectorAll('[role="main"] a[href*="/marketplace/item/"]'))
  .filter(a => {
    if (a.hasAttribute('data-fbscout-seen')) return false;
    const r = a.getBoundingClientRect();
    return r.width >= 100 && r.height >= 100;
  })"""

LISTINGS_READY_JS = """() => !!document.querySelector('[role="main"] a[href*="/marketplace/item/"]')
  || /no listings found|we couldn't find|we couldn’t find/i.test(document.body ? document.body.innerText : '')"""

LISTING_CARD_JS = """(a) => {
  const img = a.querySelector('img');
  return { href: a.href, text: (a.innerText || '').trim(), aria: a.getAttribute('aria-label') || '',
           alt: img ? (img.getAttribute('alt') || '').trim() : '' };
}"""

# The listing page: its own column (or dialog), without "Related searches" / "Today's picks".
LISTING_PAGE_JS = """() => {
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(d => d.getBoundingClientRect().height > 0);
  const root = dialogs.length ? dialogs[dialogs.length - 1] : (document.querySelector('[role="main"]') || document.body);
  const title = Array.from(root.querySelectorAll('h1')).map(h => (h.innerText || '').trim()).find(Boolean) || null;
  const sellers = Array.from(root.querySelectorAll('a[href*="/marketplace/profile/"]'))
    .map(a => ({ href: a.href, text: (a.innerText || '').trim() }));
  return { title, text: root.innerText || '', sellers };
}"""

LISTING_STOP_LINES = ("related searches", "today's picks", "today’s picks", "send seller a message", "similar items")
_PRICE_RE = re.compile(r"^(free|.{0,4}?\s?[\d][\d.,]*\s?[kKmM]?)\b", re.IGNORECASE)
# A second, struck-out price on discounted listings ("PKR8,100" then "PKR10,000").
_OLD_PRICE_RE = re.compile(r"^[^\w\s]{0,3}\s?(?:[A-Za-z]{1,4}\s?)?\d[\d.,]*$")
# Buttons and labels inside a listing's description.
LISTING_UI_LINES = {"see translation", "see original", "see more", "see less", "translated", "message", "send"}


@dataclass
class Listing:
    url: str | None
    title: str
    price: str | None = None
    location: str | None = None
    description: str | None = None
    condition: str | None = None
    seller_name: str | None = None
    seller_url: str | None = None
    listed_text: str | None = None     # "2 days ago" from "Listed 2 days ago in Karachi"


def parse_listing_card(card: dict) -> Listing:
    lines = [l.strip() for l in (card.get("text") or "").split("\n") if l.strip()]
    price = lines[0] if lines and _PRICE_RE.match(lines[0]) else None
    rest = lines[1:] if price else lines
    while price and len(rest) > 2 and _OLD_PRICE_RE.match(rest[0]):
        rest = rest[1:]
    location = rest[-1] if len(rest) >= 2 else None
    title = " ".join(rest[:-1] if location else rest)
    alt = card.get("alt") or ""
    if location and alt.endswith(f" in {location}"):       # the alt keeps the title's own line breaks
        title = alt[: -len(f" in {location}")].strip() or title
    return Listing(url=clean_url(card.get("href")), title=title, price=price, location=location)


def parse_listing_page(page_data: dict) -> dict:
    """Details from a listing page: description, condition, seller, "Listed ... in ..."."""
    lines = [l.strip() for l in (page_data.get("text") or "").split("\n") if l.strip()]
    stop = next((i for i, l in enumerate(lines) if l.lower() in LISTING_STOP_LINES), len(lines))
    lines = lines[:stop]
    out: dict = {"title": page_data.get("title")}
    at = next((i for i, l in enumerate(lines) if l.lower().startswith("listed ")), None)
    if at is not None:
        # "Listed 2 days ago in Karachi, Pakistan", or "Listed 2 weeks ago in" with the place on the next line
        m = re.match(r"listed\s+(.*?)(?:\s+in(?:\s+(.+))?)?$", lines[at], re.IGNORECASE)
        if m:
            out["listed_text"] = m.group(1)
            place = m.group(2)
            if not place and lines[at].lower().endswith(" in") and at + 1 < len(lines):
                place = lines[at + 1]
            out["location"] = place
    lower = [l.lower() for l in lines]
    start = lower.index("details") + 1 if "details" in lower else None
    if start is not None:
        if start < len(lines) and lower[start] == "condition":
            out["condition"] = lines[start + 1] if start + 1 < len(lines) else None
            start += 2
        end = next((i for i in range(start, len(lines))
                    if lower[i] == "seller information" or "location is approximate" in lower[i]), len(lines))
        description = clean_text("\n".join(l for l in lines[start:end] if l.lower() not in LISTING_UI_LINES))
        out["description"] = description or None
    seller = next((s for s in page_data.get("sellers") or []
                   if s.get("text") and s["text"].lower() not in ("seller details", "see details")), None)
    if seller:
        out["seller_name"] = seller["text"].split("\n")[0][:200]
        out["seller_url"] = clean_url(seller["href"].split("?")[0])
    return out
