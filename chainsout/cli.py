"""CLI: chainsout run | fetch | classify | notify | stats | feedback | telegram-setup."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from typing import Optional

import typer

from . import pipeline
from .config import load_settings, load_yaml
from .db import DB

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Busca ofertas de trabajo que encajen con tu perfil y avisa por Telegram.")
log = logging.getLogger("chainsout")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # JobSpy y httpx son muy habladores; además httpx loguea URLs (con el token de Telegram).
    for name in ("JobSpy", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
    for name in list(logging.root.manager.loggerDict):
        if name.lower().startswith("jobspy"):
            logging.getLogger(name).setLevel(logging.ERROR)


def _ctx():
    s = load_settings()
    return s, DB(s.db_path), load_yaml(s.root / "profile.yaml")


def _fmt(c: Counter | dict) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(dict(c).items())) or "-"


def _log_sources(stats: dict) -> None:
    for src, st in stats.items():
        log.info("fuente %-10s %s", src, _fmt(st))


def _print_table(pending) -> None:
    if not pending:
        typer.echo("(nada que notificar)")
        return
    typer.echo(f"{'score':>5} {'dec':5} {'fuente':8} {'empresa':22} título")
    for job, v in pending:
        typer.echo(f"{v.score:>5} {v.decision:5} {job.source[:8]:8} "
                   f"{(job.company or '')[:22]:22} {job.title[:60]}")
        typer.echo(f"{'':>20}{v.summary[:110]}")
        typer.echo(f"{'':>20}{job.url}")


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="No envía nada a Telegram."),
    limit: Optional[int] = typer.Option(None, help="Máximo de ofertas a clasificar."),
    source: Optional[list[str]] = typer.Option(None, "--source", "-s",
                                               help="Solo estas fuentes (repetible)."),
    verbose: bool = typer.Option(False, "-v"),
):
    """Secuencia completa: fetch → dedup → prefiltro → clasificación → Telegram."""
    _setup_logging(verbose)
    s, db, profile = _ctx()
    run_id = db.start_run(dry_run)
    stats: dict = {"sources": {}}
    if not dry_run:
        _poll_feedback(s, db)
    if _paused(db):
        db.finish_run(run_id, {"paused": True})
        return
    jobs = pipeline.fetch_sources(s, stats["sources"], set(source) if source else None, db)
    _log_sources(stats["sources"])
    stats["ingest"] = pipeline.ingest(db, jobs)
    stats["prefilter"] = pipeline.prefilter_new(db, profile)
    stats["classify"] = pipeline.classify_pending(db, s, profile, limit)
    notif, pending = pipeline.notify_pending(db, s, dry_run)
    stats["notify"] = notif
    log.info("resumen: descargadas=%d nuevas=%d duplicadas=%d prefiltradas=%d clasificadas=%d "
             "(match=%d maybe=%d reject=%d error=%d) notificadas=%d%s",
             len(jobs), stats["ingest"]["new"], stats["ingest"]["duplicate"],
             stats["prefilter"]["prefiltered"], stats["classify"]["classified"],
             stats["classify"]["match"], stats["classify"]["maybe"],
             stats["classify"]["reject"], stats["classify"]["error"],
             notif["notified"], f" sin_enviar={notif['unsent']}" if notif["unsent"] else "")
    if dry_run:
        _print_table(pending)
    else:
        _maybe_weekly_summary(s, db)
    db.finish_run(run_id, {k: dict(v) if isinstance(v, Counter) else v for k, v in stats.items()})


@app.command()
def fetch(source: Optional[list[str]] = typer.Option(None, "--source", "-s"),
          verbose: bool = typer.Option(False, "-v")):
    """Solo descarga, deduplica y prefiltra (no gasta cuota del LLM)."""
    _setup_logging(verbose)
    s, db, profile = _ctx()
    if _paused(db):
        return
    run_id = db.start_run(False)
    stats: dict = {}
    jobs = pipeline.fetch_sources(s, stats, set(source) if source else None, db)
    _log_sources(stats)
    ing, pre = pipeline.ingest(db, jobs), pipeline.prefilter_new(db, profile)
    log.info("ingest: %s", _fmt(ing))
    log.info("prefiltro: %s", _fmt(pre))
    db.finish_run(run_id, {"sources": stats, "ingest": dict(ing), "prefilter": dict(pre)})


@app.command()
def classify(limit: Optional[int] = typer.Option(None), verbose: bool = typer.Option(False, "-v")):
    """Clasifica las ofertas pendientes (y reintenta las que dieron error)."""
    _setup_logging(verbose)
    s, db, profile = _ctx()
    if _paused(db):
        return
    pipeline.prefilter_new(db, profile)
    c = pipeline.classify_pending(db, s, profile, limit)
    log.info("clasificación: %s", _fmt(c))
    if c["error"] and not c["classified"] and not any(k.startswith("unavailable") for k in c):
        # Todo falla: lo normal es que el token de Claude haya caducado o no esté.
        log.error("el clasificador falla en todas las ofertas: ¿token/sesión de Claude caducado?")
        raise typer.Exit(2)


@app.command()
def notify(dry_run: bool = typer.Option(False, "--dry-run"),
           weekly: bool = typer.Option(False, "--weekly",
                                       help="Y el resumen semanal si toca (como `run`).")):
    """Envía a Telegram las ofertas clasificadas que superan el umbral y no se han enviado."""
    _setup_logging()
    s, db, _ = _ctx()
    if not dry_run and _paused(db):
        return
    c, pending = pipeline.notify_pending(db, s, dry_run)
    if dry_run:
        _print_table(pending)
    else:
        log.info("notificación: %s", _fmt(c))
        if weekly:
            _maybe_weekly_summary(s, db)


@app.command()
def stats(days: int = typer.Option(7, help="Ventana en días.")):
    """Resumen de la base de datos."""
    s, db, _ = _ctx()
    from .summary import build_stats, format_stats_text

    typer.echo(format_stats_text(build_stats(db, days)))


@app.command()
def show(job_id: int):
    """Muestra una oferta y su último veredicto."""
    s, db, _ = _ctx()
    job = db.get_job(job_id)
    if not job:
        raise typer.Exit(f"no existe la oferta {job_id}")
    row = db.conn.execute("SELECT status, status_reason, duplicate_of FROM jobs WHERE id = ?",
                          (job_id,)).fetchone()
    typer.echo(f"[{job.id}] {job.title} — {job.company} ({job.source}, {job.location})")
    typer.echo(f"estado: {row['status']} {row['status_reason'] or ''}"
               f"{' dup_de=' + str(row['duplicate_of']) if row['duplicate_of'] else ''}")
    typer.echo(job.url)
    v = db.latest_verdict(job_id)
    if v:
        typer.echo(json.dumps(v.model_dump(), ensure_ascii=False, indent=1))


@app.command()
def feedback(job_id: int, value: str = typer.Argument(..., help="interesa | no_interesa"),
             note: Optional[str] = typer.Option(None)):
    """Registra feedback a mano (además de los botones de Telegram)."""
    if value not in ("interesa", "no_interesa"):
        raise typer.BadParameter("valor: interesa | no_interesa")
    s, db, _ = _ctx()
    if not db.get_job(job_id):
        raise typer.Exit(f"no existe la oferta {job_id}")
    db.save_feedback(job_id, value, note)
    typer.echo("ok")


@app.command("telegram-setup")
def telegram_setup():
    """Muestra el chat_id: escribe antes cualquier mensaje a tu bot."""
    from .notify_telegram import Telegram, TelegramError

    s = load_settings()
    try:
        tg = Telegram(s.telegram_token)
        me = tg.call("getMe")
        from .feedback import COMMANDS
        tg.call("setMyCommands", commands=COMMANDS)
        updates = tg.get_updates()
    except TelegramError as e:
        raise typer.Exit(f"error: {e}")
    typer.echo(f"Bot: @{me.get('username')}")
    chats = {}
    for u in updates:
        msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not chats:
        typer.echo("No hay mensajes. Escribe algo a tu bot en Telegram y repite el comando.")
        return
    for cid, name in chats.items():
        typer.echo(f"TELEGRAM_CHAT_ID={cid}   ({name})")
    typer.echo("Copia la línea en .env.")


def _poll_feedback(s, db) -> int:
    """Recoge las pulsaciones de 👍/👎 pendientes. Nunca hace fallar la ejecución."""
    if not (s.telegram_token and s.telegram_chat_id):
        return 0
    from .feedback import poll
    from .notify_telegram import Telegram, TelegramError

    try:
        n = poll(db, Telegram(s.telegram_token, s.telegram_chat_id), s.telegram_chat_id)
    except TelegramError as e:
        log.warning("feedback: %s", e)
        return 0
    if n:
        log.info("feedback: %d respuestas guardadas", n)
    return n


def _paused(db) -> bool:
    """Pausa por /pausar en Telegram o por CHAINSOUT_ENABLED=false."""
    from .feedback import is_paused

    if os.environ.get("CHAINSOUT_ENABLED", "true").strip().lower() in ("false", "0", "no", "off"):
        log.info("desactivado (CHAINSOUT_ENABLED)")
        return True
    if is_paused(db):
        log.info("en pausa (/reanudar en Telegram para seguir)")
        return True
    return False


WEEKLY_KEY = "weekly_summary_sent_at"


def _maybe_weekly_summary(s, db) -> None:
    """Envía el resumen semanal si han pasado 7 días desde el último (o nunca se envió)."""
    from datetime import datetime, timedelta

    last = db.kv_get(WEEKLY_KEY)
    if last is None:  # primera ejecución: empezamos a contar desde hoy
        db.kv_set(WEEKLY_KEY, datetime.now().isoformat(timespec="seconds"))
        return
    if datetime.now() - datetime.fromisoformat(last) >= timedelta(days=7):
        if _send_summary(s, db, 7):
            db.kv_set(WEEKLY_KEY, datetime.now().isoformat(timespec="seconds"))


def _send_summary(s, db, days: int) -> bool:
    from .notify_telegram import Telegram, TelegramError
    from .summary import build_stats, format_weekly_html

    if not (s.telegram_token and s.telegram_chat_id):
        return False
    try:
        Telegram(s.telegram_token, s.telegram_chat_id).send(format_weekly_html(build_stats(db, days)))
        return True
    except TelegramError as e:
        log.warning("resumen: %s", e)
        return False


@app.command()
def summary(days: int = typer.Option(7), send: bool = typer.Option(False, "--send",
                                                                   help="Enviarlo a Telegram.")):
    """Resumen de los últimos días: vistas, descartadas y por qué, notificadas, feedback."""
    _setup_logging()
    s, db, _ = _ctx()
    from .summary import build_stats, format_stats_text

    if send:
        typer.echo("enviado" if _send_summary(s, db, days) else "no enviado (¿Telegram sin configurar?)")
    else:
        typer.echo(format_stats_text(build_stats(db, days)))


@app.command("poll-feedback")
def poll_feedback():
    """Recoge ahora las pulsaciones de 👍/👎 (run ya lo hace en cada ejecución)."""
    _setup_logging()
    s, db, _ = _ctx()
    typer.echo(f"{_poll_feedback(s, db)} respuestas nuevas")


@app.command()
def daemon(every: int = typer.Option(3, help="Cada cuántas horas."),
           start: int = typer.Option(8, help="Primera hora del día."),
           end: int = typer.Option(22, help="Última hora del día."),
           tz: str = typer.Option(os.environ.get("TZ", "UTC")),
           now: bool = typer.Option(False, "--now", help="Ejecutar una vez al arrancar.")):
    """Alternativa a cron: ejecuta `run` cada N horas entre START y END (hora local de TZ)."""
    _setup_logging()
    from .scheduler import loop

    loop(every, start, end, tz, now, [])


def main() -> None:
    app()


if __name__ == "__main__":
    main()
