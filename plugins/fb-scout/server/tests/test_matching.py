import pytest

from fbscout.extract import clean_text
from fbscout.matching import clean_keyword, match_keyword, terms_for


def test_phrase_case_and_whitespace():
    m = match_keyword("Solar Panel", "Anyone know a good SOLAR\n  panel installer?")
    assert m.ok and m.matched_terms == ["Solar Panel"]
    assert "SOLAR panel" in m.snippet


@pytest.mark.parametrize("text", [
    "solar-panel cleaning", "LONGi 645W Solar+Panel New Arrival", "+LONGi+Hi-MO+10+645W+Solar+Panel+",
    "solar - panel", "solar_panel", "solar.panel", "solar/panel", "#solar_panel", "solar🌞panel", "solar...panel",
    "solarpanel", "#solarpanel cleaning tips", "SolarPanel", "big solarpanel",
])
def test_phrase_words_joined_by_special_characters_or_nothing(text):
    assert match_keyword("solar panel", text).ok


@pytest.mark.parametrize("text", ["solar panels", "solar paneling", "mysolarpanel", "#mysolarpanel", "solarpanels"])
def test_phrase_must_still_be_whole_words(text):
    assert not match_keyword("solar panel", text).ok


def test_special_characters_inside_the_keyword_are_flexible_too():
    assert match_keyword("solar-panel", "best solar panel price").ok
    assert match_keyword("Hi-MO 10", "LONGi HiMO? no: LONGi Hi MO 10").ok
    assert match_keyword("K-Electric", "k electric bill").ok


def test_symbols_at_the_ends_of_a_keyword_stay_literal():
    assert match_keyword("C++", "I write C++ code").ok
    assert not match_keyword("C++", "I write C code").ok
    assert match_keyword("#solar", "#solar power").ok
    assert not match_keyword("#solar", "solar power").ok


def test_phrase_respects_word_boundaries():
    assert not match_keyword("car", "I lost my scarf").ok
    assert match_keyword("solarpanel", "#solarpanel cleaning tips").ok
    assert not match_keyword("solarpanel", "solar panel").ok      # no way to know where to split


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
