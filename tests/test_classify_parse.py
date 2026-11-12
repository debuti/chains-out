import json
import subprocess

import pytest

from chainsout import classify as C
from chainsout.models import Job, batch_output_schema


def jobs(*ids):
    return [Job(source="x", url=f"https://a/{i}", title="Firmware", id=i) for i in ids]


def v(ref, decision="match", score=8):
    return {"job_ref": str(ref), "decision": decision, "score": score, "hard_fails": [],
            "location_fit": "yes", "location_note": "remote UE", "domain_fit": "yes",
            "preferences_met": [],
            "salary": {"published": False, "min": None, "max": None, "currency": None,
                       "meets_threshold": "unknown"},
            "summary": "ok", "warnings": []}


def test_parse_batch_maps_refs():
    got = C.parse_batch({"verdicts": [v(1), v(2, "reject", 1), v(99)]}, jobs(1, 2))
    assert set(got) == {1, 2} and got[2].decision == "reject"


def test_parse_batch_invalid():
    bad = v(1)
    bad["score"] = 42
    with pytest.raises(C.ClassifyError):
        C.parse_batch({"verdicts": [bad]}, jobs(1))


def test_single_job_wrong_ref_accepted():
    got = C.parse_batch({"verdicts": [v("job-1")]}, jobs(1))
    assert list(got) == [1]


def test_parse_json_text_fenced():
    assert C._parse_json_text('bla ```json\n{"a": 1}\n``` fin') == {"a": 1}


def test_schema_is_strict():
    s = batch_output_schema()
    item = s["properties"]["verdicts"]["items"]
    assert "$defs" not in s and item["additionalProperties"] is False
    assert "salary" in item["required"] and "job_ref" in item["required"]


def test_prompt_renders_profile(profile):
    system, version = C.load_prompt(profile)
    assert version == "2" and "full_remote" in system
    assert not any(x in system for x in ("{profile}", "{bias}", "{score_guide}", "{edge_cases}"))


def test_cli_backend_structured_output(monkeypatch, profile):
    class S:
        llm_backend = "claude_cli"; llm_model = "m"; claude_bin = "claude"
        llm_timeout = 5; classify_batch = 5

    calls = []

    def fake_run(cmd, **kw):
        calls.append(kw["input"])
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "",
               "structured_output": {"verdicts": [v(1), v(2)]}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    verdicts, errors = C.Classifier(S, profile).classify(jobs(1, 2, 3))
    # 3 no vuelve: se reintenta solo esa oferta y luego queda en error
    assert set(verdicts) == {1, 2} and set(errors) == {3}
    assert len(calls) == 2 and 'job_ref="3"' in calls[-1]


def test_cli_backend_error(monkeypatch, profile):
    class S:
        llm_backend = "claude_cli"; llm_model = "m"; claude_bin = "claude"
        llm_timeout = 5; classify_batch = 5

    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, json.dumps({"is_error": True, "result": "rate limited"}), ""))
    clf = C.Classifier(S, profile)
    verdicts, errors = clf.classify(jobs(1))
    # cuota agotada: no es error de la oferta, para y queda en cola
    assert not verdicts and not errors and clf.unavailable.kind == "limit"

    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, json.dumps({"is_error": True, "result": "algo raro"}), ""))
    verdicts, errors = C.Classifier(S, profile).classify(jobs(1))
    assert not verdicts and "algo raro" in errors[1]


def test_legacy_verdict_still_loads():
    from chainsout.models import Verdict
    old = {"decision": "maybe", "score": 6, "remote_scope": "eu", "summary_es": "s",
           "warnings_es": ["w"], "salary": {"published": False, "estimate_over_100k": "likely"}}
    got = Verdict.model_validate(old)
    assert (got.summary, got.warnings, got.salary.meets_threshold) == ("s", ["w"], "likely")

