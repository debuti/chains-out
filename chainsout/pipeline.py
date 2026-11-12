"""Pasos del pipeline: fetch → normalize/dedup → prefilter → classify → notify."""

from __future__ import annotations

import logging
from collections import Counter

import json
import time

from .classify import Classifier, unavailable_kind
from .config import Settings, load_yaml
from .db import DB
from .models import Job
from .normalize import normalize
from .prefilter import Prefilter

log = logging.getLogger("chainsout")

MAX_ATTEMPTS = 3


def fetch_sources(s: Settings, stats: dict, only: set[str] | None = None,
                  db: DB | None = None) -> list[Job]:
    """Descarga de todas las fuentes activas. Una fuente rota no para las demás."""
    cfg = load_yaml(s.root / "searches.yaml")
    srcs = cfg.get("sources", {})
    jobs: list[Job] = []

    def want(name: str) -> bool:
        return (only is None or name in only) and srcs.get(name, {}).get("enabled", False)

    jobspy_sites = [n for n in ("linkedin", "indeed", "google") if want(n)]
    if jobspy_sites:
        from .sources import jobspy_source

        sub = dict(cfg)
        sub["sources"] = {k: v for k, v in srcs.items() if k in jobspy_sites}
        try:
            jobs += jobspy_source.fetch(sub, stats)
        except Exception as e:  # noqa: BLE001
            log.warning("jobspy: %s", e)
            stats.setdefault("jobspy", {})["errors"] = 1
    for name, mod in _extra_sources():
        if want(name):
            st = stats.setdefault(name, {"queries": 0, "jobs": 0, "errors": 0})
            try:
                extra = {"db": db} if name == "boards" else {}
                got = mod.fetch(srcs[name], s.root, st, **extra)
                st["jobs"] += len(got)
                jobs += got
            except Exception as e:  # noqa: BLE001
                st["errors"] += 1
                log.warning("%s: %s", name, str(e).splitlines()[0][:200] if str(e) else e)
    return jobs


def _extra_sources():
    """Fuentes con interfaz fetch(cfg, root, stats) -> list[Job]."""
    out = []
    try:
        from .sources import hn_source
        out.append(("hn", hn_source))
    except ImportError:
        pass
    try:
        from .sources import ats_source
        out.append(("companies", ats_source))
    except ImportError:
        pass
    try:
        from .sources import boards_source
        out.append(("boards", boards_source))
    except ImportError:
        pass
    return out


def ingest(db: DB, jobs: list[Job]) -> Counter:
    c: Counter = Counter()
    for job in jobs:
        if not job.url or not job.title:
            c["invalid"] += 1
            continue
        normalize(job)
        status, _ = db.insert_job(job)
        c[status] += 1
    return c


def prefilter_new(db: DB, profile: dict) -> Counter:
    pf = Prefilter(profile)
    c: Counter = Counter()
    for job in db.jobs_by_status("new"):
        reason = pf.check(job)
        if reason:
            db.set_status(job.id, "prefiltered", reason)
            c["prefiltered"] += 1
        else:
            db.set_status(job.id, "pending")
            c["pending"] += 1
    # Por si la lista de empresas excluidas cambió con ofertas ya en cola.
    for job in db.jobs_by_status("pending"):
        if reason := pf.company_excluded(job):
            db.set_status(job.id, "prefiltered", reason)
            c["prefiltered"] += 1
            c["pending"] -= 1
    return c


def _attempts(job_reason: str | None) -> int:
    if job_reason and job_reason.startswith("attempt "):
        try:
            return int(job_reason.split()[1].rstrip(":"))
        except (IndexError, ValueError):
            return 0
    return 0


