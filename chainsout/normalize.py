"""Normalización: URL canónica y fingerprint (empresa + título) para deduplicar entre fuentes."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .models import Job

_COMPANY_SUFFIXES = re.compile(
    r"\b(gmbh|ag|se|ltd|limited|llc|inc|corp|corporation|plc|bv|b\.v|s\.?l\.?u?|s\.?a\.?|sas|sarl|"
    r"oy|ab|as|aps|srl|spa|kg|co|company|group|holding|holdings)\b\.?"
)
_TITLE_NOISE = re.compile(
    r"\((?:[mwfdx]\s*/\s*){1,3}[mwfdx]\)|\b(?:[mwfd]/){2}[mwfdx]\b|"
    r"\b(remote|hybrid|full[- ]?time|fully remote|100% remote|europe|emea|eu)\b"
)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_company(company: str) -> str:
    s = _ascii(company or "").lower()
    s = _COMPANY_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def norm_title(title: str) -> str:
    s = _ascii(title or "").lower()
    s = _TITLE_NOISE.sub(" ", s)
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"\bsr\b\.?", "senior", s)
    s = re.sub(r"\bjr\b\.?", "junior", s)
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return " ".join(s.split())


def fingerprint(company: str, title: str) -> str:
    key = f"{norm_company(company)}|{norm_title(title)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


_KEEP_QUERY = {"indeed.": {"jk"}, "news.ycombinator.com": {"id"}, "greenhouse.io": {"gh_jid"}}


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    # LinkedIn: .../jobs/view/<slug>-<id> → /jobs/view/<id>
    if "linkedin.com" in host:
        m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", path)
        if m:
            return f"https://linkedin.com/jobs/view/{m.group(1)}"
        host = "linkedin.com"
    keep = next((v for k, v in _KEEP_QUERY.items() if k in host), set())
    query = {k: v for k, v in parse_qs(parts.query).items() if k in keep}
    return urlunsplit(("https", host, path, urlencode(query, doseq=True), ""))


def normalize(job: Job) -> Job:
    job.title = " ".join((job.title or "").split())
    job.company = " ".join((job.company or "").split())
    job.location = " ".join((job.location or "").split())
    job.url_canonical = canonical_url(job.url)
    # Sin empresa no podemos saber si dos títulos genéricos son la misma oferta: usamos la URL.
    job.fingerprint = (fingerprint(job.company, job.title) if norm_company(job.company)
                       else fingerprint(job.url_canonical, job.title))
    return job
