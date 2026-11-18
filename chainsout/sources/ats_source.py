"""Páginas de empleo de empresas concretas (companies.yaml): APIs públicas de Greenhouse, Lever,
Ashby, Workable, Recruitee, Personio, Teamtailor, BambooHR, SmartRecruiters, Workday, Eightfold y
amazon.jobs, o la página propia de la empresa (`ats: page`). Se filtran aquí por título (dominio), antigüedad y remoto."""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..config import CONFIG_ENV, load_yaml
from ..models import Job

log = logging.getLogger("chainsout.ats")

DEFAULT_TITLE_RX = r"."  # sin `title_regex` en companies.yaml no se filtra por título
REMOTE_RX = re.compile(r"remote|home.based|work from home|worldwide|anywhere|distributed", re.I)
UA = "Mozilla/5.0 (compatible; chainsout job reader)"
NOT_REMOTE = {"onsite", "on-site", "hybrid", "in office", "inoffice"}


def _strip_html(s: str) -> str:
    s = html.unescape(s or "")  # Greenhouse devuelve el HTML escapado
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s)
    s = re.sub(r"<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _parse_dt(v) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def greenhouse(client: httpx.Client, token: str, name: str) -> list[Job]:
    r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                   params={"content": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append(Job(source="greenhouse", url=j["absolute_url"], title=j.get("title", ""),
                       company=j.get("company_name") or name,
                       location=(j.get("location") or {}).get("name", ""),
                       description=_strip_html(j.get("content", "")),
                       posted_at=j.get("first_published") or j.get("updated_at") or ""))
    return out


def lever(client: httpx.Client, token: str, name: str) -> list[Job]:
    r = client.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        wt = (j.get("workplaceType") or "").lower()
        if wt in NOT_REMOTE:
            continue
        desc = "\n\n".join(x for x in (
            j.get("descriptionPlain"),
            *(f"{li.get('text')}\n{_strip_html(li.get('content', ''))}" for li in j.get("lists", [])),
            j.get("additionalPlain")) if x)
        sal = j.get("salaryRange") or {}
        dt = _parse_dt(j.get("createdAt"))
        out.append(Job(source="lever", url=j["hostedUrl"], title=j.get("text", ""), company=name,
                       location=" / ".join(x for x in (cat.get("location"), wt) if x),
                       is_remote=True if wt == "remote" else None,
                       job_type=cat.get("commitment", ""), description=desc,
                       salary_min=sal.get("min"), salary_max=sal.get("max"),
                       currency=sal.get("currency", ""), salary_interval=sal.get("interval", ""),
                       posted_at=dt.isoformat() if dt else ""))
    return out


def ashby(client: httpx.Client, token: str, name: str) -> list[Job]:
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}",
                   params={"includeCompensation": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        wt = (j.get("workplaceType") or "").lower()
        if wt in NOT_REMOTE:
            continue
        locs = [j.get("location") or ""] + [x.get("location", "")
                                            for x in j.get("secondaryLocations") or []]
        comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
        desc = j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", ""))
        if comp:
            desc = f"Compensation: {comp}\n\n{desc}"
        out.append(Job(source="ashby", url=j["jobUrl"], title=j.get("title", ""), company=name,
                       location=" / ".join(x for x in [*locs, wt] if x),
                       is_remote=j.get("isRemote"), job_type=j.get("employmentType") or "",
                       description=desc, posted_at=j.get("publishedAt") or ""))
    return out


def _req(client, method: str, url: str, **kw) -> httpx.Response:
    """Petición con reintento si el servidor pide frenar (429)."""
    import time
    for wait in (2, 6, 15, 0):
        r = client.request(method, url, **kw)
        if r.status_code != 429 or not wait:
            break
        time.sleep(wait)
    r.raise_for_status()
    return r


def _dt_iso(v) -> str:
    dt = _parse_dt(v)
    return dt.isoformat() if dt else ""


def workable(client, c, want):
    tok = c["token"]
    r = _req(client, "GET", f"https://apply.workable.com/api/v1/widget/accounts/{tok}",
             params={"details": "true"})
    out = []
    for j in r.json().get("jobs", []):
        locs = [", ".join(x for x in (lo.get("city"), lo.get("country")) if x)
                for lo in j.get("locations") or []]
        remote = bool(j.get("telecommuting"))
        out.append(Job(source="workable", url=j.get("url") or j.get("shortlink"),
                       title=j.get("title", ""), company=c["name"],
                       location=" / ".join([*locs, *(["Remote"] if remote else [])]),
                       is_remote=remote or None, job_type=j.get("employment_type") or "",
                       description=_strip_html(j.get("description", "")),
                       posted_at=j.get("published_on") or j.get("created_at") or ""))
    return out


def recruitee(client, c, want):
    r = client.get(f"https://{c['token']}.recruitee.com/api/offers/")
    r.raise_for_status()
    out = []
    for j in r.json().get("offers", []):
        if j.get("status", "published") != "published":
            continue
        remote = bool(j.get("remote"))
        if not remote and (j.get("on_site") or j.get("hybrid")):
            continue
        sal = j.get("salary") or {}
        desc = "\n\n".join(_strip_html(j.get(k) or "") for k in ("description", "requirements"))
        out.append(Job(source="recruitee", url=j.get("careers_url") or j.get("careers_apply_url"),
                       title=j.get("title", ""), company=c["name"],
                       location=" / ".join(x for x in (j.get("location"),
                                                       "Remote" if remote else "") if x),
                       is_remote=remote or None, job_type=j.get("employment_type_code") or "",
                       description=desc, salary_min=_num(sal.get("min")),
                       salary_max=_num(sal.get("max")), currency=sal.get("currency") or "",
                       salary_interval=sal.get("period") or "",
                       posted_at=_dt_iso((j.get("published_at") or j.get("created_at") or "")
                                         .replace(" UTC", "+00:00"))))
    return out


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def personio(client, c, want):
    import xml.etree.ElementTree as ET
    tok = c["token"]
    host = c.get("host") or f"{tok}.jobs.personio.de"
    r = client.get(f"https://{host}/xml", params={"language": "en"})
    r.raise_for_status()
    out = []
    for p in ET.fromstring(r.content).iter("position"):
        g = lambda k: (p.findtext(k) or "").strip()  # noqa: E731
        offices = [g("office"), *[(o.text or "").strip() for o in p.iter("office")][1:]]
        desc = "\n\n".join(f"{(d.findtext('name') or '').strip()}\n{_strip_html(d.findtext('value') or '')}"
                            for d in p.iter("jobDescription"))
        out.append(Job(source="personio", url=f"https://{host}/job/{g('id')}",
                       title=g("name"), company=c["name"],
                       location=" / ".join(dict.fromkeys(o for o in offices if o)),
                       job_type=g("schedule") or g("employmentType"), description=desc,
                       posted_at=g("createdAt")))
    return out


def teamtailor(client, c, want):
    import xml.etree.ElementTree as ET
    host = c.get("host") or f"{c['token']}.teamtailor.com"
    r = client.get(f"https://{host}/jobs.rss")
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        g = lambda k: (it.findtext(k) or "").strip()  # noqa: E731
        status = g("remoteStatus").lower()
        if status in ("onsite", "hybrid"):
            continue
        locs = [(n.text or "").strip() for n in it.iter("{https://teamtailor.com/locations}name")]
        try:
            from email.utils import parsedate_to_datetime
            posted = parsedate_to_datetime(g("pubDate")).isoformat()
        except (TypeError, ValueError):
            posted = ""
        out.append(Job(source="teamtailor", url=g("link"), title=g("title"), company=c["name"],
                       location=" / ".join(x for x in [*locs, status] if x),
                       is_remote=True if status in ("fully", "remote") else None,
                       description=_strip_html(g("description")), posted_at=posted))
    return out


def bamboohr(client, c, want):
    base = f"https://{c['token']}.bamboohr.com/careers"
    r = client.get(f"{base}/list")
    r.raise_for_status()
    out = []
    for j in r.json().get("result", []):
        title = j.get("jobOpeningName", "")
        if not want(title):
            continue
        loc = j.get("location") or {}
        remote = str(j.get("isRemote")) in ("1", "True", "true") or j.get("locationType") == "1"
        try:
            d = client.get(f"{base}/{j['id']}/detail").json().get("result", {}).get("jobOpening", {})
        except (httpx.HTTPError, ValueError):
            d = {}
        out.append(Job(source="bamboohr", url=f"{base}/{j['id']}", title=title, company=c["name"],
                       location=" / ".join(x for x in (loc.get("city"), loc.get("state"),
                                                       "Remote" if remote else "") if x),
                       is_remote=remote or None, job_type=j.get("employmentStatusLabel") or "",
                       description=_strip_html(d.get("description", "")),
                       posted_at=d.get("datePosted") or ""))
    return out


def smartrecruiters(client, c, want):
    base = f"https://api.smartrecruiters.com/v1/companies/{c['token']}/postings"
    out, offset = [], 0
    while offset < 1000:
        r = client.get(base, params={"limit": 100, "offset": offset})
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            if not want(j.get("name", "")) or (loc.get("hybrid") and not loc.get("remote")):
                continue
            try:
                d = client.get(f"{base}/{j['id']}").json()
            except (httpx.HTTPError, ValueError):
                d = {}
            secs = ((d.get("jobAd") or {}).get("sections") or {}).values()
            out.append(Job(source="smartrecruiters",
                           url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{c['token']}/{j['id']}",
                           title=j.get("name", ""), company=c["name"],
                           location=" / ".join(x for x in (loc.get("fullLocation"),
                                                           "Remote" if loc.get("remote") else "") if x),
                           is_remote=loc.get("remote"),
                           description="\n\n".join(_strip_html(s.get("text", "")) for s in secs
                                                    if isinstance(s, dict)),
                           posted_at=j.get("releasedDate") or ""))
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def _wd_posted(v: str) -> str:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' → ISO."""
    v = (v or "").lower()
    m = re.search(r"(\d+)", v)
    days = 0 if "today" in v else 1 if "yesterday" in v else int(m.group(1)) if m else None
    if days is None:
        return ""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def workday(client, c, want):
    """token: <tenant>.wdN.myworkdayjobs.com/<site>. `search` opcional (texto de búsqueda).

    Workday corta (429) tras unas 60 peticiones seguidas por IP: se limita a `max_requests`
    (40) por empresa; sin presupuesto, la oferta va sin descripción, y si corta se devuelve
    lo que haya.
    """
    m = re.match(r"(?:https?://)?([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)",
                 c["token"])
    if not m:
        raise ValueError("token de workday no válido")
    tenant, wd, site = m.groups()
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    budget = [int(c.get("max_requests", 40))]

    def call(method, url, **kw):
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        try:
            return _req(client, method, url, **kw)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            log.info("%s: Workday pide frenar; se queda con lo que hay", c["name"])
            budget[0] = 0
            return None

    found: dict[str, dict] = {}
    for q in _searches(c):
        for offset in range(0, 100, 20):
            r = call("POST", f"{api}/jobs", json={"limit": 20, "offset": offset,
                                                  "searchText": q, "appliedFacets": {}})
            posts = r.json().get("jobPostings", []) if r else []
            for j in posts:
                path = j.get("externalPath", "")
                if path and path not in found and want(j.get("title", "")) \
                        and (j.get("remoteType") or "").lower() not in ("hybrid", "on-site", "onsite"):
                    found[path] = j
            if len(posts) < 20:
                break
    out = []
    for path, j in found.items():
        r = call("GET", f"{api}{path}")
        try:
            d = r.json().get("jobPostingInfo", {}) if r else {}
        except ValueError:
            d = {}
        out.append(Job(source="workday", url=d.get("externalUrl") or f"{host}/{site}{path}",
                       title=j.get("title", ""), company=c["name"],
                       location=" / ".join(x for x in (d.get("location") or j.get("locationsText"),
                                                       j.get("remoteType")) if x),
                       job_type=d.get("timeType") or "",
                       description=_strip_html(d.get("jobDescription", "")),
                       posted_at=d.get("startDate") or _wd_posted(j.get("postedOn", ""))))
    return out


def _searches(c) -> list[str]:
    q = c.get("search") or [""]
    return [q] if isinstance(q, str) else list(q)


def eightfold(client, c, want):
    """Eightfold (p. ej. Infineon). host: jobs.infineon.com, token: dominio (infineon.com)."""
    host, dom = c["host"], c["token"]
    seen, out = set(), []
    for q in _searches(c):
        for start in range(0, 100, 10):
            r = client.get(f"https://{host}/api/pcsx/search",
                           params={"domain": dom, "query": q, "location": "", "start": start,
                                   "sort_by": "timestamp"})
            r.raise_for_status()
            pos = (r.json().get("data") or {}).get("positions") or []
            for j in pos:
                if j["id"] in seen or not want(j.get("name", "")) \
                        or (j.get("workLocationOption") or "").lower() in ("onsite", "hybrid"):
                    continue
                seen.add(j["id"])
                try:
                    d = client.get(f"https://{host}/api/pcsx/position_details",
                                   params={"position_id": j["id"], "domain": dom, "hl": "en"}
                                   ).json().get("data") or {}
                except (httpx.HTTPError, ValueError):
                    d = {}
                ts = j.get("postedTs")
                out.append(Job(source="eightfold", url=d.get("publicUrl") or f"https://{host}{j.get('positionUrl', '')}",
                               title=j.get("name", ""), company=c["name"],
                               location=" / ".join([*(j.get("locations") or []), j.get("workLocationOption") or ""]).strip(" /"),
                               is_remote=True if j.get("workLocationOption") == "remote" else None,
                               description=_strip_html(d.get("jobDescription", "")),
                               posted_at=_dt_iso(ts) if ts else ""))
            if len(pos) < 10:
                break
    return out


def amazon(client, c, want):
    """amazon.jobs (búsqueda JSON pública). `search`: textos a buscar."""
    seen, out = set(), []
    for q in _searches(c):
        r = client.get("https://www.amazon.jobs/en/search.json",
                       params={"base_query": q, "result_limit": 100, "sort": "recent"})
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            if j["id"] in seen or not want(j.get("title", "")):
                continue
            seen.add(j["id"])
            try:
                posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                posted = ""
            desc = "\n\n".join(_strip_html(j.get(k) or "") for k in
                                ("description", "basic_qualifications", "preferred_qualifications"))
            out.append(Job(source="amazon", url=f"https://www.amazon.jobs{j.get('job_path', '')}",
                           title=j.get("title", ""), company=j.get("company_name") or c["name"],
                           location=j.get("normalized_location") or j.get("location") or "",
                           job_type=j.get("job_schedule_type") or "", description=desc,
                           posted_at=posted))
    return out


# --- Páginas propias (sin ATS) -------------------------------------------------------------------

LD_RX = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
A_RX = re.compile(r'<a\b[^>]*?href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def _ld_postings(text: str) -> list[dict]:
    import json
    out = []
    for raw in LD_RX.findall(text):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack += d
            elif isinstance(d, dict):
                if "JobPosting" in str(d.get("@type")):
                    out.append(d)
                stack += [v for k, v in d.items() if k in ("@graph", "itemListElement", "item")]
    return out


def _ld_location(d: dict) -> tuple[str, bool]:
    locs = d.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    names = []
    for lo in locs:
        a = (lo or {}).get("address") if isinstance(lo, dict) else None
        if isinstance(a, dict):
            names.append(", ".join(str(a[k]) for k in ("addressLocality", "addressCountry")
                                   if isinstance(a.get(k), str) and a.get(k)))
        elif isinstance(a, str):
            names.append(a)
    remote = "TELECOMMUTE" in str(d.get("jobLocationType", "")).upper()
    if remote:
        names.append("Remote")
    return " / ".join(n for n in names if n), remote


def _page_text(html_s: str) -> str:
    s = re.sub(r"<(script|style|noscript|svg|nav|header|footer)\b.*?</\1>", " ", html_s, flags=re.S | re.I)
    m = re.search(r"<main\b.*?</main>", s, re.S | re.I) or re.search(r"<body\b.*?</body>", s, re.S | re.I)
    return re.sub(r"[ \t]+", " ", _strip_html(m.group(0) if m else s))[:15000]


def _ld_job(d: dict, url: str, name: str) -> Job:
    loc, remote = _ld_location(d)
    return Job(source="page", url=d.get("url") or url, title=html.unescape(str(d.get("title", ""))),
               company=name, location=loc, is_remote=remote or None,
               job_type=str(d.get("employmentType") or ""),
               description=_strip_html(str(d.get("description", ""))),
               posted_at=str(d.get("datePosted") or ""))


def page_links(client, c) -> tuple[list[Job], dict[str, str]]:
    """Lee la página de empleo: (ofertas JSON-LD si las trae, {enlace: texto} candidatos)."""
    url = c["url"]
    r = client.get(url, follow_redirects=True)
    r.raise_for_status()
    found = _ld_postings(r.text)
    if found:
        return [_ld_job(d, url, c["name"]) for d in found], {}
    link_rx = re.compile(c["link_regex"]) if c.get("link_regex") else None
    links: dict[str, str] = {}
    for href, inner in A_RX.findall(r.text):
        text = re.sub(r"\s+", " ", _strip_html(inner)).strip()
        try:
            full = str(httpx.URL(str(r.url)).join(html.unescape(href.strip())))
        except (httpx.InvalidURL, ValueError):
            continue
        if full.rstrip("/") == str(r.url).rstrip("/") or not full.startswith("http"):
            continue
        if link_rx and not link_rx.search(full):
            continue
        if not link_rx and httpx.URL(full).host != r.url.host:
            continue
        if text and full not in links:
            links[full] = text[:200]
    return [], links


def page_watch(client, c) -> Job:
    """Página sin enlaces a ofertas: la página entera es una "oferta" cuya versión (hash del texto)
    va en la URL, así que vuelve a juzgarse solo cuando cambia."""
    import hashlib
    r = client.get(c["url"], follow_redirects=True)
    r.raise_for_status()
    text = _page_text(r.text)
    h = hashlib.sha1(re.sub(r"\W+", "", text).encode()).hexdigest()[:8]
    sep = "&" if "?" in c["url"] else "?"
    return Job(source="page-watch", url=f"{c['url']}{sep}chainsout_v={h}",
               title=f"Careers page [{h}]", company=c["name"], description=text)


def page(client, c, want):
    """Página de empleo propia (`url`: una o una lista, p. ej. una por búsqueda). Usa los JobPosting
    de schema.org (JSON-LD) si los hay; si no, los enlaces cuyo href casa con `link_regex` (o, sin
    él, del mismo sitio) y cuyo texto pasa el filtro de títulos, y abre cada oferta para sacar su
    texto. Con `watch: true` la página entera es la oferta (ver page_watch)."""
    urls = c["url"] if isinstance(c["url"], list) else [c["url"]]
    if c.get("watch"):
        return [page_watch(client, {**c, "url": urls[0]})]
    links: dict[str, str] = {}
    for u in urls:
        ld_jobs, got = page_links(client, {**c, "url": u})
        if ld_jobs:
            return ld_jobs
        links.update(got)
    out = []
    for full, text in list(links.items())[: int(c.get("max_links", 60))]:
        if not want(text):
            continue
        try:
            d = client.get(full, follow_redirects=True)
            d.raise_for_status()
        except httpx.HTTPError:
            continue
        ld = _ld_postings(d.text)
        if ld:
            j = _ld_job(ld[0], full, c["name"])
            j.url = full
            out.append(j)
        else:
            out.append(Job(source="page", url=full, title=text, company=c["name"],
                           description=_page_text(d.text)))
    return out


def _wrap(fn):
    return lambda client, c, want: fn(client, c["token"], c["name"])


PROVIDERS = {"greenhouse": _wrap(greenhouse), "lever": _wrap(lever), "ashby": _wrap(ashby),
             "workable": workable, "recruitee": recruitee, "personio": personio,
             "teamtailor": teamtailor, "bamboohr": bamboohr,
             "smartrecruiters": smartrecruiters, "workday": workday,
             "eightfold": eightfold, "amazon": amazon, "page": page}


def _keep(j: Job, title_rx: re.Pattern, now: datetime, max_age: timedelta) -> bool:
    if not title_rx.search(j.description if j.source == "page-watch" else j.title):
        return False
    dt = _parse_dt(j.posted_at)
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt and now - dt > max_age:
        return False
    # Sin mención de remoto ni en ubicación ni en la descripción → presencial.
    if not j.is_remote and not REMOTE_RX.search(f"{j.location}\n{j.description}"):
        return False
    return not re.search(r"office.based|on.?site", j.location, re.I)


def fetch(cfg: dict, root: Path, stats: dict) -> list[Job]:
    from concurrent.futures import ThreadPoolExecutor

    path = root / cfg.get("file", "companies.yaml")
    if not path.exists() and not os.environ.get(CONFIG_ENV):
        return []
    data = load_yaml(path)
    title_rx = re.compile(data.get("title_regex") or DEFAULT_TITLE_RX, re.I)
    max_age = timedelta(days=int(data.get("max_age_days", 60)))
    now = datetime.now(timezone.utc)
    entries = []
    for c in data.get("companies", []):
        c = dict(c)
        prov = c.get("ats")
        c["name"] = c.get("name") or c.get("token") or c.get("url")
        if prov not in PROVIDERS or not (c.get("url") if prov == "page" else c.get("token") or prov == "amazon"):
            log.warning("companies.yaml: entrada no válida %r", c)
            continue
        entries.append(c)

    def one(c):
        with httpx.Client(timeout=30, http2=True, headers={"User-Agent": UA}) as client:
            return PROVIDERS[c["ats"]](client, c, lambda t: bool(title_rx.search(t)))

    jobs: list[Job] = []
    with ThreadPoolExecutor(int(cfg.get("workers", 8))) as ex:
        futs = [(c, ex.submit(one, c)) for c in entries]
        for c, fut in futs:
            stats["queries"] += 1
            try:
                got = fut.result()
            except (httpx.HTTPError, ValueError, KeyError, TypeError, SyntaxError) as e:
                stats["errors"] += 1
                log.warning("%s (%s): %s", c["name"], c["ats"], type(e).__name__)
                continue
            jobs += [j for j in got if j.url and _keep(j, title_rx, now, max_age)]
    return jobs
