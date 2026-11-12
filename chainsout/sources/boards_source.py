"""Bolsas de empleo remoto con API/RSS pública (fase 2).

Revisado el 2026-11-12 (ver README):
- RemoteOK (remoteok.com/api): API JSON pública; condiciones: citar a Remote OK y enlazar a la
  oferta original. El filtro por tag no funciona: se descarga el feed y se filtra aquí.
- Remotive (remotive.com/api/remote-jobs): API pública; pide no consultarla más de unas pocas
  veces al día y no republicar en otros portales. Uso personal → OK, con intervalo mínimo.
- We Work Remotely: RSS público por categoría.
- Himalayas (himalayas.app/jobs/api/search): API pública con búsqueda y filtro por país.
- rustjobs.dev bloquea el acceso automatizado (403/429) → no se usa.
Cada bolsa admite `min_interval_hours` para no consultarla más de lo que piden sus condiciones.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import Job

log = logging.getLogger("chainsout.boards")

DEFAULT_KEYWORDS = r"."  # sin `keywords` en searches.yaml no se filtra (lo hace el prefiltro)
UA = {"User-Agent": "chainsout (uso personal)"}


def _text(s: str) -> str:
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def remoteok(client: httpx.Client, cfg: dict) -> list[Job]:
    r = client.get("https://remoteok.com/api", headers={"User-Agent": "Mozilla/5.0 chainsout"})
    r.raise_for_status()
    out = []
    for j in r.json()[1:]:  # el primer elemento es el aviso legal
        out.append(Job(source="remoteok", url=j.get("url") or j.get("apply_url", ""),
                       title=j.get("position", ""), company=j.get("company", ""),
                       location=j.get("location") or "Remote", is_remote=True,
                       salary_min=j.get("salary_min") or None,
                       salary_max=j.get("salary_max") or None,
                       currency="USD" if j.get("salary_max") else "",
                       salary_interval="yearly" if j.get("salary_max") else "",
                       description=_text(j.get("description", "")) +
                       f"\n\nTags: {', '.join(j.get('tags') or [])}",
                       posted_at=j.get("date", "")))
    return out


def remotive(client: httpx.Client, cfg: dict) -> list[Job]:
    r = client.get("https://remotive.com/api/remote-jobs",
                   params={"category": cfg.get("category", "software-dev")})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append(Job(source="remotive", url=j["url"], title=j.get("title", ""),
                       company=j.get("company_name", ""),
                       location=f"Remote ({j.get('candidate_required_location', '')})",
                       is_remote=True, job_type=j.get("job_type", ""),
                       description=(f"Salary: {j['salary']}\n\n" if j.get("salary") else "") +
                       _text(j.get("description", "")),
                       posted_at=j.get("publication_date", "")))
    return out


def weworkremotely(client: httpx.Client, cfg: dict) -> list[Job]:
    out = []
    for cat in cfg.get("categories", ["remote-programming-jobs"]):
        r = client.get(f"https://weworkremotely.com/categories/{cat}.rss")
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for it in root.iter("item"):
            g = lambda t: (it.findtext(t) or "").strip()  # noqa: E731
            title = g("title")
            company, _, role = title.partition(":")
            out.append(Job(source="weworkremotely", url=g("link") or g("guid"),
                           title=(role or title).strip(), company=company.strip() if role else "",
                           location=f"Remote ({g('region') or g('country') or '?'})",
                           is_remote=True, job_type=g("type"),
                           description=_text(g("description")), posted_at=g("pubDate")))
    return out


def himalayas(client: httpx.Client, cfg: dict) -> list[Job]:
    out = []
    for q in cfg.get("queries") or []:
        params = {"q": q, "sort": "recent", "limit": 20}
        if cfg.get("country"):
            params["country"] = cfg["country"]
        r = client.get("https://himalayas.app/jobs/api/search", params=params)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            locs = j.get("locationRestrictions") or []
            pub = j.get("pubDate")
            out.append(Job(
                source="himalayas", url=j.get("guid") or j.get("applicationLink", ""),
                title=j.get("title", ""), company=j.get("companyName", ""),
                location="Remote (" + (", ".join(locs[:6]) + ("…" if len(locs) > 6 else "")
                                       if locs else "worldwide") + ")",
                is_remote=True, job_type=j.get("employmentType", ""),
                salary_min=float(j["minSalary"]) if j.get("minSalary") else None,
                salary_max=float(j["maxSalary"]) if j.get("maxSalary") else None,
                currency=j.get("currency") or "", salary_interval=j.get("salaryPeriod") or "",
                description=_text(j.get("description", "")),
                posted_at=datetime.fromtimestamp(int(pub), timezone.utc).isoformat()
                if str(pub or "").isdigit() else ""))
    return out


BOARDS = {"remoteok": remoteok, "remotive": remotive, "weworkremotely": weworkremotely,
          "himalayas": himalayas}


def fetch(cfg: dict, root: Path, stats: dict, db=None) -> list[Job]:
    kw = re.compile(cfg.get("keywords") or DEFAULT_KEYWORDS, re.I)
    jobs: list[Job] = []
    now = datetime.now(timezone.utc)
    with httpx.Client(timeout=30, headers=UA, follow_redirects=True) as client:
        for name, bcfg in (cfg.get("boards") or {}).items():
            bcfg = bcfg or {}
            if name not in BOARDS or not bcfg.get("enabled", True):
                continue
            key = f"board_last_fetch:{name}"
            last = db.kv_get(key) if db else None
            min_h = float(bcfg.get("min_interval_hours", 0))
            if last and min_h and (now - datetime.fromisoformat(last)).total_seconds() < min_h * 3600:
                continue
            stats["queries"] += 1
            try:
                got = BOARDS[name](client, bcfg)
            except (httpx.HTTPError, ValueError, KeyError, ET.ParseError) as e:
                stats["errors"] += 1
                log.warning("%s: %s", name, type(e).__name__)
                continue
            if db:
                db.kv_set(key, now.isoformat())
            keep = [j for j in got if j.url and kw.search(j.title)]
            # Solo el título: en estas bolsas generalistas "embedded"/"rust" salen en cualquier
            # descripción ("embedded with our customers", listas de categorías...).
            stats[name] = len(keep)
            jobs += keep
    return jobs
