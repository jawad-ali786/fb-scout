from datetime import datetime, timedelta, timezone

import pytest

from fbscout.dates import finer, parse_exact, parse_relative, resolve

PKT = timezone(timedelta(hours=5))
NOW = datetime(2026, 10, 6, 12, 14, tzinfo=PKT)   # a Tuesday


@pytest.mark.parametrize("text, expected, precision", [
    ("Monday 10 August 2026 at 14:15", datetime(2026, 8, 10, 14, 15), "minute"),
    ("Sunday, September 28, 2026 at 4:12 PM", datetime(2026, 9, 28, 16, 12), "minute"),
    ("Thursday, 3 September 2026 at 07:14", datetime(2026, 9, 3, 7, 14), "minute"),
    ("September 28, 2026 at 12:05 am", datetime(2026, 9, 28, 0, 5), "minute"),
    ("28 September 2025", datetime(2025, 9, 28), "day"),
    ("Saturday 3 October 2026 at 17:51", datetime(2026, 10, 3, 17, 51), "minute"),
])
def test_parse_exact(text, expected, precision):
    p = parse_exact(text, PKT)
    assert p.value == expected.replace(tzinfo=PKT)
    assert p.precision == precision and p.source == "time_exact"


@pytest.mark.parametrize("text", [None, "", "3d", "May be an image of text", "September 28 at 4:12 PM",
                                  "31 February 2026", "Monday at 25:00"])
def test_parse_exact_rejects(text):
    assert parse_exact(text, PKT) is None


@pytest.mark.parametrize("text, expected, precision", [
    ("Just now", NOW, "minute"),
    ("12m", NOW - timedelta(minutes=12), "minute"),
    ("5h", NOW - timedelta(hours=5), "hour"),
    ("2 hours ago", NOW - timedelta(hours=2), "hour"),
    ("an hour ago", NOW - timedelta(hours=1), "hour"),
    ("3d", NOW - timedelta(days=3), "day"),
    ("2w", NOW - timedelta(weeks=2), "week"),
    ("1y", NOW - timedelta(days=365), "year"),
    ("Yesterday at 10:00", datetime(2026, 10, 5, 10, 0, tzinfo=PKT), "minute"),
    ("Yesterday", datetime(2026, 10, 5, 0, 0, tzinfo=PKT), "day"),
    ("Saturday at 9:30 PM", datetime(2026, 10, 3, 21, 30, tzinfo=PKT), "minute"),
    ("Tuesday at 08:00", datetime(2026, 9, 29, 8, 0, tzinfo=PKT), "minute"),      # a week ago, not today
    ("September 28 at 4:12 PM", datetime(2026, 9, 28, 16, 12, tzinfo=PKT), "minute"),
    ("28 September at 16:12", datetime(2026, 9, 28, 16, 12, tzinfo=PKT), "minute"),
    ("December 30", datetime(2025, 12, 30, tzinfo=PKT), "day"),                    # no year: last year
    ("March 3, 2024", datetime(2024, 3, 3, tzinfo=PKT), "day"),
])
def test_parse_relative(text, expected, precision):
    p = parse_relative(text, NOW)
    assert p.value == expected and p.precision == precision and p.source == "time_text"


@pytest.mark.parametrize("text", [None, "", "May be an image of text", "Reel", "Sponsored", "5 apples"])
def test_parse_relative_rejects(text):
    assert parse_relative(text, NOW) is None


def test_resolve_prefers_tooltip_and_uses_run_offset():
    r = resolve("Sunday, September 28, 2026 at 4:12 PM", "3d", "2026-10-01T10:30:41Z", 300)
    assert r == {"posted_at": "2026-09-28T16:12:00+05:00", "posted_date": "2026-09-28",
                 "posted_at_precision": "minute", "posted_at_source": "time_exact"}


def test_resolve_relative_to_capture_time_in_browser_timezone():
    # 21:30 UTC on 1 Oct is already 2 Oct in Pakistan; "1d" is then 1 Oct there.
    r = resolve(None, "1d", "2026-10-01T21:30:00Z", 300)
    assert r["posted_at"] == "2026-10-01T02:30:00+05:00" and r["posted_date"] == "2026-10-01"
    assert r["posted_at_precision"] == "day" and r["posted_at_source"] == "time_text"


def test_resolve_unknown():
    assert resolve(None, None, "2026-10-01T10:00:00Z", 0)["posted_at"] is None
    assert resolve(None, "3d", None, 0)["posted_at"] is None


def test_finer():
    assert finer("minute", "day") and finer("day", None)
    assert not finer("day", "minute") and not finer(None, None) and not finer("hour", "hour")
