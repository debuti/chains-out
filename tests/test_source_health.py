from chainsout import pipeline
from chainsout.db import DB


def test_alert_once_and_recovery(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "_tg_text", lambda s, t: sent.append(t))
    db = DB(tmp_path / "x.db")
    bad = {"reddit": {"queries": 0, "jobs": 0, "errors": 1},
           "companies": {"queries": 9, "jobs": 5, "errors": 1}}
    pipeline.check_source_health(db, None, bad)
    pipeline.check_source_health(db, None, bad)
    assert len(sent) == 1 and "reddit" in sent[0]
    pipeline.check_source_health(db, None, {"reddit": {"queries": 1, "jobs": 0, "errors": 0}})
    assert len(sent) == 2 and "vuelve" in sent[1]
