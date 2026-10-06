"""Facebook URL helpers: build search URLs, strip tracking, classify what a link points to.

Pure functions (no browser), so they are easy to unit-test and to fix when
Facebook changes its URL formats.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

BASE = "https://www.facebook.com"

# Query parameters that identify content. Everything else (__cft__, __tn__,
# ref, mibextid, notif_id, ...) is tracking and gets dropped.
KEEP_PARAMS = ("id", "story_fbid", "fbid", "v", "set", "multi_permalinks", "comment_id", "reply_comment_id")

# First path segments that are Facebook features, not user/page names.
RESERVED = {
    "ads", "bookmarks", "business", "dialog", "events", "friends", "fundraisers", "gaming", "groups",
    "hashtag", "help", "home.php", "l.php", "login", "login.php", "marketplace", "media", "memories",
    "messages", "notifications", "pages", "people", "permalink.php", "photo", "photo.php", "photos",
    "policies", "privacy", "profile.php", "public", "reel", "reels", "saved", "search", "settings",
    "share", "sharer", "sharer.php", "stories", "story.php", "tr", "video.php", "watch",
}

# Kinds a post-level permalink can have, best first (used to pick the permalink).
CONTENT_PRIORITY = ("group_post", "post", "reel", "video", "photo", "event", "marketplace")


def _is_fb_host(host: str) -> bool:
    host = host.lower().split(":")[0]
    return host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.com" or host.endswith(".fb.com")


def _split(url: str):
    try:
        return urlsplit(url)
    except ValueError:
        return None


def clean_url(url: str | None) -> str | None:
    """Canonical form: https://www.facebook.com/<path>/ with only identifying query params."""
    if not url:
        return None
    parts = _split(url.strip())
    if not parts or parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not _is_fb_host(parts.netloc):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    # Outbound link wrapper: l.facebook.com/l.php?u=<real url>
    if parts.path.rstrip("/") == "/l.php" and query.get("u"):
        return clean_url(query["u"])

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/" and not path.endswith(".php"):
        path = path.rstrip("/") + "/"
    # /groups/<g>/?multi_permalinks=<post> is the same post as /groups/<g>/posts/<post>/
    group = re.fullmatch(r"/groups/([^/]+)/", path)
    if group and re.fullmatch(r"\d+", query.get("multi_permalinks", "")):
        path = f"/groups/{group.group(1)}/posts/{query.pop('multi_permalinks')}/"
    kept = [(k, query[k]) for k in KEEP_PARAMS if k in query]
    return urlunsplit(("https", "www.facebook.com", path, urlencode(kept), ""))


def _segments_and_query(url: str | None):
    u = clean_url(url)
    if not u:
        return None
    parts = urlsplit(u)
    if not _is_fb_host(parts.netloc):
        return None
    segs = [s for s in parts.path.lower().split("/") if s]
    return segs, dict(parse_qsl(parts.query)), parts.path.lower()


def classify_kind(url: str | None) -> str:
    """What a Facebook URL points to: post, group_post, comment, reply, reel, video, photo, event, marketplace, unknown."""
    parsed = _segments_and_query(url)
    if not parsed:
        return "unknown"
    segs, qs, path = parsed
    base = _classify_base(segs, qs, path)
    if "comment_id" in qs or "reply_comment_id" in qs:
        # Facebook also adds comment_id to the commenter's profile link; only a
        # content URL + comment_id is a real comment permalink.
        if base == "unknown":
            return "unknown"
        return "reply" if "reply_comment_id" in qs else "comment"
    return base


def _classify_base(segs: list[str], qs: dict, path: str) -> str:
    if segs[:1] == ["groups"]:
        if len(segs) >= 4 and segs[2] in ("posts", "permalink"):
            return "group_post"
        if "multi_permalinks" in qs:
            return "group_post"
        if len(segs) >= 3 and segs[2] == "videos":
            return "video"
        return "unknown"
    # /<page-or-user>/<section>/<id>/ (not /search/posts/ etc.)
    section = segs[1] if len(segs) >= 3 and segs[0] not in RESERVED else None
    if segs[:1] in (["reel"], ["reels"]) and len(segs) >= 2:
        return "reel"
    if (segs[:1] == ["watch"] and "v" in qs) or path == "/video.php" or section == "videos":
        return "video"
    if path in ("/photo.php", "/photo/") or section == "photos":
        return "photo"
    if segs[:1] == ["events"] and len(segs) >= 2:
        return "event"
    if segs[:2] == ["marketplace", "item"]:
        return "marketplace"
    if segs[:1] == ["share"] and len(segs) >= 3:
        return {"p": "post", "r": "reel", "v": "video"}.get(segs[1], "unknown")
    if section == "posts" or path in ("/permalink.php", "/story.php"):
        return "post"
    return "unknown"


