"""~15 ofertas etiquetadas a mano contra el LLM real. Solo con `pytest --llm` (gasta cuota).

Imprime un informe de aciertos (usa -s para verlo) y falla si baja del 80 %.
"""

import pytest

from chainsout.classify import Classifier
from chainsout.config import load_settings
from tests.conftest import job_from, load_jobs


@pytest.mark.llm
def test_labelled_accuracy(profile):
    s = load_settings()
    data = load_jobs("labelled_jobs.json")
    jobs = []
    for i, d in enumerate(data, 1):
        j = job_from(d)
        j.id = i
        jobs.append(j)
    verdicts, errors = Classifier(s, profile).classify(jobs)
    ok = 0
    lines = []
    for d, j in zip(data, jobs):
        v = verdicts.get(j.id)
        if not v:
            lines.append(f"ERR  {j.title[:45]:45} {errors.get(j.id)}")
            continue
        positive = v.decision == "match" or (v.decision == "maybe" and v.score >= s.umbral_maybe)
        got = "positive" if positive else "reject"
        hit = got == d["expected"]
        ok += hit
        lines.append(f"{'OK ' if hit else 'BAD'}  {j.title[:45]:45} esperado={d['expected']:8} "
                     f"→ {v.decision}/{v.score} {','.join(v.hard_fails)}")
    report = f"Aciertos: {ok}/{len(data)} (modelo {s.llm_model})\n" + "\n".join(lines)
    print("\n" + report)
    (s.root / ".pytest_cache").mkdir(exist_ok=True)
    (s.root / ".pytest_cache" / "llm_report.txt").write_text(report)
    assert ok / len(data) >= 0.8, report
