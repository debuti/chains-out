from datetime import datetime
from zoneinfo import ZoneInfo

from chainsout import feedback as F
from chainsout.models import Job
from chainsout.normalize import normalize
from chainsout.notify_telegram import feedback_keyboard, format_message
from chainsout.scheduler import next_slot
from tests.test_db import verdict


class FakeTG:
    def __init__(self, updates):
        self.updates, self.calls = updates, []

    def get_updates(self, offset=None, timeout=0):
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def call(self, method, **kw):
        self.calls.append((method, kw))
        return True


def cb(uid, data, chat=42):
    return {"update_id": uid, "callback_query": {
        "id": f"q{uid}", "data": data,
        "message": {"message_id": 7, "chat": {"id": chat}}}}


def test_parse_callback():
    assert F.parse_callback("fb:12:y") == (12, "interesa")
    assert F.parse_callback("fb:12:n") == (12, "no_interesa")
    assert F.parse_callback("fb:x:y") is None and F.parse_callback("noop") is None


def test_keyboard_roundtrip():
    kb = feedback_keyboard(5)
    datas = [b["callback_data"] for b in kb["inline_keyboard"][0]]
    assert [F.parse_callback(d) for d in datas] == [(5, "interesa"), (5, "no_interesa")]


def test_poll_saves_and_advances_offset(db):
    _, jid = db.insert_job(normalize(Job(source="x", url="https://a/1", title="FW", company="A")))
    tg = FakeTG([cb(100, f"fb:{jid}:y"), cb(101, f"fb:{jid}:n", chat=666),
                 {"update_id": 102, "message": {"text": "hola"}}])
    assert F.poll(db, tg, "42") == 1  # el de otro chat se ignora
    assert db.kv_get(F.OFFSET_KEY) == "103"
    assert db.conn.execute("SELECT value FROM feedback").fetchall()[0][0] == "interesa"
    assert any(m == "editMessageReplyMarkup" for m, _ in tg.calls)
    assert F.poll(db, tg, "42") == 0  # nada nuevo


def test_format_message_escapes_html():
    j = Job(source="linkedin", url="https://x/?a=1&b=2", title="Firmware <Rust> Eng",
            company="A&B", location="Spain")
    txt = format_message(j, verdict())
    assert "&lt;Rust&gt;" in txt and "A&amp;B" in txt and "score 8/10" in txt


def test_next_slot():
    tz = ZoneInfo("Europe/Madrid")
    assert next_slot(datetime(2026, 11, 12, 9, 30, tzinfo=tz), 3, 8, 22).hour == 11
    t = next_slot(datetime(2026, 11, 12, 22, 0, 1, tzinfo=tz), 3, 8, 22)
    assert (t.day, t.hour) == (13, 8)


def test_pause_commands(tmp_path):
    from chainsout.db import DB
    from chainsout.feedback import _command, is_paused
    db = DB(tmp_path / "t.db")
    assert not is_paused(db)
    assert "pausa" in _command(db, "/pausar")
    assert is_paused(db)
    assert "Pausa" in _command(db, "/estado") or "pausa" in _command(db, "/estado")
    _command(db, "/reanudar@chainsoutbot")
    assert not is_paused(db)
    assert _command(db, "hola") is None
