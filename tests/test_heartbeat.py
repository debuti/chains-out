from types import SimpleNamespace

from chainsout import cli


def test_heartbeat_once_a_day(db, monkeypatch):
    sent = []

    class TG:
        def __init__(self, *a):
            pass

        def send(self, text):
            sent.append(text)

    monkeypatch.setattr("chainsout.notify_telegram.Telegram", TG)
    s = SimpleNamespace(telegram_token="t", telegram_chat_id="1")
    cli._maybe_heartbeat(s, db)
    cli._maybe_heartbeat(s, db)
    assert len(sent) == 1 and "sigue vivo" in sent[0]
