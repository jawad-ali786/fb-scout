import pytest

from fbscout.lang import detect_language


@pytest.mark.parametrize("text, code", [
    ("We offer professional solar panel cleaning services in Karachi. Dirty panels can reduce efficiency.", "en"),
    ("Anyone know a good solar panel installer near Lahore?", "en"),
    ("Solar panels ko regular cleaning aur basic condition checks ki zaroorat hai", "ur-Latn"),
    ("Premium quality LONGi 645W Solar Panel ab EPS par available hai.", "ur-Latn"),
    ("Mera solar panel kaam nahi kar raha, koi acha technician batayein", "ur-Latn"),
    ("سولر پینل کی قیمت کیا ہے؟ مجھے بتائیں", "ur"),
    ("ما هو سعر الألواح الشمسية", "ar"),
    ("सोलर पैनल की कीमत क्या है", "hi"),
    ("Small solar set up, lang mga boss. Pm po kung interested", "tl"),
    ("Ptpa: solar 5kw 30k only. Mga kaya paganahin. Minimum panel need 3pcs, battery much better", "tl"),
    ("#SolarEnergy #Solarsystem #Solarcleaning", "und"),
    ("645W", "und"),
    ("", "und"),
    (None, "und"),
])
def test_detect_language(text, code):
    assert detect_language(text) == code


def test_english_with_one_urdu_word_stays_english():
    assert detect_language("Thanks for the info, this is the best price hai") == "en"
