import pytest

from fbscout.urls import (
    build_search_url, classify_kind, clean_url, first_link, group_post_from_photo, group_root, group_segment, link_role,
    pick_permalink, strip_comment_params,
)


@pytest.mark.parametrize("raw, expected", [
    ("https://www.facebook.com/groups/123/posts/456/?__cft__[0]=AZX&__tn__=%2CO%2CP-R",
     "https://www.facebook.com/groups/123/posts/456/"),
    ("https://m.facebook.com/groups/123/posts/456", "https://www.facebook.com/groups/123/posts/456/"),
    ("https://web.facebook.com/permalink.php?story_fbid=99&id=7&ref=share",
     "https://www.facebook.com/permalink.php?id=7&story_fbid=99"),
    ("https://www.facebook.com/watch/?v=555&mibextid=abc", "https://www.facebook.com/watch/?v=555"),
    ("https://www.facebook.com/groups/284821339354057/?multi_permalinks=1839971230505719&__cft__[0]=AZ",
     "https://www.facebook.com/groups/284821339354057/posts/1839971230505719/"),
    ("https://www.facebook.com/groups/123/?multi_permalinks=456,789", "https://www.facebook.com/groups/123/?multi_permalinks=456%2C789"),
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&h=AT0",
     "https://example.com/a?b=1"),
    ("https://www.facebook.com/x/posts/pfbid02abc?comment_id=11&reply_comment_id=22&__cft__=q",
     "https://www.facebook.com/x/posts/pfbid02abc/?comment_id=11&reply_comment_id=22"),
    ("#", None),
    ("javascript:void(0)", None),
    (None, None),
])
def test_clean_url(raw, expected):
    assert clean_url(raw) == expected


@pytest.mark.parametrize("url, kind", [
    ("https://www.facebook.com/groups/123/posts/456/", "group_post"),
    ("https://www.facebook.com/groups/123/permalink/456/", "group_post"),
    ("https://www.facebook.com/groups/123/?multi_permalinks=456", "group_post"),
    ("https://www.facebook.com/SolarCo/posts/pfbid02abc", "post"),
    ("https://www.facebook.com/permalink.php?story_fbid=1&id=2", "post"),
    ("https://www.facebook.com/reel/987654321", "reel"),
    ("https://www.facebook.com/watch/?v=1", "video"),
    ("https://www.facebook.com/SolarCo/videos/123/", "video"),
    ("https://www.facebook.com/photo/?fbid=1&set=a.2", "photo"),
    ("https://www.facebook.com/events/42/", "event"),
    ("https://www.facebook.com/marketplace/item/77/", "marketplace"),
    ("https://www.facebook.com/share/p/AbCd/", "post"),
    ("https://www.facebook.com/groups/123/posts/456/?comment_id=9", "comment"),
    ("https://www.facebook.com/groups/123/posts/456/?comment_id=9&reply_comment_id=10", "reply"),
    # commenter's profile link carries comment_id but is not a comment permalink
    ("https://www.facebook.com/profile.php?id=100&comment_id=9", "unknown"),
    ("https://www.facebook.com/SolarCo/photos/a.1/2/", "photo"),
    ("https://www.facebook.com/groups/123/", "unknown"),
    ("https://www.facebook.com/search/posts/?q=x", "unknown"),
    ("https://www.facebook.com/search/videos/?q=x", "unknown"),
    ("https://www.facebook.com/SolarCo/posts/", "unknown"),
    ("https://example.com/posts/1", "unknown"),
])
def test_classify_kind(url, kind):
    assert classify_kind(url) == kind


@pytest.mark.parametrize("url, role", [
    ("https://www.facebook.com/groups/123/?__cft__=1", "group"),
    ("https://www.facebook.com/groups/123/user/456/", "profile"),
    ("https://www.facebook.com/ali.khan", "profile"),
    ("https://www.facebook.com/profile.php?id=100&comment_id=9", "profile"),
    ("https://www.facebook.com/hashtag/solar", "hashtag"),
    ("https://www.facebook.com/groups/123/posts/456/", "content"),
    ("https://www.facebook.com/groups/123/posts/456/?comment_id=9", "comment"),
    ("https://www.facebook.com/search/posts/?q=x", "other"),
    ("https://example.com", "external"),
    (None, "none"),
])
def test_link_role(url, role):
    assert link_role(url) == role


