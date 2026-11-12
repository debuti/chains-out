"""Hacker News "Ask HN: Who is hiring?" vía la API de Algolia.

Cada comentario de primer nivel del hilo mensual es una oferta. Se lee el hilo del mes y, durante
los primeros días del mes, también el anterior. Prefiltro propio: REMOTE + keywords de dominio.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import Job

log = logging.getLogger("chainsout.hn")

ALGOLIA = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={id}"

DEFAULT_KEYWORDS = r"."  # sin `keywords` en searches.yaml no se filtra (lo hace el prefiltro)
ROLE_RX = re.compile(r"engineer|developer|programmer|architect|lead|head of|scientist|swe\b|"
                     r"staff|principal|cto|founding", re.I)


def html_to_text(s: str) -> str:
    s = re.sub(r"<p>", "\n\n", s or "")
    s = re.sub(r"<a [^>]*href=\"([^\"]+)\"[^>]*>.*?</a>", r"\1", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def parse_header(text: str) -> tuple[str, str, str]:
    """(empresa, título, ubicación) a partir de la primera línea "Empresa | Rol | Lugar | ..."."""
    first = text.split("\n", 1)[0]
    parts = [p.strip() for p in first.split("|") if p.strip()]
    if len(parts) < 2:
        return "", first[:120], ""
    company = parts[0][:80]
    rest = parts[1:]
    nonrole = re.compile(r"remote|onsite|on-site|hybrid|https?://|(?-i:,\s?[A-Z]{2}\s*$)|"
                         r"full.?time|part.?time|contract|intern|visa|[$€£]\s?\d|\d+k\b", re.I)
    roles = [p for p in rest if not nonrole.search(p)]
    title = next((p for p in roles if ROLE_RX.search(p)), roles[0] if roles else "")
    title = title or "Oferta en HN Who is hiring (ver descripción)"
    loc = next((p for p in rest if re.search(r"remote|onsite|on-site|hybrid", p, re.I)), "")
    return company, title[:160], loc[:160]


def find_threads(client: httpx.Client, grace_days: int) -> list[dict]:
    r = client.get(f"{ALGOLIA}/search_by_date",
                   params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
    r.raise_for_status()
    hits = [h for h in r.json()["hits"] if "who is hiring" in h["title"].lower()]
    if not hits:
        return []
    out = [hits[0]]
    created = datetime.fromtimestamp(hits[0]["created_at_i"], timezone.utc)
    if (datetime.now(timezone.utc) - created).days < grace_days and len(hits) > 1:
        out.append(hits[1])
    return out


def comment_to_job(c: dict, keyword_rx: re.Pattern) -> Job | None:
    if not c.get("text") or c.get("type") != "comment":
        return None
    text = html_to_text(c["text"])
    if not re.search(r"\bremote\b", text, re.I) or not keyword_rx.search(text):
        return None
    company, title, loc = parse_header(text)
    return Job(source="hn", url=ITEM_URL.format(id=c["id"]), title=title, company=company,
               location=loc, is_remote=True, description=text,
               posted_at=(c.get("created_at") or "")[:10])


def fetch(cfg: dict, root: Path, stats: dict) -> list[Job]:
    keyword_rx = re.compile(cfg.get("keywords") or DEFAULT_KEYWORDS, re.I)
    with httpx.Client(timeout=30, headers={"User-Agent": "chainsout/0.2"}) as client:
        threads = find_threads(client, int(cfg.get("previous_thread_grace_days", 10)))
        jobs: list[Job] = []
        for t in threads:
            stats["queries"] += 1
            r = client.get(f"{ALGOLIA}/items/{t['objectID']}")
            r.raise_for_status()
            kids = r.json().get("children", [])
            got = [j for j in (comment_to_job(c, keyword_rx) for c in kids) if j]
            log.debug("%s: %d comentarios, %d candidatos", t["title"], len(kids), len(got))
            stats["comments"] = stats.get("comments", 0) + len(kids)
            jobs += got
    return jobs
