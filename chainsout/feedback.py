"""Feedback desde los botones inline de Telegram (polling corto con getUpdates, sin webhook)."""

from __future__ import annotations

import logging

from .db import DB
from .notify_telegram import Telegram, TelegramError

log = logging.getLogger("chainsout.feedback")

OFFSET_KEY = "telegram_update_offset"
PAUSED_KEY = "paused"
COMMANDS = [
    {"command": "pausar", "description": "Deja de buscar (p. ej. si ya tienes trabajo)"},
    {"command": "reanudar", "description": "Vuelve a buscar"},
    {"command": "estado", "description": "¿Está buscando o en pausa?"},
]


def is_paused(db: DB) -> bool:
    return db.kv_get(PAUSED_KEY) == "1"


def _command(db: DB, text: str) -> str | None:
    """Comandos de texto del dueño del bot. Devuelve la respuesta, o None si no es un comando."""
    cmd = (text or "").strip().split()[0].split("@")[0].lower() if (text or "").strip() else ""
    if cmd in ("/pausar", "/pausa", "/pause"):
        db.kv_set(PAUSED_KEY, "1")
        return "⏸ chainsout en pausa: no buscará ni avisará hasta /reanudar."
    if cmd in ("/reanudar", "/resume"):
        db.kv_set(PAUSED_KEY, "0")
        return "▶️ chainsout reanudado: vuelve a buscar en la próxima ejecución."
    if cmd in ("/estado", "/status", "/start"):
        return "⏸ En pausa (/reanudar para seguir)." if is_paused(db) else \
            "▶️ Buscando (/pausar para parar)."
    return None
VALUES = {"y": "interesa", "n": "no_interesa"}
ANSWERS = {"interesa": "👍 Guardado: te interesa", "no_interesa": "👎 Guardado: no te interesa"}


def parse_callback(data: str) -> tuple[int, str] | None:
    """callback_data = "fb:<job_id>:<y|n>"."""
    try:
        tag, jid, val = data.split(":")
        if tag == "fb" and val in VALUES:
            return int(jid), VALUES[val]
    except (ValueError, AttributeError):
        pass
    return None


def poll(db: DB, tg: Telegram, chat_id: str) -> int:
    """Procesa las pulsaciones pendientes. Devuelve cuántos feedbacks se han guardado."""
    offset = db.kv_get(OFFSET_KEY)
    updates = tg.get_updates(int(offset) if offset else None)
    saved = 0
    for u in updates:
        db.kv_set(OFFSET_KEY, str(u["update_id"] + 1))
        m = u.get("message")
        if m and str((m.get("chat") or {}).get("id")) == str(chat_id):
            reply = _command(db, m.get("text", ""))
            if reply:
                try:
                    tg.send(reply)
                except TelegramError:
                    pass
            continue
        cq = u.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue  # solo aceptamos al dueño del bot
        parsed = parse_callback(cq.get("data", ""))
        text = "Ya registrado" if cq.get("data") == "noop" else "No entiendo este botón"
        if parsed and db.get_job(parsed[0]):
            job_id, value = parsed
            db.save_feedback(job_id, value)
            saved += 1
            text = ANSWERS[value]
            _mark_message(tg, msg, value)
        try:
            tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=text)
        except TelegramError:
            pass  # las callback queries caducan; da igual si ya no se puede responder
    return saved


def _mark_message(tg: Telegram, msg: dict, value: str) -> None:
    """Añade al mensaje lo que elegiste y quita los botones.

    Se reutilizan el texto y las entidades (negrita, enlaces) que trae el callback; como lo
    nuevo va al final, los offsets de las entidades siguen valiendo.
    """
    label = "👍 Te interesa" if value == "interesa" else "👎 No te interesa"
    try:
        chat, mid = msg["chat"]["id"], msg["message_id"]
    except KeyError:
        return
    params = dict(chat_id=chat, message_id=mid, text=f"{msg.get('text', '')}\n\n{label}",
                  link_preview_options={"is_disabled": True})
    if msg.get("entities"):
        params["entities"] = msg["entities"]
    try:
        if not msg.get("text"):
            raise TelegramError("sin texto")
        tg.call("editMessageText", **params)  # sin reply_markup: desaparecen los botones
    except TelegramError:
        try:  # p. ej. mensaje ya editado: al menos quitar los botones
            tg.call("editMessageReplyMarkup", chat_id=chat, message_id=mid,
                    reply_markup={"inline_keyboard": []})
        except TelegramError:
            pass


def mark_pending(tg: Telegram, chat_id: str) -> int:
    """Marca ya los mensajes con 👍/👎 pulsado, sin base de datos ni confirmar las pulsaciones.

    Para que el mensaje cambie al momento aunque la ejecución completa (que guarda el
    feedback y confirma el offset) tarde horas. getUpdates sin offset no confirma nada, así
    que `poll` las vuelve a ver después; editar dos veces solo da un error que se ignora.
    """
    marked = 0
    for u in tg.get_updates(None):
        cq = u.get("callback_query") or {}
        msg = cq.get("message") or {}
        parsed = parse_callback(cq.get("data", ""))
        if not parsed or str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        value = parsed[1]
        _mark_message(tg, msg, value)
        marked += 1
        try:
            tg.call("answerCallbackQuery", callback_query_id=cq["id"], text=ANSWERS[value])
        except TelegramError:
            pass
    return marked
