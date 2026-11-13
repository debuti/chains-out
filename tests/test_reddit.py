import re

from chainsout.sources import reddit_source as r

BODY = """**COMPANY:** Acme Robotics

**TYPE:** Full time

**LOCATION:** Berlin, Germany

**REMOTE:** Yes, EU timezones

**VISA:** No

**DESCRIPTION:** We build robots. Looking for a Senior Rust Engineer to work on our control stack.

**ESTIMATED COMPENSATION:** 90-120k EUR
"""


def c(body=BODY, author="someone"):
    return {"body": body, "author": author, "permalink": "/r/rust/comments/abc/x/def/",
            "created_utc": 1_760_000_000}


def test_parse_fields():
    f = r.parse_fields(BODY)
    assert f["company"] == "Acme Robotics" and f["type"] == "Full time"
    assert f["remote"].startswith("Yes")


def test_comment_to_job():
    j = r.comment_to_job(c(), "rust", re.compile("rust", re.I))
    assert j.source == "reddit" and j.company == "Acme Robotics"
    assert j.url == "https://www.reddit.com/r/rust/comments/abc/x/def/"
    assert "Rust Engineer" in j.title and j.is_remote and "Berlin" in j.location
    assert j.posted_at.startswith("2025-")


def test_filters():
    rx = re.compile(".")
    assert r.comment_to_job(c(author="AutoModerator"), "rust", rx) is None
    assert r.comment_to_job(c(body="[deleted]", author="[deleted]"), "rust", rx) is None
    assert r.comment_to_job(c(), "rust", re.compile("golang")) is None
    j = r.comment_to_job(c(body="We are hiring, DM me"), "rust", rx)
    assert j.title == "Oferta en r/rust (ver descripción)"
