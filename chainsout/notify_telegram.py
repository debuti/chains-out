"""Telegram Bot API (sin librerías extra: httpx). Una oferta por mensaje."""

from __future__ import annotations

import html
import logging

import httpx

from .models import Job, Verdict

log = logging.getLogger("chainsout.telegram")

API = "https://api.telegram.org/bot{token}/{method}"

FIT_ES = {"yes": "✔", "no": "✘", "unclear": "?"}
EST_ES = {"likely": "probablemente llega al umbral", "unlikely": "probablemente no llega al umbral",
          "unknown": "desconocido"}


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str = "", timeout: float = 30):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN vacío")
        self.token = token
        self.chat_id = chat_id
        self.http = httpx.Client(timeout=timeout)

    def call(self, method: str, **params) -> dict | list:
        try:
            r = self.http.post(API.format(token=self.token, method=method), json=params)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # Nunca loguear la URL: contiene el token.
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', 'error')}")
        return data["result"]

    def send(self, text: str, reply_markup: dict | None = None) -> int:
        params = dict(chat_id=self.chat_id, text=text, parse_mode="HTML",
                      link_preview_options={"is_disabled": True})
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", **params)["message_id"]

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", **params)


def _money(x: float | None) -> str:
    if x is None:
        return "?"
    return f"{x / 1000:.0f}k" if x >= 1000 else f"{x:g}"


def salary_line(job: Job, v: Verdict) -> str:
    s = v.salary
    if s.published and (s.min or s.max):
        rng = _money(s.min) if s.min == s.max or not s.max else f"{_money(s.min)}–{_money(s.max)}"
        if not s.min:
            rng = f"hasta {_money(s.max)}"
        return f"{rng} {s.currency or ''} (publicado)".replace("  ", " ")
    return f"no publicado, {EST_ES[s.meets_threshold]} (estimado)"


def format_message(job: Job, v: Verdict) -> str:
    e = html.escape
    icon = "⭐" if v.preferences_met else "💼"
    head = f"{icon} <b>{e(job.title)}</b>"
    if job.company:
        head += f" — {e(job.company)}"
    head += f" (score {v.score}/10"
    if v.decision == "maybe":
        head += ", dudosa"
    head += ")"
    lines = [
        head,
        f"🌍 {e(v.location_note or 'ubicación dudosa')} {FIT_ES[v.location_fit]}"
        f" · 💶 {e(salary_line(job, v))}",
        f"✅ {e(v.summary)}",
    ]
    for w in v.warnings[:4]:
        lines.append(f"⚠️ {e(w)}")
    meta = " · ".join(x for x in (job.source, job.location) if x)
    if meta:
        lines.append(f"📍 {e(meta)}")
    lines.append(f"🔗 {e(job.url)}")
    return "\n".join(lines)


def feedback_keyboard(job_id: int | None) -> dict | None:
    """Botones 👍/👎; se recogen con getUpdates en la siguiente ejecución (sin webhook)."""
    if job_id is None:
        return None
    return {"inline_keyboard": [[
        {"text": "👍 Me interesa", "callback_data": f"fb:{job_id}:y"},
        {"text": "👎 No", "callback_data": f"fb:{job_id}:n"},
    ]]}
