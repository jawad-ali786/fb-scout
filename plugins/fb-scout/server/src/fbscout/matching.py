"""Keyword verification. Facebook search is fuzzy, so every result is checked again here."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MODES = ("phrase", "all", "any")

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def normalize(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", text).strip()


def clean_keyword(keyword: str) -> str:
    return normalize(keyword).strip("\"'“”‘’ ")


def terms_for(keyword: str, mode: str = "phrase") -> list[str]:
    kw = clean_keyword(keyword)
    if mode == "phrase":
        return [kw] if kw else []
    return list(dict.fromkeys(w for w in kw.split(" ") if w))


# Between the words of a keyword: any characters that are not letters or digits,
# or none at all ("solar panel", "solar-panel", "Solar+Panel", "solar_panel",
# "solarpanel", "#solarpanel"). capture.py highlights the same way.
WORD_SEPARATOR = r"[\W_]*"


def _pattern(term: str) -> re.Pattern:
    # Alternating word / non-word chunks. Non-word chunks between two words become
    # WORD_SEPARATOR; leading/trailing ones stay literal ("#solar", "C++").
    parts = re.split(r"([\W_]+)", term)
    body = "".join(
        re.escape(p) if i % 2 == 0 or not (parts[i - 1] and i + 1 < len(parts) and parts[i + 1]) else WORD_SEPARATOR
        for i, p in enumerate(parts))
    # Word boundaries that also work for Urdu/Arabic script and hashtags.
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


@dataclass
class MatchResult:
    ok: bool
    matched_terms: list[str] = field(default_factory=list)
    snippet: str | None = None


def match_keyword(keyword: str, text: str | None, mode: str = "phrase", context: int = 70) -> MatchResult:
    if mode not in MODES:
        raise ValueError(f"match_mode must be one of {MODES}, got {mode!r}")
    terms = terms_for(keyword, mode)
    hay = normalize(text)
    if not terms or not hay:
        return MatchResult(False)

    found: list[str] = []
    first: re.Match | None = None
    for term in terms:
        m = _pattern(term).search(hay)
        if m:
            found.append(term)
            if first is None or m.start() < first.start():
                first = m

    ok = bool(found) if mode == "any" else len(found) == len(terms)
    if not ok or first is None:
        return MatchResult(False, found)
    start, end = max(0, first.start() - context), min(len(hay), first.end() + context)
    snippet = ("…" if start else "") + hay[start:end] + ("…" if end < len(hay) else "")
    return MatchResult(True, found, snippet)
