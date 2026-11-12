"""Estadísticas (comando `stats`) y resumen semanal para Telegram."""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timedelta

from .db import DB


def build_stats(db: DB, days: int = 7) -> dict:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    q = db.conn.execute
    by_source = Counter({r[0]: r[1] for r in q(
        "SELECT source, COUNT(*) FROM jobs WHERE fetched_at >= ? GROUP BY source", (since,))})
    by_status = Counter({r[0]: r[1] for r in q(
        "SELECT status, COUNT(*) FROM jobs WHERE fetched_at >= ? GROUP BY status", (since,))})
    prefilter = Counter()
    for (reason,) in q("SELECT status_reason FROM jobs WHERE status = 'prefiltered' "
                       "AND fetched_at >= ?", (since,)):
        key = (reason or "?").split(":")[0]
        if key == "title":
            key = "título"
        prefilter[key] += 1
    decisions = Counter()
    hard_fails = Counter()
    import json

    for decision, reasons in q(
        "SELECT v.decision, v.reasons FROM verdicts v JOIN jobs j ON j.id = v.job_id "
        "WHERE v.id = (SELECT MAX(id) FROM verdicts WHERE job_id = j.id) AND v.created_at >= ?",
        (since,),
    ):
        decisions[decision] += 1
        if decision == "reject":
            for hf in json.loads(reasons or "{}").get("hard_fails", []) or ["(sin código)"]:
                hard_fails[hf] += 1
    notified = q("SELECT COUNT(*) FROM notifications WHERE sent_at >= ?", (since,)).fetchone()[0]
    fb = Counter({r[0]: r[1] for r in q(
        "SELECT value, COUNT(*) FROM feedback WHERE created_at >= ? GROUP BY value", (since,))})
    runs = q("SELECT COUNT(*) FROM runs WHERE started_at >= ?", (since,)).fetchone()[0]
    return dict(days=days, by_source=by_source, by_status=by_status, prefilter=prefilter,
                decisions=decisions, hard_fails=hard_fails, notified=notified, feedback=fb,
                runs=runs, total=sum(by_source.values()))


def _c(c: Counter, n: int = 8) -> str:
    return ", ".join(f"{k} {v}" for k, v in c.most_common(n)) or "-"


def format_stats_text(st: dict) -> str:
    return "\n".join([
        f"Últimos {st['days']} días ({st['runs']} ejecuciones)",
        f"Ofertas vistas: {st['total']} ({_c(st['by_source'])})",
        f"Estados: {_c(st['by_status'])}",
        f"Prefiltro: {_c(st['prefilter'])}",
        f"Clasificadas: {_c(st['decisions'])}",
        f"Motivos de descarte (LLM): {_c(st['hard_fails'])}",
        f"Notificadas: {st['notified']} · Feedback: {_c(st['feedback'])}",
    ])


def format_weekly_html(st: dict) -> str:
    e = html.escape
    return "\n".join([
        f"📊 <b>Resumen semanal de chainsout</b> ({st['runs']} ejecuciones)",
        f"👀 Vistas: {st['total']} ({e(_c(st['by_source']))})",
        f"🧹 Prefiltro: {st['by_status'].get('prefiltered', 0)} ({e(_c(st['prefilter'], 5))})",
        f"🔁 Duplicadas entre fuentes: {st['by_status'].get('duplicate', 0)}",
        f"🤖 Clasificadas: {sum(st['decisions'].values())} ({e(_c(st['decisions']))})",
        f"❌ Motivos de descarte: {e(_c(st['hard_fails'], 6))}",
        f"📨 Notificadas: {st['notified']} · 👍/👎: {e(_c(st['feedback']))}",
    ])
