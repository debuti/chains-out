"""Hilos de ofertas de Reddit (p. ej. el "Who's Hiring" de r/rust, que se renueva con cada versión).

Por cada entrada de `threads` busca en el subreddit los posts cuyo título casa con `title_regex`,
lee el más reciente (y el anterior si salió hace menos de `previous_thread_grace_days`) y convierte
cada comentario de primer nivel en una oferta.

Reddit bloquea las peticiones anónimas: hace falta una app OAuth (tipo "script" o "web app") y las
variables REDDIT_CLIENT_ID y REDDIT_CLIENT_SECRET.
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..models import Job
from .hn_source import ROLE_RX

log = logging.getLogger("chainsout.reddit")

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
UA = "chainsout/0.12 (job feed reader)"

FIELD_RX = re.compile(r"^\W*([A-Za-z][A-Za-z /]{1,30}?)\W*:\W*(.+)$")
REMOTE_RX = re.compile(r"\bremote\b", re.I)


def token(client: httpx.Client) -> str:
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("faltan REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET")
    r = client.post(TOKEN_URL, auth=(cid, secret), data={"grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


def parse_fields(text: str) -> dict[str, str]:
    """Campos "CLAVE: valor" de la plantilla (COMPANY, TYPE, LOCATION, REMOTE...), en minúsculas."""
    out: dict[str, str] = {}
    for line in text.splitlines():
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
        title = next((ln.strip(" *#_") for ln in text.splitlines()
                      if ROLE_RX.search(ln) and len(ln) < 160), "")
    title = (title or f"Oferta en r/{sub} (ver descripción)")[:160]
    remote = f.get("remote", "")
    loc = " / ".join(x for x in (f.get("location", ""), remote and f"remote: {remote}") if x)
    is_remote = bool(REMOTE_RX.search(loc) or re.match(r"\s*yes", remote, re.I)) or None
    return Job(source="reddit", url=f"https://www.reddit.com{c['permalink']}", title=title,
               company=company, location=loc[:160], is_remote=is_remote,
               job_type=f.get("type", "")[:60], description=text,
               posted_at=datetime.fromtimestamp(c.get("created_utc", 0), timezone.utc)
               .strftime("%Y-%m-%d"))


def find_threads(client: httpx.Client, sub: str, title_rx: re.Pattern, query: str,
                 grace_days: int) -> list[dict]:
    r = client.get(f"{API}/r/{sub}/search", params={
        "q": query, "restrict_sr": 1, "sort": "new", "limit": 25, "raw_json": 1})
    r.raise_for_status()
    posts = [p["data"] for p in r.json()["data"]["children"]
             if title_rx.search(p["data"].get("title", ""))]
    posts.sort(key=lambda p: p["created_utc"], reverse=True)
    if not posts:
        return []
    out = [posts[0]]
    age = datetime.now(timezone.utc).timestamp() - posts[0]["created_utc"]
    if age < grace_days * 86400 and len(posts) > 1:
        out.append(posts[1])
    return out


def fetch(cfg: dict, root: Path, stats: dict) -> list[Job]:
    keyword_rx = re.compile(cfg.get("keywords") or r".", re.I)
    grace = int(cfg.get("previous_thread_grace_days", 10))
    jobs: list[Job] = []
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as client:
        client.headers["Authorization"] = f"Bearer {token(client)}"
        for t in cfg.get("threads", []):
            sub = t["subreddit"]
            title_rx = re.compile(t.get("title_regex") or r"hiring", re.I)
            for post in find_threads(client, sub, title_rx, t.get("query", "hiring"), grace):
                stats["queries"] += 1
                r = client.get(f"{API}/r/{sub}/comments/{post['id']}",
                               params={"depth": 1, "limit": 500, "sort": "new", "raw_json": 1})
                r.raise_for_status()
                kids = [k["data"] for k in r.json()[1]["data"]["children"] if k["kind"] == "t1"]
                got = [j for j in (comment_to_job(c, sub, keyword_rx) for c in kids) if j]
                log.debug("%s: %d comentarios, %d candidatos", post["title"], len(kids), len(got))
                stats["comments"] = stats.get("comments", 0) + len(kids)
                jobs += got
    return jobs
