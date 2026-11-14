import httpx

from chainsout import release_alerts


class KV(dict):
    def kv_get(self, k):
        return self.get(k)

    def kv_set(self, k, v):
        self[k] = v


def fake(tags):
    def handler(req):
        return httpx.Response(200, json=[{"tag_name": t, "draft": False, "prerelease": False}
                                         for t in tags])
    return handler


def test_check(monkeypatch):
    tags = ["1.91.1", "1.91.0"]
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(fake(tags)), **kw))
    db, sent = KV(), []
    alerts = [{"repo": "rust-lang/rust", "tag_regex": r"^\d+\.\d+\.0$", "message": "R {tag}"}]
    assert release_alerts.check(alerts, db, sent.append) == 0  # primera vez: memoriza
    assert db["release:rust-lang/rust"] == "1.91.0"
    assert release_alerts.check(alerts, db, sent.append) == 0
    tags.insert(0, "1.92.0")
    assert release_alerts.check(alerts, db, sent.append) == 1 and sent == ["R 1.92.0"]
