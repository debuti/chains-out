import httpx

from chainsout.normalize import canonical_url
from chainsout.sources import ats_source as A

_Client = httpx.Client
NOW = "2026-11-10T10:00:00+00:00"
ALL = lambda t: True  # noqa: E731


def client(routes):
    def handler(req):
        for frag, body in routes.items():
            if frag in str(req.url):
                if isinstance(body, str):
                    return httpx.Response(200, text=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)
    return _Client(transport=httpx.MockTransport(handler))


def test_workable_recruitee_personio_teamtailor():
    c = client({
        "workable.com/api/v1/widget/accounts/acme": {"jobs": [
            {"title": "Embedded Rust Engineer", "url": "https://apply.workable.com/j/1", "telecommuting": True,
             "locations": [{"city": "Paris", "country": "France"}], "description": "<p>no_std</p>",
             "published_on": "2026-11-01"}]},
        "bee.recruitee.com/api/offers": {"offers": [
            {"title": "Firmware Dev", "careers_url": "https://bee.recruitee.com/o/fw", "remote": True,
             "location": "NL", "description": "<p>C</p>", "requirements": "", "salary": {"min": "5000"},
             "published_at": "2026-11-01 10:00:00 UTC"},
            {"title": "Mech", "careers_url": "https://bee.recruitee.com/o/m", "on_site": True}]},
        "cat.jobs.personio.de/xml": "<workzag-jobs><position><id>7</id><office>Berlin</office>"
            "<name>Kernel Engineer</name><jobDescriptions><jobDescription><name>About</name>"
            "<value><![CDATA[<p>Remote ok</p>]]></value></jobDescription></jobDescriptions>"
            "<createdAt>2026-11-01T00:00:00+00:00</createdAt></position></workzag-jobs>",
        "dog.teamtailor.com/jobs.rss": '<rss xmlns:tt="https://teamtailor.com/locations"><channel>'
            "<item><title>Rust Dev</title><link>https://dog.teamtailor.com/jobs/1</link>"
            "<pubDate>Wed, 04 Nov 2026 10:00:00 +0100</pubDate><remoteStatus>fully</remoteStatus>"
            "<description>&lt;p&gt;x&lt;/p&gt;</description></item>"
            "<item><title>Office</title><link>https://dog.teamtailor.com/jobs/2</link>"
            "<remoteStatus>onsite</remoteStatus></item></channel></rss>",
    })
    w = A.workable(c, {"token": "acme", "name": "Acme"}, ALL)
    assert w[0].is_remote and "Remote" in w[0].location and w[0].description == "no_std"
    r = A.recruitee(c, {"token": "bee", "name": "Bee"}, ALL)
    assert [j.title for j in r] == ["Firmware Dev"] and r[0].salary_min == 5000
    p = A.personio(c, {"token": "cat", "name": "Cat"}, ALL)
    assert p[0].url == "https://cat.jobs.personio.de/job/7" and "Remote ok" in p[0].description
    t = A.teamtailor(c, {"token": "dog", "name": "Dog"}, ALL)
    assert [j.title for j in t] == ["Rust Dev"] and t[0].is_remote


def test_page_links_and_watch():
    listing = ('<html><body><a href="/careers/os-kernel">OS Kernel Engineer</a>'
               '<a href="/careers/finance">Director of Finance</a><a href="/about">About</a>'
               '<a href="https://other.com/careers/x">Other</a></body></html>')
    detail = "<html><body><main><h1>OS Kernel Engineer</h1><p>Fully remote, Rust.</p></main></body></html>"
    c = client({"ox.com/careers/os-kernel": detail, "ox.com/careers/finance": detail,
                "ox.com/careers": listing})
    cfg = {"name": "Ox", "url": "https://ox.com/careers", "link_regex": r"/careers/[\w-]+$"}
    jobs = A.page(c, cfg, lambda t: "Kernel" in t)
    assert [(j.title, j.url) for j in jobs] == [("OS Kernel Engineer", "https://ox.com/careers/os-kernel")]
    assert "Fully remote" in jobs[0].description
    w1 = A.page(c, {**cfg, "watch": True}, ALL)[0]
    assert w1.source == "page-watch" and "chainsout_v=" in w1.url
    # La versión forma parte de la URL canónica: si la página cambia, es otra "oferta".
    assert "chainsout_v" in canonical_url(w1.url)


def test_page_jsonld():
    html = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
            '"title":"Firmware Engineer","url":"https://z.com/j/1","datePosted":"2026-11-01",'
            '"jobLocationType":"TELECOMMUTE","description":"<p>MCU</p>"}</script>')
    jobs = A.page(client({"z.com/careers": html}), {"name": "Z", "url": "https://z.com/careers"}, ALL)
    assert jobs[0].title == "Firmware Engineer" and jobs[0].is_remote and jobs[0].description == "MCU"


def test_workday_budget_and_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(200, json={"jobPostings": [
                {"title": "Firmware Engineer", "externalPath": "/job/US/FW_1", "postedOn": "Posted 3 Days Ago",
                 "locationsText": "US, Remote"},
                {"title": "Firmware Lead", "externalPath": "/job/US/FW_2", "postedOn": "Posted Today"}]})
        if "FW_1" in str(req.url):
            return httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "<p>C</p>",
                                                                "externalUrl": "https://x/FW_1"}})
        return httpx.Response(429)
    c = {"name": "Acme", "token": "acme.wd5.myworkdayjobs.com/ext", "search": "firmware"}
    jobs = A.workday(_Client(transport=httpx.MockTransport(handler)), c, ALL)
    assert [j.url for j in jobs] == ["https://x/FW_1", "https://acme.wd5.myworkdayjobs.com/ext/job/US/FW_2"]
    assert jobs[0].description == "C" and not jobs[1].description and jobs[1].posted_at
    c["max_requests"] = 2
    calls.clear()
    jobs = A.workday(_Client(transport=httpx.MockTransport(handler)), c, ALL)
    assert len(calls) == 2 and len(jobs) == 2 and not jobs[1].description
