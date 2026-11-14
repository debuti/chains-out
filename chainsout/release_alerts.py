"""Avisos por Telegram cuando un repo de GitHub publica una versión nueva.

Sirve de recordatorio para fuentes que no se pueden leer solas: p. ej. r/rust abre un hilo
"Who's hiring" nuevo con cada versión de Rust, y la API de Reddit ya no da acceso a apps nuevas.

searches.yaml:
  release_alerts:
    - repo: rust-lang/rust
      tag_regex: '^\\d+\\.\\d+\\.0$'   # solo versiones x.y.0
      message: "Rust {tag} publicado: ..."
"""

from __future__ import annotations

import logging
import os
import re

import httpx

log = logging.getLogger("chainsout.releases")


def latest_tag(client: httpx.Client, repo: str, tag_rx: re.Pattern) -> str | None:
    r = client.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 20})
    r.raise_for_status()
    for rel in r.json():  # la API los da del más nuevo al más viejo
        if not rel.get("draft") and not rel.get("prerelease") and tag_rx.search(rel["tag_name"]):
            return rel["tag_name"]
    return None


def check(alerts: list[dict], db, send) -> int:
    """Llama a send(texto) por cada versión nueva. La primera vez solo memoriza. Devuelve avisos."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "chainsout"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    sent = 0
    with httpx.Client(timeout=20, headers=headers) as client:
        for a in alerts or []:
            repo = a["repo"]
            try:
                tag = latest_tag(client, repo, re.compile(a.get("tag_regex") or "."))
            except Exception as e:  # noqa: BLE001
                log.warning("release %s: %s", repo, e)
                continue
            key = f"release:{repo}"
            prev = db.kv_get(key)
            if tag and tag != prev:
                if prev is not None:
                    send((a.get("message") or "🆕 {repo} {tag}").format(repo=repo, tag=tag))
                    sent += 1
                db.kv_set(key, tag)
    return sent
