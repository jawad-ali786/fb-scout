"""Turn Facebook's time strings into real dates.

Two sources, best first:
  time_exact  the timestamp's hover tooltip: "Monday 10 August 2026 at 14:15",
              "Sunday, September 28, 2026 at 4:12 PM"
  time_text   the short label next to the author: "3d", "5h", "Yesterday at 10:00",
              "September 28 at 4:12 PM"; relative to the record's captured_at.

Facebook shows times in the browser's timezone, so each run stores that UTC
offset (browser_utc_offset_minutes) and the parsed time carries it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
MONTHS = {name: i for i, name in enumerate(_MONTH_NAMES, 1)}
MONTHS.update({name[:3]: i for name, i in list(MONTHS.items())})
MONTHS["sept"] = 9

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Coarsest last; a finer value replaces a coarser one when records are merged.
PRECISIONS = ("minute", "hour", "day", "week", "month", "year")

_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})(?:\s*([ap])\.?\s?m\b\.?)?", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_DAY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\b")

_UNITS = {
    "s": "second", "sec": "second", "secs": "second", "second": "second", "seconds": "second",
    "m": "minute", "min": "minute", "mins": "minute", "minute": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
    "d": "day", "day": "day", "days": "day",
    "w": "week", "wk": "week", "wks": "week", "week": "week", "weeks": "week",
    "mo": "month", "mos": "month", "month": "month", "months": "month",
    "y": "year", "yr": "year", "yrs": "year", "year": "year", "years": "year",
}
_RELATIVE_RE = re.compile(r"^(\d+|an?|one)\s*([a-z]+)(?:\s+ago)?$")
_UNIT_DELTA = {"second": timedelta(seconds=1), "minute": timedelta(minutes=1), "hour": timedelta(hours=1),
               "day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30),
               "year": timedelta(days=365)}
_UNIT_PRECISION = {"second": "minute", "minute": "minute", "hour": "hour", "day": "day",
                   "week": "week", "month": "month", "year": "year"}


@dataclass
class ParsedTime:
    value: datetime          # timezone-aware, in the browser's timezone
    precision: str           # one of PRECISIONS
    source: str              # "time_exact" or "time_text"

    def as_fields(self) -> dict:
        v = self.value.replace(second=0, microsecond=0)
        return {
            "posted_at": v.isoformat(),
            "posted_date": v.date().isoformat(),
            "posted_at_precision": self.precision,
            "posted_at_source": self.source,
        }


EMPTY_FIELDS = {"posted_at": None, "posted_date": None, "posted_at_precision": None, "posted_at_source": None}


def _clean(text: str) -> str:
    text = text.replace(" ", " ").replace("\xa0", " ").replace(",", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _clock(m: re.Match) -> time | None:
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "p" else 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _split_clock(text: str) -> tuple[str, time | None, bool]:
    """Remove the clock time from the text. Returns (rest, time, had_a_time)."""
    m = _TIME_RE.search(text)
    if not m:
        return text, None, False
    return (text[:m.start()] + " " + text[m.end():]), _clock(m), True


def _month_in(text: str) -> tuple[int | None, str]:
    for word in re.findall(r"[a-z]+", text):
        if word in MONTHS:
            return MONTHS[word], re.sub(rf"\b{word}\b", " ", text, count=1)
    return None, text


def _calendar(text: str, default_year: int | None) -> tuple[date | None, time | None, bool]:
    """'28 september 2026 at 16:12' / 'september 28 at 4:12 pm' -> (date, time, has_year)."""
    rest, clock, had_time = _split_clock(text)
    if had_time and clock is None:
        return None, None, False
    month, rest = _month_in(rest)
    if month is None:
        return None, None, False
    year_m = _YEAR_RE.search(rest)
    year = int(year_m.group(1)) if year_m else default_year
    if year_m:
        rest = rest[:year_m.start()] + " " + rest[year_m.end():]
    day_m = _DAY_RE.search(rest)
    if not day_m or year is None:
        return None, None, False
    try:
        return date(year, month, int(day_m.group(1))), clock, bool(year_m)
    except ValueError:
        return None, None, False


def parse_exact(text: str | None, tz: timezone) -> ParsedTime | None:
    """The hover tooltip: always has day, month and year, usually a clock time."""
    if not text:
        return None
    d, clock, has_year = _calendar(_clean(text), None)
    if not d or not has_year:
        return None
    value = datetime.combine(d, clock or time(0, 0), tzinfo=tz)
    return ParsedTime(value, "minute" if clock else "day", "time_exact")


def parse_relative(text: str | None, now: datetime) -> ParsedTime | None:
    """The short label ("3d", "Yesterday at 10:00", "September 28 at 4:12 PM"), relative to `now` (aware)."""
    if not text:
        return None
    t = _clean(text)
    if len(t) > 60:
        return None
    if t in ("just now", "now", "a few seconds ago"):
        return ParsedTime(now, "minute", "time_text")

    m = _RELATIVE_RE.match(t)
    if m and m.group(2) in _UNITS:
        n = int(m.group(1)) if m.group(1).isdigit() else 1
        unit = _UNITS[m.group(2)]
        return ParsedTime(now - n * _UNIT_DELTA[unit], _UNIT_PRECISION[unit], "time_text")

    rest, clock, had_time = _split_clock(t)
    if had_time and clock is None:
        return None
    rest = re.sub(r"\bat\b", " ", rest).strip()
    if rest in ("yesterday", "today") or rest in WEEKDAYS:
        if rest == "today":
            d = now.date()
        elif rest == "yesterday":
            d = now.date() - timedelta(days=1)
        else:  # most recent past weekday ("Monday at 10:00")
            back = (now.weekday() - WEEKDAYS.index(rest)) % 7 or 7
            d = now.date() - timedelta(days=back)
        return ParsedTime(datetime.combine(d, clock or time(0, 0), tzinfo=now.tzinfo),
                          "minute" if clock else "day", "time_text")

    d, clock, has_year = _calendar(t, now.year)
    if not d:
        return None
    value = datetime.combine(d, clock or time(0, 0), tzinfo=now.tzinfo)
    if not has_year and value > now + timedelta(days=1):  # "December 30" seen in January
        try:
            value = value.replace(year=value.year - 1)
        except ValueError:  # 29 February
            value = value - timedelta(days=365)
    return ParsedTime(value, "minute" if clock else "day", "time_text")


def offset_tz(utc_offset_minutes: int | float | None) -> timezone:
    """Timezone for a run. Unknown (old runs) -> this computer's current offset."""
    if utc_offset_minutes is None:
        off = datetime.now().astimezone().utcoffset() or timedelta(0)
        return timezone(off)
    return timezone(timedelta(minutes=int(utc_offset_minutes)))


def parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve(time_exact: str | None, time_text: str | None, captured_at: str | None,
            utc_offset_minutes: int | float | None = None) -> dict:
    """posted_at / posted_date / posted_at_precision / posted_at_source for a record (all None if unknown)."""
    tz = offset_tz(utc_offset_minutes)
    parsed = parse_exact(time_exact, tz)
    if parsed is None:
        captured = parse_iso_utc(captured_at)
        if captured is not None:
            parsed = parse_relative(time_text, captured.astimezone(tz))
    return parsed.as_fields() if parsed else dict(EMPTY_FIELDS)


def finer(a: str | None, b: str | None) -> bool:
    """True if precision `a` is finer than `b` (None is the coarsest)."""
    rank = {p: i for i, p in enumerate(PRECISIONS)}
    return rank.get(a, len(PRECISIONS)) < rank.get(b, len(PRECISIONS))
