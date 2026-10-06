"""Content that isn't people talking about the keyword: promotions, job posts, giveaways, spam.

Searches leave these out by default; each type can be switched back on (include_types). The
rules are simple and reproducible (English, Roman Urdu and Urdu cues, scored; THRESHOLD points
make a type), and every decision comes with its reason so it can be checked. Someone describing
their own experience or problem ("my inverter", "is it normal?", "worst service") outweighs
selling cues, so a complaint that mentions a price or a phone number is kept.

Extend the cue lists below when you see posts in the wrong place.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

CONTENT_TYPES = ("promotion", "job", "giveaway", "spam")
DESCRIPTIONS = {
    "promotion": "ads, items or services for sale, price lists, brand pages' own posts and announcements",
    "job": "job offers and hiring posts",
    "giveaway": "contests, lucky draws, 'tag 3 friends'",
    "spam": "earn-money, forex/crypto signals, loan offers, follow-for-follow",
}
THRESHOLD = 3
LOGIC_VERSION = 1   # raise when classify() changes; rule edits are picked up by RULES_VERSION by themselves

# (pattern, points[, how many different matches count]), matched on lower-cased text; by default once.
PROMOTION = [
    # selling
    (r"\bfor\s+sale\b|\bfor\s+sell\b|\bselling\b|\bbechna\b|\bbaichna\b|\bsale\s+(?:karna|purchase)\b|\bxả\s+kho\b", 3),
    (r"\b(?:order|buy|book|call|shop)\s+(?:now|today|karein|karen)\b|\bpayment\s+on\s+delivery\b", 2),
    (r"\b(?:dm|inbox|message)\s+(?:us|me)\b|\b(?:dm|inbox)\s+(?:for|to)\b|\binbox\s+(?:karein|karen|kren)\b|\bwrite\s*:", 2),
    (r"\bdiscount\b|\b\d{1,2}\s?%\s?off\b|\boffers?\b|\bdeals?\b|\bpromo\b|\bclearance\b|\bsale\b", 2, 2),
    (r"\bfree\s+(?:delivery|installation|consultation|shipping|cod)\b|\bcash\s+on\s+delivery\b|\bcod\b|"
     r"\bdelivery\s+(?:all\s+over|available)\b|\bfast\s+delivery\b|\bready\s+for\s+shipment\b", 2),
    (r"\bcontact\s+(?:us|now|for|no|number)\b|\bfor\s+(?:details|more\s+info|booking|orders?|price)\b|"
     r"\brabta\s+(?:karein|karen|kren)\b|\bcell\s*(?:no|#|number)\b|\bcall\s+or\s+whatsapp\b", 2),
    # stock, trade, used items
    (r"\bin\s+stock\b|\bready\s+stock\b|\bnew\s+stock\b|\bon\s+(?:rotterdam\s+)?stock\b|\bwholesale\b|\bdirect\s+supplier\b|"
     r"\bdastiyab\b|\bdabba\s+pack\b|\bbox\s+pack(?:ed)?\b|\bsealed\s+pack\b", 2),
    (r"\b\d+\s?pcs\b|\bpallets?\b|\bmoq\b|\bfob\b|\bcif\b|\bex[-\s](?:karachi|lahore|warehouse)\b|\bcontainer\s+(?:rates?|available)\b", 2),
    (r"\b\d[\d,.]*\s?(?:-\s?\d[\d,.]*\s?)?each\b|\bper\s+(?:watt|piece|pc|unit)\b", 2, 2),   # price lists: per line
    (r"\brates?\s+(?:list|updates?)\b|\btoday'?s?\s+(?:\w+\s+){0,3}(?:rates?|prices?)\b", 2),
    (r"\b(?:good|perfect|excellent|new)\s+condition\b|\bcondition\s+\d+\s*/\s*10\b|\blike\s+new\b|\bcollection\s+only\b|"
     r"\b\d+\s?(?:weeks?|weaks|months?|years?)\s+(?:use|used)\b|\bused\s+for\s+\d+\b|\bfinal\s+(?:price|rate|\d)", 2, 2),
    (r"\bavailable\b|\bbrand\s+new\b|\bbest\s+(?:price|rates?|quality)\b|\blowest\s+price\b|\blimited\s+stock\b|\blocation\s*:|"
     r"\bcheap(?:est)?\b|\baffordable\b", 1),
    # services and businesses
    (r"\b(?:professional|expert)\s+(?:\w+\s+){0,3}(?:service|services|cleaning|washing|installation)\b|"
     r"\bservices?\s+(?:available|provider)\b|\bwe\s+(?:offer|provide|deal\s+in|sell|install)\b|"
     r"\bour\s+(?:shop|store|showroom|services?|products?|team)\b|\bvisit\s+(?:us|our)\b|\bfree\s+consultation\b", 2),
    # brand marketing and PR
    (r"\bintroducing\b|\bmeet\s+the\b|\bproud\s+to\s+(?:share|announce|be)\b|\bwe\s+are\s+proud\b|\bofficially\b|"
     r"\bnow\s+available\b|\bjoin\s+us\b|\bexpo\b|\bexhibition\b|\bhighlights\b|\bpartnership\b|\bofficial\s+partner\b|"
     r"\baward(?:ed)?\b|\btier\s*(?:#\s*)?1\b|\bworld\s+records?\b|\bbenchmark\b|\blaunch(?:ed|es)?\b", 2),
    # advertising copy: a point for each different phrase, up to three
    (r"\bcomes\s+with\b|\b(?:perfect|ideal|suitable)\s+for\b|\bdesigned\s+(?:to|for)\b|\bengineered\b|\bhigh[-\s]efficiency\b|"
     r"\bupgrade\s+your\b|\bpower\s+up\b|\bkeep\s+your\b|\bdon'?t\s+let\b|\bsay\s+goodbye\b|\bexperience\s+the\b|"
     r"\benjoy\s+(?:uninterrupted|clean|reliable|smooth)\b|\binvest\s+in\b|\bkey\s+(?:specifications|features)\b|"
     r"\bfeaturing\b|\bcutting[-\s]edge\b|\bstate[-\s]of[-\s]the[-\s]art\b|\bnext[-\s]gen\b|\bboost\s+your\b|\bdurable\b|"
     r"\blong[-\s]lasting\b|\bpremium\s+quality\b|\bmaximum\s+(?:power|savings|efficiency|performance)\b|\bfor\s+modern\s+homes\b", 1, 3),
]
JOB = [
    (r"\b(?:we\s+are\s+)?hiring\b|\bvacanc(?:y|ies)\b|\bjob\s+(?:opportunit(?:y|ies)|opening|vacancy|offer)\b|\bnaukri\b|\bnew\s+hires\b", 3),
    (r"\bapply\s+now\b|\bsend\s+(?:your\s+)?(?:cv|resume)\b|\binternship\b|\bwalk[-\s]?in\s+interview\b", 3),
    (r"\b(?:staff|technicians?|salesman|salesmen|drivers?|helpers?|engineers?|electricians?)\s+(?:required|needed|wanted)\b", 3),
    (r"\b(?:ki|ke)\s+zaroorat\s+hai\b", 2),
    (r"\bsalary\b|\btankhwa\b|\bcandidates?\b|\bexperience\s+required\b|\bjobs\b", 1),
]
GIVEAWAY = [
    (r"\bgive\s?away\b|\blucky\s+draw\b|\bcontest\b|\bwinners?\s+will\s+be\b|\bwin\s+(?:a|an|amazing|exciting)\b", 3),
    (r"\btag\s+(?:\d+|your|three)\s+friends?\b|\blike,?\s+share\s+(?:and|&)\b|\bparticipate\b|\binaam\b|"
     r"\blucky\s+(?:fans?|winners?)\b|\bshortlisted\b", 2),
]
SPAM = [
    (r"\bearn\s+(?:money|daily|\$|rs)\b|\bonline\s+earning\b|\bearn\s+from\s+home\b|\bwork\s+from\s+home\b", 3),
    (r"\bforex\b|\bcrypto\s+(?:signals?|trading)\b|\bbinary\s+options?\b|\binvestment\s+plan\b|\bdaily\s+profit\b", 3),
    (r"\bclick\s+(?:the|this)\s+link\b|\bloan\s+approved\b|\bfollow\s+for\s+follow\b", 2),
]
# Someone describing their own experience, problem or question: keeps a post in.
OPINION = re.compile(
    r"\b(?:disappointed|worst|terrible|horrible|pathetic|complaint|complain|fraud|scam|refund|cheated|faulty|"
    r"defective|useless|bekar|ghatiya|kharab|dhoka|shikayat|never\s+(?:buy|again)|beware|not\s+working|"
    r"stopped\s+working|loot|waste\s+of)\b"
    r"|\b(?:i|we)\s+(?:bought|purchased|installed|have\s+been\s+using|am\s+using|faced|got|have\s+(?:an?|the|my))\b"
    r"|\bmy\s+(?:\w+\s+){0,3}(?:inverter|panels?|system|unit|battery|ups|order|experience)\b"
    r"|\b(?:mera|meri|mere|hamara|hamari)\b"
    r"|\banyone\s+(?:know|using|used|tried|faced|facing)\b|\bwhich\s+(?:is|one)\s+better\b|\bany\s+(?:suggestions?|advice)\b"
    r"|\bis\s+it\s+normal\b|\b(?:pls|plz|please)\s+guide\b|\bguide\s+me\b|\bkoi\s+(?:bata|batay)"
    r"|شکایت|خراب|بیکار")

_PHONE = re.compile(r"(?:\+?92[\s-]?|\b0)3\d{2}[\s.-]?\d{7}\b|\+\d[\d\s-]{8,15}\d|\b0\d{9,11}\b|\bwhats\s?app\b|\bwa\.me/")
_PRICE = re.compile(r"\b(?:rs\.?|pkr|price|qeemat|rate|demand)\s*[:\-]?\s*\d|\b\d[\d,]*\s?(?:/-|rs\b|pkr\b|k\b|naira\b|peso)"
                    r"|[$€£₦₱₹]\s?\d|\b\d[\d,.]*\s?(?:usd|euro|aed)\b|\((?:euro|usd)\)")
_URL = re.compile(r"www\.|https?://|\.com\b|\.pk\b")

_GROUPS = (("promotion", PROMOTION), ("job", JOB), ("giveaway", GIVEAWAY), ("spam", SPAM))
_COMPILED = {name: [(re.compile(rule[0], re.IGNORECASE), rule[1], rule[2] if len(rule) > 2 else 1) for rule in rules]
             for name, rules in _GROUPS}
_SPEC_LINE = re.compile(r"^\s*[*•-]?\s*[a-z][a-z ()/.]{1,28}:\s*\S", re.MULTILINE)   # "Rated Power: 3000W"

# The dataset re-checks its items when this changes.
RULES_VERSION = f"{LOGIC_VERSION}-" + hashlib.sha1(repr((
    _GROUPS, OPINION.pattern, _PHONE.pattern, _PRICE.pattern, _URL.pattern, _SPEC_LINE.pattern, THRESHOLD,
)).encode("utf-8")).hexdigest()[:10]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"(?<=\w)\+(?=\w)", " ", text)          # "brand+new+inverter" copy-paste artefact
    text = re.sub(r"[‐-―‑]", "-", text)    # unicode hyphens
    return re.sub(r"\s+", " ", text)


def _letters(text: str | None) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower())


def classify(text: str | None, image_text: str | None = None, author_name: str | None = None,
             keyword: str | Iterable[str] | None = None) -> tuple[str | None, str | None]:
    """(content type, reason), or (None, None) for ordinary posts. `keyword`: the keyword(s) the post was
    found for; a page or account named after one (the brand's own page) is a selling cue."""
    raw = unicodedata.normalize("NFKC", f"{text or ''}\n{image_text or ''}").lower()
    hay = _norm(raw)
    if not hay.strip():
        return None, None
    scores: dict[str, int] = {}
    cues: dict[str, list[str]] = {}

    def add(name: str, points: int, cue: str) -> None:
        scores[name] = scores.get(name, 0) + points
        cues.setdefault(name, []).append(cue)

    for name, rules in _COMPILED.items():
        for rx, pts, most in rules:
            for cue in list(dict.fromkeys(m.group(0).strip() for m in rx.finditer(hay)))[:most]:
                add(name, pts, cue)
    if _PHONE.search(hay):
        add("promotion", 2, "phone/WhatsApp number")
    if _PRICE.search(hay):
        add("promotion", 1, "price")
    if _URL.search(hay):
        add("promotion", 1, "link")
    if len(_SPEC_LINE.findall(raw)) >= 4:
        add("promotion", 2, "specification sheet")
    if hay.count("#") >= 5:
        add("promotion", 1, "many hashtags")
    author = _letters(author_name)
    keywords = [keyword] if isinstance(keyword, str) else list(keyword or [])
    if author and any(len(_letters(k)) >= 3 and _letters(k) in author for k in keywords):
        add("promotion", 3, f"posted by the brand's own page ({author_name})")
    if OPINION.search(hay):   # someone with an experience or a question: selling cues count much less
        scores = {k: v - 4 for k, v in scores.items()}
    best = [k for k in ("job", "giveaway", "spam", "promotion") if scores.get(k, 0) >= THRESHOLD]
    if not best:
        return None, None
    name = max(best, key=lambda k: scores[k])
    return name, ", ".join(dict.fromkeys(cues[name]))[:200]


def parse_types(value) -> tuple[str, ...]:
    """include_types from the CLI/MCP: a list, a comma-separated string, or 'all'."""
    if not value:
        return ()
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    items = [str(v).strip().lower() for v in items if str(v).strip()]
    if "all" in items:
        return CONTENT_TYPES
    bad = [v for v in items if v not in CONTENT_TYPES]
    if bad:
        raise ValueError(f"unknown content type(s) {', '.join(bad)}; use {', '.join(CONTENT_TYPES)} or all")
    return tuple(dict.fromkeys(items))
