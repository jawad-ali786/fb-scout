import pytest

from fbscout.extract import clean_text, group_name_from_title, image_text


@pytest.mark.parametrize("title, name", [
    ("Solar Panel & Inverter & Battery | 610w AVCON Bi-facial Solar Panel | Facebook", "Solar Panel & Inverter & Battery"),
    ("(3) Solar Users Pakistan | Facebook", "Solar Users Pakistan"),
    ("(20+) Search results | Solar Users Pakistan | Facebook", "Solar Users Pakistan"),
    ("Facebook", None),
    ("Search | Facebook", None),
    ("", None),
    (None, None),
])
def test_group_name_from_title(title, name):
    assert group_name_from_title(title) == name


def test_image_text_keeps_descriptions_only():
    assert image_text(["May be an image of text", "May be an image of text", "No photo description available."]) \
        == "May be an image of text"
    assert image_text([]) is None


def test_clean_text_drops_buttons_and_filler():
    assert clean_text("Facebook\nSolar panel sale… See more") == "Solar panel sale…"
