"""Claude sin uso o con token rechazado: se para, avisa una vez y no gasta intentos."""
import json
from dataclasses import replace

import pytest

from chainsout import classify as C
from chainsout import pipeline as P
from chainsout.config import load_settings
from tests.test_db import mk, verdict


@pytest.mark.parametrize("msg,kind", [
    ("claude error: Claude AI usage limit reached|1762963200", "limit"),
    ("claude error: You've hit your limit · resets 3pm (Europe/Madrid)", "limit"),
    ("claude error: Invalid bearer token (401)", "auth"),
    ("claude rc=1: JSON inválido", None),
])
def test_unavailable_kind(msg, kind):
    assert C.unavailable_kind(msg) == kind


@pytest.fixture
def setup(db, monkeypatch, tmp_path):
    s = replace(load_settings(tmp_path), classify_batch=2, telegram_token="", telegram_chat_id="")
    ids = [db.insert_job(mk(f"https://example.com/j/{i}", company=f"Co{i}"))[1] for i in range(5)]
    for i in ids:
        db.set_status(i, "pending")
    sent = []
    monkeypatch.setattr(P, "_tg_text", lambda s, text: sent.append(text))
    monkeypatch.setattr(C, "load_prompt", lambda profile: ("sys", "v"))
    return s, ids, sent


def test_limit_stops_and_notifies_once(db, setup, monkeypatch):
    s, ids, sent = setup
    calls = []

    def fake(self, jobs):
        calls.append(len(jobs))
        if len(calls) == 1:
            return {"verdicts": [dict(verdict().model_dump(), job_ref=j.id) for j in jobs]}
        C._check_unavailable("claude error: Claude AI usage limit reached|1000000000")
    monkeypatch.setattr(C.Classifier, "_call", fake)
    monkeypatch.setattr(C, "parse_batch",
                        lambda data, jobs: {j.id: verdict() for j in jobs} if data else {})

    c = P.classify_pending(db, s, {})
    assert calls == [2, 2]  # primer lote ok, segundo para en seco: sin reintentos ni uno a uno
    assert c["classified"] == 2 and c["error"] == 0 and c["unavailable_limit"] == 1
    assert len(sent) == 1 and "sin uso" in sent[0]
    assert db.conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0] == 3

    # reset ya pasado: se reintenta; vuelve a fallar → no repite el aviso
    P.classify_pending(db, s, {})
    assert len(sent) == 1
    state = json.loads(db.kv_get(P.UNAVAILABLE_KEY))
    assert state["kind"] == "limit"

    # vuelve: clasifica y avisa
    monkeypatch.setattr(C.Classifier, "_call", lambda self, jobs: {"ok": 1})
    c = P.classify_pending(db, s, {})
    assert c["classified"] == 3 and "vuelve" in sent[-1] and len(sent) == 2


def test_sleeps_until_reset(db, setup, monkeypatch):
    s, ids, sent = setup
    db.kv_set(P.UNAVAILABLE_KEY, json.dumps({"kind": "limit", "until": 4102444800}))
    monkeypatch.setattr(C.Classifier, "_call", lambda self, jobs: pytest.fail("no debe llamar"))
    c = P.classify_pending(db, s, {})
    assert c["sleeping"] == 5 and not sent


def test_old_auth_errors_do_not_burn_attempts(db, setup, monkeypatch):
    s, ids, sent = setup
    db.set_status(ids[0], "error", "attempt 3: claude error: Invalid bearer token (401)")
    db.set_status(ids[1], "error", "attempt 3: claude rc=1: basura")
    monkeypatch.setattr(C.Classifier, "_call", lambda self, jobs: {"ok": 1})
    monkeypatch.setattr(C, "parse_batch", lambda data, jobs: {j.id: verdict() for j in jobs})
    c = P.classify_pending(db, s, {})
    assert c["classified"] == 4  # la de 401 se reintenta; la de 3 intentos reales, no