def test_pick_permalink_prefers_post_over_photo():
    links = [
        {"i": 0, "href": "https://www.facebook.com/ali.khan", "text": "Ali"},
        {"i": 1, "href": "https://www.facebook.com/photo/?fbid=1", "text": ""},
        {"i": 2, "href": "https://www.facebook.com/groups/1/posts/2/?__cft__=x", "text": "3d"},
    ]
    assert pick_permalink(links) == {"url": "https://www.facebook.com/groups/1/posts/2/", "kind": "group_post", "index": 2}


def test_pick_permalink_falls_back_to_comment_link():
    links = [{"i": 0, "href": "https://www.facebook.com/x/posts/9/?comment_id=5", "text": "1h"}]
    assert pick_permalink(links)["url"] == "https://www.facebook.com/x/posts/9/"


def test_pick_permalink_none():
    assert pick_permalink([{"i": 0, "href": None, "text": "x"}]) is None


def test_first_link_skips_empty_text():
    links = [
        {"href": "https://www.facebook.com/groups/1/", "text": ""},
        {"href": "https://www.facebook.com/groups/1/", "text": "Solar Group\nPublic"},
    ]
    assert first_link(links, "group") == {"name": "Solar Group", "url": "https://www.facebook.com/groups/1/"}


def test_first_link_skips_profile_picture_links():
    links = [
        {"href": "https://www.facebook.com/SolarWalay", "text": "Online status indicator\nActive"},   # avatar label
        {"href": "https://www.facebook.com/SolarWalay", "text": "SolarWalay", "img": True},           # avatar with image
        {"href": "https://www.facebook.com/SolarWalay", "text": "SolarWalay"},
    ]
    assert first_link(links, "profile") == {"name": "SolarWalay", "url": "https://www.facebook.com/SolarWalay/"}


def test_group_helpers_and_search_url():
    assert group_segment("https://www.facebook.com/groups/SolarPK/posts/1") == "SolarPK"
    assert group_root("https://m.facebook.com/groups/123?ref=x") == "https://www.facebook.com/groups/123/"
    assert group_segment("https://www.facebook.com/SolarCo") is None
    assert build_search_url("solar panel") == "https://www.facebook.com/search/posts/?q=solar%20panel"
    assert build_search_url("solar & co", "https://www.facebook.com/groups/123/") == \
        "https://www.facebook.com/groups/123/search/?q=solar%20%26%20co"
    with pytest.raises(ValueError):
        build_search_url("x", "https://www.facebook.com/SolarCo")


@pytest.mark.parametrize("photo, group, expected", [
    ("https://www.facebook.com/photo/?fbid=1&set=gm.456&__cft__[0]=x", "https://www.facebook.com/groups/123/",
     "https://www.facebook.com/groups/123/posts/456/"),
    ("https://www.facebook.com/photo/?fbid=1&set=pcb.789", "https://www.facebook.com/groups/SolarPK/user/5/",
     "https://www.facebook.com/groups/SolarPK/posts/789/"),
    ("https://www.facebook.com/photo/?fbid=1&set=a.456", "https://www.facebook.com/groups/123/", None),   # page album
    ("https://www.facebook.com/photo/?fbid=1&set=gm.456", None, None),
    ("https://www.facebook.com/photo/?fbid=1", "https://www.facebook.com/groups/123/", None),
])
def test_group_post_from_photo(photo, group, expected):
    assert group_post_from_photo(photo, group) == expected


def test_strip_comment_params():
    assert strip_comment_params("https://www.facebook.com/x/posts/9/?comment_id=5&reply_comment_id=6") == \
        "https://www.facebook.com/x/posts/9/"
