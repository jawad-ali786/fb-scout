import pytest

from fbscout.extract import clean_text
from fbscout.matching import clean_keyword, match_keyword, terms_for


def test_phrase_case_and_whitespace():
    m = match_keyword("Solar Panel", "Anyone know a good SOLAR\n  panel installer?")
    assert m.ok and m.matched_terms == ["Solar Panel"]
    assert "SOLAR panel" in m.snippet


def test_phrase_respects_word_boundaries():
    assert not match_keyword("car", "I lost my scarf").ok
    assert not match_keyword("solar panel", "#solarpanel cleaning tips").ok
    assert match_keyword("solarpanel", "#solarpanel cleaning tips").ok


def test_all_and_any_modes():
    text = "panels for solar energy"
    assert not match_keyword("solar panel", text, "phrase").ok
    assert not match_keyword("solar panel", text, "all").ok  # "panels" != "panel"
    assert match_keyword("solar energy", "energy from the solar roof", "all").ok
    m = match_keyword("solar wind", text, "any")
    assert m.ok and m.matched_terms == ["solar"]


def test_unicode_keywords():
    assert match_keyword("کراچی", "میں کراچی میں رہتا ہوں").ok
    assert match_keyword("café", "Best café in town").ok        # decomposed accent (NFKC)
    assert match_keyword("brand x", "Brand​ X is great").ok      # zero-width space removed


def test_snippet_ellipsis():
    text = "a" * 200 + " keyword " + "b" * 200
    m = match_keyword("keyword", text, context=10)
    assert m.snippet.startswith("…") and m.snippet.endswith("…") and "keyword" in m.snippet


def test_clean_keyword_and_terms():
    assert clean_keyword('  "Solar  Panel" ') == "Solar Panel"
    assert terms_for("solar solar panel", "all") == ["solar", "panel"]
    assert terms_for("", "phrase") == []


def test_bad_mode():
    with pytest.raises(ValueError):
        match_keyword("x", "x", "fuzzy")


def test_empty_text():
    assert not match_keyword("x", None).ok


@pytest.mark.parametrize("raw, expected", [
    ("#SolarPakistan #TikTokPakistan See less", "#SolarPakistan #TikTokPakistan"),
    ("Clean Panels = Better Solar Performance\nT&C Apply See less See less", "Clean Panels = Better Solar Performance\nT&C Apply"),
    ("Anyone know an installer? Thanks!\nSee less", "Anyone know an installer? Thanks!"),
    ("Short text… See more", "Short text…"),
    ("I want to see more solar panels", "I want to see more solar panels"),     # lowercase is real text
    ("See less wiring, more panels: here's how", "See less wiring, more panels: here's how"),
    ("Facebook\nSolar Panel Cleaning Service in Karachi", "Solar Panel Cleaning Service in Karachi"),   # hidden filler
    ("Follow us on Facebook for rates", "Follow us on Facebook for rates"),
])
def test_clean_text_drops_button_labels(raw, expected):
    assert clean_text(raw) == expected
