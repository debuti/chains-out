import json
import re
from pathlib import Path

from chainsout.sources import hn_source as h

RX = re.compile(h.DEFAULT_KEYWORDS, re.I)
COMMENTS = json.loads((Path(__file__).parent / "fixtures" / "hn_comments.json").read_text())


def test_parse_header():
    assert h.parse_header("Acme | Senior Firmware Engineer | REMOTE (EU) | Full-time | €120k") == \
        ("Acme", "Senior Firmware Engineer", "REMOTE (EU)")
    assert h.parse_header("Foo | Full-time | Remote")[1].startswith("Oferta en HN")


def test_html_to_text():
    t = h.html_to_text('a &amp; b<p>see <a href="https:&#x2F;&#x2F;x.com&#x2F;j" rel="nofollow">x</a>')
    assert t == "a & b\n\nsee https://x.com/j"


def test_comment_filter_real_thread():
    jobs = [j for j in (h.comment_to_job(c, RX) for c in COMMENTS) if j]
    # El PhD es ONSITE → fuera; la de Rust remote pasa
    assert all("PhD" not in j.title for j in jobs)
    assert any("Rust" in j.title for j in jobs)
    j = next(j for j in jobs if "Rust" in j.title)
    assert j.url.startswith("https://news.ycombinator.com/item?id=") and j.source == "hn"
