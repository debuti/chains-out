import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from chainsout.sources import ats_source as A
from chainsout.sources import boards_source as B

_Client = httpx.Client
NOW = datetime.now(timezone.utc).isoformat()


def client(routes):
    def handler(req):
        for frag, body in routes.items():
            if frag in str(req.url):
                if isinstance(body, str):
                    return httpx.Response(200, text=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)
    return _Client(transport=httpx.MockTransport(handler))


def test_greenhouse_and_filters(tmp_path, monkeypatch):
    (tmp_path / "companies.yaml").write_text(
        "title_regex: 'firmware|embedded|kernel|\\brust\\b'\n"
        "companies:\n  - {name: Acme, ats: greenhouse, token: acme}\n"
        "  - {name: Bee, ats: ashby, token: bee}\n")
    gh = {"jobs": [
        {"absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1", "title": "Senior Firmware Engineer",
         "company_name": "Acme", "location": {"name": "Remote - Europe"},
         "content": "&lt;p&gt;Rust on MCUs&lt;/p&gt;", "first_published": NOW},
        {"absolute_url": "https://x/2", "title": "Account Executive", "location": {"name": "Remote"},
         "content": "", "first_published": NOW},
        {"absolute_url": "https://x/3", "title": "Embedded Engineer", "location": {"name": "Munich"},
         "content": "on-site role", "first_published": NOW},
        {"absolute_url": "https://x/4", "title": "Kernel Engineer", "location": {"name": "Remote"},
         "content": "", "first_published": "2020-01-01T00:00:00Z"},
    ]}
    ab = {"jobs": [
        {"title": "Embedded Rust Developer", "jobUrl": "https://jobs.ashbyhq.com/bee/1",
         "location": "Spain", "workplaceType": "Remote", "isRemote": True, "isListed": True,
         "descriptionPlain": "no_std", "publishedAt": NOW, "employmentType": "FullTime"},
        {"title": "Firmware Engineer", "jobUrl": "https://jobs.ashbyhq.com/bee/2",
         "location": "Paris", "workplaceType": "Hybrid", "isListed": True, "publishedAt": NOW},
    ]}
    c = client({"greenhouse.io/v1/boards/acme": gh, "ashbyhq.com/posting-api/job-board/bee": ab})
    monkeypatch.setattr(A.httpx, "Client", lambda **kw: c)
    st = {"queries": 0, "errors": 0}
    jobs = A.fetch({}, tmp_path, st)
    assert [j.title for j in jobs] == ["Senior Firmware Engineer", "Embedded Rust Developer"]
    assert jobs[0].description == "Rust on MCUs" and st["queries"] == 2


def test_boards_title_filter_and_interval(db, monkeypatch):
    rss = """<rss><channel><item><title>Acme: Embedded Rust Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/acme-1</link><region>Anywhere</region>
      <description>&lt;p&gt;hi&lt;/p&gt;</description></item>
      <item><title>Foo: Sales Lead</title><link>https://weworkremotely.com/x</link>
      <description>embedded with customers</description></item></channel></rss>"""
    rok = [{"legal": "..."}, {"position": "Firmware Engineer", "company": "R", "url": "https://remoteok.com/1",
                              "description": "x", "tags": ["c"]}]
    routes = {"weworkremotely.com/categories": rss, "remoteok.com/api": rok}
    monkeypatch.setattr(B.httpx, "Client", lambda **kw: client(routes))
    cfg = {"keywords": r"\brust\b|embedded|firmware",
           "boards": {"weworkremotely": {}, "remoteok": {"min_interval_hours": 8}}}
    st = {"queries": 0, "errors": 0}
    jobs = B.fetch(cfg, Path("."), st, db=db)
    assert {(j.company, j.title) for j in jobs} == {("Acme", "Embedded Rust Engineer"), ("R", "Firmware Engineer")}
    st2 = {"queries": 0, "errors": 0}
    B.fetch(cfg, Path("."), st2, db=db)
    assert st2["queries"] == 1  # remoteok se salta por min_interval_hours