def link_role(url: str | None) -> str:
    """Role of a link inside a post: content, comment, group, profile, hashtag, external, other, none."""
    u = clean_url(url)
    if not u:
        return "none"
    if _segments_and_query(u) is None:
        return "external"
    kind = classify_kind(u)
    if kind in ("comment", "reply"):
        return "comment"
    if kind != "unknown":
        return "content"
    segs, qs, path = _segments_and_query(strip_comment_params(u))
    if segs[:1] == ["groups"]:
        if len(segs) == 2:
            return "group"
        if len(segs) >= 4 and segs[2] == "user":
            return "profile"
        return "other"
    if segs[:1] == ["hashtag"]:
        return "hashtag"
    if path == "/profile.php":
        return "profile" if "id" in qs else "other"
    if len(segs) == 1 and segs[0] not in RESERVED:
        return "profile"  # user or page vanity URL
    if len(segs) == 2 and segs[0] == "people":
        return "profile"
    return "other"


def strip_comment_params(url: str | None) -> str | None:
    """Post URL from a comment URL (drop comment_id / reply_comment_id)."""
    u = clean_url(url)
    if not u:
        return None
    parts = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("comment_id", "reply_comment_id")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def group_segment(url: str | None) -> str | None:
    """'123456' or 'groupslug' from a group URL (any group page URL works)."""
    parsed = _segments_and_query(url)
    if not parsed:
        return None
    segs = [s for s in urlsplit(clean_url(url)).path.split("/") if s]  # keep original case
    if len(segs) >= 2 and segs[0].lower() == "groups":
        return segs[1]
    return None


def group_root(url: str | None) -> str | None:
    seg = group_segment(url)
    return f"{BASE}/groups/{seg}/" if seg else None


def build_search_url(keyword: str, group_url: str | None = None) -> str:
    q = quote(keyword, safe="")
    if group_url:
        seg = group_segment(group_url)
        if not seg:
            raise ValueError(f"Not a Facebook group URL: {group_url!r}")
        return f"{BASE}/groups/{quote(seg, safe='')}/search/?q={q}"
    return f"{BASE}/search/posts/?q={q}"


def build_marketplace_url(keyword: str, location: str | None = None) -> str:
    """Marketplace search, near the account's location or in `location` (a city name such as
    'karachi' or Facebook's numeric location id, as in /marketplace/<location>/search/)."""
    q = quote(keyword, safe="")
    loc = (location or "").strip().strip("/")
    return f"{BASE}/marketplace/{quote(loc, safe='')}/search/?query={q}" if loc else f"{BASE}/marketplace/search/?query={q}"


def pick_permalink(links: list[dict]) -> dict | None:
    """Best post permalink among a post's links (dicts with 'href').

    Falls back to a comment link with its comment params removed.
    Returns {'url', 'kind', 'index'} or None.
    """
    best = None
    best_rank = len(CONTENT_PRIORITY)
    for link in links:
        href = link.get("href")
        if link_role(href) != "content":
            continue
        kind = classify_kind(href)
        rank = CONTENT_PRIORITY.index(kind)
        if rank < best_rank:
            best, best_rank = {"url": clean_url(href), "kind": kind, "index": link.get("i")}, rank
    if best:
        return best
    for link in links:
        if link_role(link.get("href")) == "comment":
            base = strip_comment_params(link["href"])
            kind = classify_kind(base)
            return {"url": base, "kind": kind if kind != "unknown" else "post", "index": None}
    return None


def group_post_from_photo(photo_url: str | None, group_url: str | None) -> str | None:
    """Group post URL from a photo link inside a group post.

    Group photo links carry the post id in `set`: gm.<id> (single photo) or
    pcb.<id> (multi-photo post).
    """
    seg = group_segment(group_url)
    parsed = _segments_and_query(photo_url)
    if not seg or not parsed:
        return None
    m = re.fullmatch(r"(?:gm|pcb)\.(\d+)", parsed[1].get("set", ""))
    return f"{BASE}/groups/{seg}/posts/{m.group(1)}/" if m else None


# Accessibility text inside profile-picture links, not a name.
UI_LABELS = {"online status indicator", "active"}


def first_link(links: list[dict], role: str) -> dict | None:
    """First text link with this role (author/group names). Profile-picture links are skipped."""
    for link in links:
        name = (link.get("text") or "").strip().split("\n")[0].strip()
        if not name or link.get("img") or name.lower() in UI_LABELS:
            continue
        if link_role(link.get("href")) == role:
            return {"name": name[:200], "url": strip_comment_params(link["href"])}
    return None
