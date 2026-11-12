from chainsout.models import Job
from chainsout.prefilter import Prefilter


def J(title, desc="Fully remote. Firmware for ARM Cortex-M microcontrollers in Rust.", job_type=""):
    return Job(source="x", url="u", title=title, description=desc, job_type=job_type)


def test_passes_good(profile):
    assert Prefilter(profile).check(J("Senior Embedded Rust Engineer")) is None


def test_rejects_obvious_titles(profile):
    pf = Prefilter(profile)
    for t in ["Embedded Software Intern", "Werkstudent Embedded Systems",
              "Firmware Engineer Part-Time", "Full-Stack Developer", "Sales Engineer Embedded"]:
        assert pf.check(J(t)), t


def test_job_type_only_when_all_bad(profile):
    pf = Prefilter(profile)
    assert pf.check(J("Firmware Engineer", job_type="parttime"))
    assert pf.check(J("Firmware Engineer", job_type="fulltime, parttime")) is None


def test_no_domain_keywords(profile):
    pf = Prefilter(profile)
    assert pf.check(J("Software Engineer", desc="React, Node and AWS.")) == "no_domain_keywords"
    assert pf.check(J("Software Engineer", desc="Linux kernel drivers, remote")) is None


def test_no_remote_mention(profile):
    pf = Prefilter(profile)
    onsite = Job(source="linkedin", url="u", title="Firmware Engineer", location="Munich",
                 description="Embedded C at our Munich site.")
    assert pf.check(onsite) == "no_remote_mention"
    onsite.description += " Fully remote possible."
    assert pf.check(onsite) is None


def test_company_reject(profile):
    pf = Prefilter(profile)
    for c in ["ACME Corp", "Initech GmbH"]:
        j = Job(source="x", url="u", title="Senior Embedded Rust Engineer", company=c,
                description="Fully remote. Firmware for ARM Cortex-M in Rust.")
        assert pf.check(j).startswith("company:"), c
    ok = Job(source="x", url="u", title="Senior Embedded Rust Engineer", company="Artistic Labs",
             description="Fully remote. Firmware for ARM Cortex-M in Rust.")
    assert pf.check(ok) is None
