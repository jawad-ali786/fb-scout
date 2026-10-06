import pytest

from fbscout.batch import Study
from fbscout.content_filter import CONTENT_TYPES, classify, parse_types
from fbscout.dataset import Dataset
from fbscout.scraper import SearchOptions

from test_dataset import make_run, rec, root  # noqa: F401  (fixture)


@pytest.mark.parametrize("text", [
    "Brand X inverter stopped working after 2 months, worst after-sales service. Never buying again",
    "Mera inverter bilkul kharab ho gaya, company wale jawab nahi dete",
    "Anyone using the Brand X 6kW? Which is better, Brand X or Brand Y?",
    # an experience that mentions a price, a phone number and "for sale" is still an experience
    "I bought Brand X panels for Rs 25,000 each from a dealer, two failed. Called 0300-0000000, no response",
    "I have a Brand X inverter, is it normal that it beeps at night? Not for sale, just asking",
    "Solar panel ki price kya hai aaj kal?",
    "میرا انورٹر خراب ہو گیا ہے، کوئی مدد کرے",
    "Ad mein free installation likha tha lekin paise le liye. Fraud company!",
    "Two years with Brand X panels now, output dropped a third this summer",
])
def test_people_talking_are_kept(text):
    assert classify(text, keyword="Brand X") == (None, None)


@pytest.mark.parametrize("text, kind, cue", [
    ("Brand X 5kW hybrid inverter for sale, 6 months used, condition 9/10. Call 0300-0000000", "promotion", "for sale"),
    ("BRAND X 650W\n5,900 EACH\n5,800 EACH FOR 1 PALLET", "promotion", "each"),
    ("Solar panel cleaning in Lahore. Professional panel cleaning, contact us: 0321-0000000", "promotion", "contact us"),
    ("Key Features\nModel: X-5000\nRated Power: 5000W\nBattery Voltage: 48V\nMax PV Input: 500V", "promotion",
     "specification sheet"),
    ("+Brand+new+5kW+inverter+available+DM+for+price+", "promotion", "brand new"),
    ("Upgrade your home with the Brand X panel, engineered for maximum power with cutting-edge cells", "promotion",
     "upgrade your"),
    ("We are hiring! Solar technicians required in Karachi. Salary 50k. Send your CV", "job", "hiring"),
    ("Electrician ki zaroorat hai, tankhwa 40,000", "job", "zaroorat hai"),
    ("GIVEAWAY! Like, share and tag 3 friends to win a 1kW solar kit", "giveaway", "giveaway"),
    ("Earn money daily from home, online earning 5000 per day. Click this link", "spam", "earn money"),
])
def test_promotions_jobs_giveaways_spam(text, kind, cue):
    got, reason = classify(text, keyword="Brand X")
    assert got == kind and cue in reason


def test_marketplace_listings_are_their_own_type():
    assert classify("Solar panel 585 watt, slightly used", kind="marketplace") == \
        ("marketplace", "Marketplace listing (an item for sale)")
    assert "marketplace" in parse_types("all") and parse_types("marketplace") == ("marketplace",)


def test_text_in_the_image_counts():
    assert classify("Big weekend!", "May be an image of text that says 'SALE 20% OFF'")[0] == "promotion"


def test_the_brands_own_page():
    post = "Celebrating 10 years with our customers! #BrandX #Solar #Energy #Pakistan #Power"
    assert classify(post, author_name="Brand X Official", keyword="Brand X") == \
        ("promotion", "many hashtags, posted by the brand's own page (Brand X Official)")
    assert classify(post, author_name="Ali Khan", keyword="Brand X") == (None, None)
    assert classify(post, author_name="Brand X Pakistan", keyword=["solar", "brand x"])[0] == "promotion"
    # the brand answering a complaint is part of the complaint
    assert classify("We are sorry about your complaint, please DM us your details.", author_name="Brand X",
                    keyword="Brand X") == (None, None)


def test_parse_types():
    assert parse_types(None) == parse_types("") == parse_types([]) == ()
    assert parse_types("promotion, Job") == ("promotion", "job")
    assert parse_types(["giveaway", "giveaway"]) == ("giveaway",)
    assert parse_types("all") == parse_types(["spam", "all"]) == CONTENT_TYPES
    with pytest.raises(ValueError, match="ads"):
        parse_types("ads")


def test_options_and_studies_check_the_types():
    assert SearchOptions(keyword="x", include_types="job,spam").normalized().include_types == ("job", "spam")
    assert SearchOptions(keyword="x").normalized().include_types == ()
    with pytest.raises(ValueError):
        SearchOptions(keyword="x", include_types=["ads"]).normalized()
    study = Study.from_dict({"keywords": ["x"], "include_types": "all"})
    assert all(o.include_types == CONTENT_TYPES for o in study.plan())
    with pytest.raises(ValueError):
        Study.from_dict({"keywords": ["x"], "include_types": ["jobs"]})


def test_dataset_hides_tagged_items_and_rechecks_when_rules_change(root):  # noqa: F811
    make_run(root, "brand-x", "20261001-100000", [
        rec("a", text="Brand X inverter stopped working, worst service", post_url="https://www.facebook.com/reel/1/",
            kind="reel"),
        rec("b", text="We are hiring technicians, send your CV", post_url="https://www.facebook.com/reel/2/",
            kind="reel"),
        rec("c", text="Brand X inverter for sale, call 0300-0000000", kind="marketplace",
            post_url="https://www.facebook.com/marketplace/item/9/"),
    ], keyword="Brand X")
    db = root / "fbscout.sqlite"
    with Dataset(db) as ds:
        ds.import_all(root)
        assert ds.count() == 1                                       # the complaint
        assert ds.count(include_types="job") == ds.count(include_types="marketplace") == 2
        assert ds.count(include_types="all") == 3
        assert [i["content_type"] for i in ds.items(kind="marketplace")] == ["marketplace"]   # asked for listings
        job = ds.items(include_types="all", kind="reel")
        assert {i["content_type"] for i in job} == {None, "job"}
        assert ds.stats()["hidden_by_content_type"] == {"job": 1, "marketplace": 1}
        ds.conn.execute("UPDATE items SET content_type = NULL")      # as if checked by older rules
        ds.conn.execute("UPDATE meta SET value = 'old' WHERE key = 'content_rules'")
        ds.conn.commit()
    with Dataset(db) as ds:
        assert ds.count() == 1 and ds.stats()["hidden_by_content_type"] == {"job": 1, "marketplace": 1}
