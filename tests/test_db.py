from chainsout.models import Job, Salary, Verdict
from chainsout.normalize import normalize


def mk(url, title="Senior Firmware Engineer", company="Acme", source="linkedin"):
    return normalize(Job(source=source, url=url, title=title, company=company))


def verdict(decision="match", score=8):
    return Verdict(decision=decision, score=score, location_fit="yes", location_note="remote UE",
                   domain_fit="yes", preferences_met=["stack"],
                   salary=Salary(published=False, meets_threshold="unknown"), summary="ok")


def test_dedup_seen_and_cross_source(db):
    s1, id1 = db.insert_job(mk("https://www.linkedin.com/jobs/view/foo-123?trk=a"))
    s2, id2 = db.insert_job(mk("https://linkedin.com/jobs/view/123"))
    s3, id3 = db.insert_job(mk("https://indeed.com/viewjob?jk=9", company="ACME GmbH",
                               title="Sr. Firmware Engineer (m/w/d)", source="indeed"))
    s4, _ = db.insert_job(mk("https://indeed.com/viewjob?jk=10", title="Kernel Engineer"))
    assert (s1, s2, s3, s4) == ("new", "seen", "duplicate", "new")
    assert id2 == id1
    row = db.conn.execute("SELECT status, duplicate_of FROM jobs WHERE id=?", (id3,)).fetchone()
    assert row["status"] == "duplicate" and row["duplicate_of"] == id1


def test_pending_notifications_threshold(db):
    ids = []
    for i, (dec, sc) in enumerate([("match", 8), ("maybe", 6), ("maybe", 5), ("reject", 9)]):
        _, jid = db.insert_job(mk(f"https://x.com/{i}", title=f"Firmware {i}"))
        db.save_verdict(jid, verdict(dec, sc), "m", "1")
        ids.append(jid)
    got = [j.id for j, _ in db.pending_notifications(6)]
    assert got == [ids[0], ids[1]]
    db.mark_notified(ids[0], 1)
    db.mark_notified(ids[0], 2)  # idempotente
    assert [j.id for j, _ in db.pending_notifications(6)] == [ids[1]]


def test_kv(db):
    assert db.kv_get("a") is None
    db.kv_set("a", "1")
    db.kv_set("a", "2")
    assert db.kv_get("a") == "2"
