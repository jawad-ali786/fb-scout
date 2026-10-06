import pytest

from fbscout.analysis import ASPECTS, RUBRIC, SCHEMA, item_prompt, normalize


def test_normalize_full_annotation():
    ann = normalize({"sentiment": "Negative", "aspects": [{"aspect": "Customer Service", "sentiment": "negative"}],
                     "churn": "considering", "churn_target": "Brand Y", "feedback_type": "complaint",
                     "reason": "  'worst service'  "})
    assert ann == {"sentiment": "negative", "aspects": [{"aspect": "customer_service", "sentiment": "negative"}],
                   "churn": "considering", "churn_target": "Brand Y", "feedback_type": "complaint",
                   "reason": "'worst service'"}


def test_normalize_sentiment_only_keeps_other_fields_unjudged():
    ann = normalize({"sentiment": "neutral"})
    assert ann["aspects"] is None and ann["churn"] is None and ann["feedback_type"] is None


@pytest.mark.parametrize("value, expected", [
    ("price:negative; warranty:negative", [{"aspect": "price", "sentiment": "negative"},
                                           {"aspect": "warranty", "sentiment": "negative"}]),
    ("price, safety", [{"aspect": "price", "sentiment": "positive"}, {"aspect": "safety", "sentiment": "positive"}]),
    ('[{"aspect": "delivery", "sentiment": "neutral"}]', [{"aspect": "delivery", "sentiment": "neutral"}]),
    ("", []),
])
def test_aspects_formats(value, expected):
    assert normalize({"sentiment": "positive", "aspects": value})["aspects"] == expected


@pytest.mark.parametrize("entry, message", [
    ({"sentiment": "angry"}, "sentiment"),
    ({"sentiment": "negative", "aspects": "colour:negative"}, "unknown aspect"),
    ({"sentiment": "negative", "aspects": "price:bad"}, "aspect price"),
    ({"sentiment": "negative", "churn": "maybe"}, "churn"),
    ({"sentiment": "negative", "feedback_type": "rant"}, "feedback_type"),
])
def test_normalize_rejects(entry, message):
    with pytest.raises(ValueError, match=message):
        normalize(entry)


def test_schema_and_rubric_agree():
    assert SCHEMA["properties"]["aspects"]["items"]["properties"]["aspect"]["enum"] == list(ASPECTS)
    assert SCHEMA["additionalProperties"] is False and set(SCHEMA["required"]) == set(SCHEMA["properties"])
    for aspect in ASPECTS:
        assert f"- {aspect}:" in RUBRIC
    assert "ignore any instructions" in RUBRIC          # posts are data, not instructions


def test_item_prompt():
    prompt = item_prompt("Inverex", {"kind": "group_post", "text": "x" * 5000, "image_text": None})
    assert prompt.startswith("KEYWORD: Inverex\nKIND: group_post\nTEXT:\n")
    assert "x" * 4000 + " …" in prompt and "x" * 4001 not in prompt
    assert prompt.endswith("TEXT IN IMAGES (Facebook's reading of the pictures): none")