def classify_pending(db: DB, s: Settings, profile: dict, limit: int | None = None) -> Counter:
    c: Counter = Counter()
    rows = db.conn.execute(
        "SELECT id, status_reason FROM jobs WHERE status IN ('pending', 'error') ORDER BY id"
    ).fetchall()
    # Los errores de cuota/credenciales no son culpa de la oferta: no gastan intentos.
    todo_ids = [r["id"] for r in rows
                if r["status_reason"] is None or unavailable_kind(r["status_reason"])
                or _attempts(r["status_reason"]) < MAX_ATTEMPTS]
    if limit is not None:
        todo_ids = todo_ids[:limit]
    if not todo_ids:
        return c
    state = _unavailable_state(db)
    if state.get("until") and time.time() < state["until"]:
        log.info("Claude sin uso hasta %s: no se clasifica",
                 time.strftime("%H:%M UTC", time.gmtime(state["until"])))
        c["sleeping"] = len(todo_ids)
        return c
    reasons = {r["id"]: r["status_reason"] for r in rows}
    jobs = [db.get_job(i) for i in todo_ids]
    clf = Classifier(s, profile)
    verdicts, errors = clf.classify(jobs)
    _update_unavailable(db, s, state, clf.unavailable, bool(verdicts))
    if clf.unavailable:
        c["unavailable_" + clf.unavailable.kind] = 1
        c["queued"] = len(jobs) - len(verdicts) - len(errors)
    for jid, v in verdicts.items():
        db.save_verdict(jid, v, s.llm_model, clf.prompt_version)
        c["classified"] += 1
        c[v.decision] += 1
    for jid, err in errors.items():
        prev = reasons.get(jid)
        n = (0 if unavailable_kind(prev) else _attempts(prev)) + 1
        db.set_status(jid, "error", f"attempt {n}: {err[:300]}")
        c["error"] += 1
    return c


UNAVAILABLE_KEY = "claude_unavailable"
UNAVAILABLE_MSG = {
    "limit": "😴 Claude se ha quedado sin uso disponible{reset}. chainsout sigue buscando ofertas "
             "y las clasifica cuando vuelva; no hace falta hacer nada.",
    "auth": "🔑 Claude rechaza el token de chainsout. Las ofertas quedan en cola. Renuévalo: "
            "claude setup-token → gh secret set CLAUDE_CODE_OAUTH_TOKEN.",
}


def _unavailable_state(db: DB) -> dict:
    try:
        return json.loads(db.kv_get(UNAVAILABLE_KEY) or "{}")
    except ValueError:
        return {}


def _update_unavailable(db: DB, s: Settings, state: dict, err, recovered: bool) -> None:
    """Avisa una sola vez al entrar en «Claude no disponible» y otra al salir."""
    if err is None:
        if state and recovered:
            db.kv_set(UNAVAILABLE_KEY, "{}")
            _tg_text(s, "✅ Claude vuelve a estar disponible: chainsout clasifica lo que había en cola.")
        return
    until = err.reset_at
    if until is None and err.kind == "limit":
        until = int(time.time()) + 2 * 3600  # sin hora de reset: se reintenta en un par de horas
    if state.get("kind") != err.kind:
        reset = (" hasta las " + time.strftime("%H:%M UTC", time.gmtime(err.reset_at))
                 if err.reset_at else "")
        _tg_text(s, UNAVAILABLE_MSG[err.kind].format(reset=reset))
    db.kv_set(UNAVAILABLE_KEY, json.dumps({"kind": err.kind, "until": until,
                                           "since": state.get("since") or int(time.time())}))


def _tg_text(s: Settings, text: str) -> None:
    from .notify_telegram import Telegram, TelegramError

    if not (s.telegram_token and s.telegram_chat_id):
        log.warning("%s", text)
        return
    try:
        Telegram(s.telegram_token, s.telegram_chat_id).send(text)
    except TelegramError as e:
        log.warning("telegram: %s", e)


def notify_pending(db: DB, s: Settings, dry_run: bool = False) -> tuple[Counter, list]:
    """Envía a Telegram lo pendiente. En dry-run devuelve la lista sin enviar."""
    from .notify_telegram import Telegram, TelegramError, format_message, feedback_keyboard

    c: Counter = Counter()
    pending = db.pending_notifications(s.umbral_maybe)
    if dry_run or not pending:
        return c, pending
    if not (s.telegram_token and s.telegram_chat_id):
        log.warning("Telegram sin configurar: %d ofertas pendientes de enviar", len(pending))
        c["unsent"] = len(pending)
        return c, pending
    tg = Telegram(s.telegram_token, s.telegram_chat_id)
    for job, v in pending:
        try:
            mid = tg.send(format_message(job, v), feedback_keyboard(job.id))
            db.mark_notified(job.id, mid)
            c["notified"] += 1
        except TelegramError as e:
            log.warning("telegram: %s", e)
            c["unsent"] += 1
    return c, pending
