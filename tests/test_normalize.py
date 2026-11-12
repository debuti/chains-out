from chainsout.models import Job
from chainsout.normalize import canonical_url, fingerprint, norm_company, norm_title, normalize


def test_linkedin_url_canonical():
    a = canonical_url("https://es.linkedin.com/jobs/view/senior-firmware-engineer-at-acme-4012345678?refId=x&trk=y")
    b = canonical_url("https://www.linkedin.com/jobs/view/4012345678/")
    assert a == b == "https://linkedin.com/jobs/view/4012345678"


def test_indeed_keeps_jk_only():
    u = canonical_url("https://www.indeed.com/viewjob?jk=abc123&from=serp&vjs=3")
    assert u == "https://indeed.com/viewjob?jk=abc123"


def test_hn_keeps_id():
    assert canonical_url("https://news.ycombinator.com/item?id=42&p=2") == \
        "https://news.ycombinator.com/item?id=42"


def test_tracking_params_dropped():
    assert canonical_url("http://jobs.example.com/x/?utm_source=li") == "https://jobs.example.com/x"


def test_norm_company_suffixes():
    assert norm_company("Acme Robotics GmbH") == norm_company("ACME Robotics") == "acme robotics"
    assert norm_company("Señal S.L.") == "senal"


def test_norm_title_noise():
    assert norm_title("Sr. Embedded Software Engineer (m/w/d) - Remote") == \
        norm_title("Senior Embedded Software Engineer")
    assert norm_title("Firmware Engineer (Rust)") == "firmware engineer"


def test_fingerprint_same_offer_across_sources():
    assert fingerprint("Acme GmbH", "Senior Firmware Engineer (m/f/d)") == \
        fingerprint("ACME", "Sr Firmware Engineer - Remote")
    assert fingerprint("Acme", "Firmware Engineer") != fingerprint("Acme", "Kernel Engineer")


def test_fingerprint_without_company_uses_url():
    a = normalize(Job(source="hn", url="https://news.ycombinator.com/item?id=1", title="Firmware Engineer"))
    b = normalize(Job(source="hn", url="https://news.ycombinator.com/item?id=2", title="Firmware Engineer"))
    assert a.fingerprint != b.fingerprint


def test_normalize_whitespace():
    j = normalize(Job(source="x", url="https://a.com/1", title="  Embedded\n Engineer ", company=" Acme  Inc "))
    assert j.title == "Embedded Engineer" and j.company == "Acme Inc"
