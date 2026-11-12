"""Páginas de empleo de empresas concretas (companies.yaml) vía las APIs JSON públicas de
Greenhouse, Lever y Ashby. Devuelven todas las ofertas de la empresa: se filtran aquí por título
(dominio) y se descartan las marcadas explícitamente como presenciales/híbridas."""

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


PROVIDERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch(cfg: dict, root: Path, stats: dict) -> list[Job]:
    path = root / cfg.get("file", "companies.yaml")
    if not path.exists() and not os.environ.get(CONFIG_ENV):
        return []
    data = load_yaml(path)
    title_rx = re.compile(data.get("title_regex") or DEFAULT_TITLE_RX, re.I)
    max_age = timedelta(days=int(data.get("max_age_days", 60)))
    now = datetime.now(timezone.utc)
    jobs: list[Job] = []
    with httpx.Client(timeout=30, headers={"User-Agent": "chainsout"}) as client:
        for c in data.get("companies", []):
            prov, token = c.get("ats"), c.get("token")
            name = c.get("name") or token
            if prov not in PROVIDERS or not token:
                log.warning("companies.yaml: entrada no válida %r", c)
                continue
            stats["queries"] += 1
            try:
                got = PROVIDERS[prov](client, token, name)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                stats["errors"] += 1
                log.warning("%s (%s/%s): %s", name, prov, token, type(e).__name__)
                continue
            for j in got:
                dt = _parse_dt(j.posted_at)
                if not title_rx.search(j.title):
                    continue
                if dt and now - dt > max_age:
                    continue
                # Sin mención de remoto ni en ubicación ni en la descripción → presencial.
                if not REMOTE_RX.search(f"{j.location}\n{j.description}"):
                    continue
                if re.search(r"office.based|on.?site", j.location, re.I):
                    continue
                jobs.append(j)
    return jobs
