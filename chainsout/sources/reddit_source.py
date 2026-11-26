"""Hilos de ofertas de Reddit (p. ej. el "Who's Hiring" de r/rust, que se renueva con cada versión).

Por cada entrada de `threads` busca en el subreddit los posts cuyo título casa con `title_regex`,
lee el más reciente (y el anterior si salió hace menos de `previous_thread_grace_days`) y convierte
cada comentario de primer nivel en una oferta.

Reddit ya no da API ni deja leer sin ella (bloquea curl y navegadores automáticos), así que se lee
de Arctic Shift (arctic-shift.photon-reddit.com), un archivo público de Reddit casi en tiempo real.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import Job
from .hn_source import ROLE_RX

log = logging.getLogger("chainsout.reddit")

API = "https://arctic-shift.photon-reddit.com/api"
UA = "chainsout/0.12 (job feed reader)"
PAGE = 100

FIELD_RX = re.compile(r"^\W*([A-Za-z][A-Za-z /]{1,30}?)\W*:\W*(.+)$")
# Campos de la plantilla que nombran el puesto de pasada pero no son el título.
NOT_TITLE_RX = re.compile(r"\W*(contact|type|visa|compensation|estimated compensation)\W*:", re.I)
REMOTE_RX = re.compile(r"\bremote\b", re.I)


LINK_RX = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def plain(text: str) -> str:
    """Quita el markdown que estorba a los campos: escapes (\\*), enlaces [texto](url) y negritas."""
    text = re.sub(r"\\([*_\[\]()#>~`-])", r"\1", text)
    return LINK_RX.sub(r"\1", text).replace("**", "").replace("__", "")


def parse_fields(text: str) -> dict[str, str]:
    """Campos "CLAVE: valor" de la plantilla (COMPANY, TYPE, LOCATION, REMOTE...), en minúsculas."""
    out: dict[str, str] = {}
    for line in plain(text).splitlines():
        m = FIELD_RX.match(line.strip())
        if m:
            out.setdefault(m.group(1).strip().lower(), m.group(2).strip(" *_"))
    return out


def comment_to_job(c: dict, sub: str, keyword_rx: re.Pattern) -> Job | None:
    text = html.unescape(c.get("body") or "").strip()
    if not text or c.get("author") in (None, "[deleted]", "AutoModerator") \
            or not keyword_rx.search(text):
        return None
    f = parse_fields(text)
    company = f.get("company", "")[:80]
    title = next((f[k] for k in ("role", "position", "title", "job title") if f.get(k)), "")
    if not title:
        title = next((ln.strip(" *#_") for ln in plain(text).splitlines()
                      if ROLE_RX.search(ln) and len(ln) < 160
                      and not NOT_TITLE_RX.match(ln)), "")
    title = (title or f"Oferta en r/{sub} (ver descripción)")[:160]
    remote = f.get("remote", "")
    loc = " / ".join(x for x in (f.get("location", ""), remote and f"remote: {remote}") if x)
    is_remote = bool(REMOTE_RX.search(loc) or re.match(r"\s*yes", remote, re.I)) or None
    return Job(source="reddit", url=f"https://www.reddit.com{c['permalink']}", title=title,
               company=company, location=loc[:160], is_remote=is_remote,
               job_type=f.get("type", "")[:60], description=text,
               posted_at=datetime.fromtimestamp(c.get("created_utc", 0), timezone.utc)
               .strftime("%Y-%m-%d"))


def get(client: httpx.Client, path: str, params: dict) -> list[dict]:
    """GET a Arctic Shift con reintentos: a veces responde 422/429 "Timeout. Maybe slow down"."""
    for i in range(4):
        r = client.get(f"{API}/{path}", params=params)
        if r.status_code in (422, 429) or r.status_code >= 500:
            time.sleep(5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()["data"] or []
    r.raise_for_status()
    raise RuntimeError(f"Arctic Shift: {r.text[:200]}")


def find_threads(client: httpx.Client, sub: str, title_rx: re.Pattern, query: str,
                 grace_days: int, author: str | None = None) -> list[dict]:
    """Busca por autor si se conoce (barato en Arctic Shift); si no, o si no sale nada, por título
    (búsqueda de texto, que a menudo da timeout)."""
    base = {"subreddit": sub, "sort": "desc", "limit": 25}
    posts = []
    if author:
        posts = [p for p in get(client, "posts/search", {**base, "author": author})
                 if title_rx.search(p.get("title", ""))]
    if not posts:
        data = get(client, "posts/search", {**base, "title": query})
        posts = [p for p in data if title_rx.search(p.get("title", ""))]
    posts.sort(key=lambda p: p["created_utc"], reverse=True)
    if not posts:
        return []
    out = [posts[0]]
    age = datetime.now(timezone.utc).timestamp() - posts[0]["created_utc"]
    if age < grace_days * 86400 and len(posts) > 1:
        out.append(posts[1])
    return out


def top_comments(client: httpx.Client, post_id: str) -> list[dict]:
    """Comentarios de primer nivel del hilo, paginando por fecha."""
    out, after = [], None
    while True:
        params = {"link_id": post_id, "sort": "asc", "limit": PAGE}
        if after:
            params["after"] = after
        page = get(client, "comments/search", params)
        out += [c for c in page if str(c.get("parent_id", "")).startswith("t3_")]
        if len(page) < PAGE:
            return out
        after = int(page[-1]["created_utc"])


def fetch(cfg: dict, root: Path, stats: dict) -> list[Job]:
    keyword_rx = re.compile(cfg.get("keywords") or r".", re.I)
    grace = int(cfg.get("previous_thread_grace_days", 10))
    jobs: list[Job] = []
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as client:
        for t in cfg.get("threads", []):
            sub = t["subreddit"]
            title_rx = re.compile(t.get("title_regex") or r"hiring", re.I)
            for post in find_threads(client, sub, title_rx, t.get("query", "hiring"), grace,
                                     t.get("author")):
                stats["queries"] += 1
                kids = top_comments(client, post["id"])
                got = [j for j in (comment_to_job(c, sub, keyword_rx) for c in kids) if j]
                log.debug("%s: %d comentarios, %d candidatos", post["title"], len(kids), len(got))
                stats["comments"] = stats.get("comments", 0) + len(kids)
                jobs += got
    return jobs
