"""Clasificador LLM: juzga ofertas contra profile.yaml y devuelve Verdicts validados.

Backends:
- claude_cli (por defecto): `claude -p` headless con la suscripción de Claude Code.
- anthropic_api: SDK oficial con ANTHROPIC_API_KEY (opcional: `uv sync --extra api`).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from pydantic import ValidationError

from .models import BatchOutput, Job, Verdict, batch_output_schema

log = logging.getLogger("chainsout.classify")

PROMPT_PATH = Path(__file__).parent / "prompts" / "classify.md"
MAX_DESC_CHARS = 9000


class ClassifyError(RuntimeError):
    pass


class ClaudeUnavailable(ClassifyError):
    """Claude no atiende: sin uso disponible (kind="limit") o token rechazado (kind="auth").
    No es culpa de las ofertas: se para el lote entero y se reintenta más tarde."""

    def __init__(self, msg: str, kind: str, reset_at: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.reset_at = reset_at


_LIMIT_RE = re.compile(r"usage limit|limit reached|hit your limit|rate.?limit|\b429\b|"
                       r"out of (extra )?usage|credit balance is too low", re.I)
_AUTH_RE = re.compile(r"\b401\b|invalid bearer|invalid api key|authenticat|oauth token|"
                      r"/login|not logged in", re.I)


def unavailable_kind(msg: str | None) -> str | None:
    """'limit' / 'auth' si el mensaje de error es de cuota o de credenciales."""
    if not msg:
        return None
    if _LIMIT_RE.search(msg):
        return "limit"
    if _AUTH_RE.search(msg):
        return "auth"
    return None


def _check_unavailable(msg: str) -> None:
    kind = unavailable_kind(msg)
    if kind:
        m = re.search(r"\|(\d{10})\b", msg)  # "Claude AI usage limit reached|<epoch>"
        raise ClaudeUnavailable(msg, kind, int(m.group(1)) if m else None)


def load_prompt(profile: dict) -> tuple[str, str]:
    """Devuelve (system_prompt, prompt_version)."""
    tpl = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.search(r"prompt_version:\s*(\S+)", tpl)
    version = m.group(1) if m else "0"
    tpl = re.sub(r"^<!--.*?-->\s*", "", tpl, flags=re.S)
    lines = []
    cand = profile.get("candidate", {}) or {}
    if cand.get("residence"):
        lines.append(f"- Residencia: {cand['residence']}")
    if cand.get("languages"):
        lines.append(f"- Idiomas: {', '.join(cand['languages'])}")
    if profile.get("salary_threshold"):
        lines.append(f"- Umbral de salario (`salary.meets_threshold`): {profile['salary_threshold']}")
    kinds = {"hard": "DURO", "almost_hard": "CASI DURO", "preference": "PREFERENCIA"}
    for c in profile.get("criteria", []):
        lines.append(f"- [{kinds.get(c.get('type'), c.get('type', '')).strip()}] "
                     f"{c['id']}: {c['text'].strip()}")
    scoring = profile.get("scoring", {}) or {}
    bias = (scoring.get("bias") or "").strip()
    guide = (scoring.get("guide") or "").strip()
    cases = profile.get("edge_cases") or []
    extra = "\n".join(f"{i}. {c.strip()}" for i, c in enumerate(cases, 6))
    return (tpl.replace("{profile}", "\n".join(lines)).replace("{bias}", bias)
            .replace("{score_guide}", guide).replace("{edge_cases}", extra), version)


def _truncate(text: str, limit: int = MAX_DESC_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    # Cabeza y cola: la ubicación/condiciones suelen estar al principio o al final.
    head = int(limit * 0.7)
    return text[:head] + "\n[...recortado...]\n" + text[-(limit - head):]


def render_job(job: Job) -> str:
    sal = ""
    if job.salary_min or job.salary_max:
        sal = (f"{job.salary_min or '?'} - {job.salary_max or '?'} {job.currency or ''} "
               f"({job.salary_interval or 'interval?'})")
    meta = {
        "job_ref": str(job.id),
        "fuente": job.source,
        "titulo": job.title,
        "empresa": job.company,
        "ubicacion": job.location,
        "remoto_declarado": job.is_remote,
        "tipo": job.job_type,
        "salario_estructurado": sal or None,
        "publicada": job.posted_at,
        "url": job.url,
    }
    meta_txt = "\n".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
    return (f"<oferta job_ref=\"{job.id}\">\n{meta_txt}\n\n<descripcion>\n"
            f"{_truncate(job.description)}\n</descripcion>\n</oferta>")


def build_user_prompt(jobs: list[Job]) -> str:
    body = "\n\n".join(render_job(j) for j in jobs)
    refs = ", ".join(str(j.id) for j in jobs)
    return (f"Clasifica estas {len(jobs)} ofertas. Devuelve exactamente un veredicto por oferta, "
            f"con job_ref en {{{refs}}}.\n\n{body}")


# --- backends ---------------------------------------------------------------------------------

def _call_claude_cli(system: str, user: str, model: str, claude_bin: str, timeout: int) -> dict:
    schema = json.dumps(batch_output_schema())
    cmd = [claude_bin, "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--strict-mcp-config",
           "--system-prompt", system, "--json-schema", schema]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout,
                           cwd="/tmp", env=env)
    except subprocess.TimeoutExpired as e:
        raise ClassifyError(f"claude timeout ({timeout}s)") from e
    except FileNotFoundError as e:
        raise ClassifyError(f"no se encuentra {claude_bin}") from e
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["sin salida"]
        _check_unavailable(f"claude rc={p.returncode}: {tail[0][:200]}")
        raise ClassifyError(f"claude rc={p.returncode}: {tail[0][:200]}") from e
    if out.get("is_error") or out.get("subtype") not in (None, "success"):
        msg = f"claude error: {str(out.get('result') or out.get('subtype'))[:200]}"
        status = out.get("api_error_status")
        _check_unavailable(f"{msg} ({status})" if status else msg)
        raise ClassifyError(msg)
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    return _parse_json_text(out.get("result") or "")


def _call_anthropic_api(system: str, user: str, model: str, timeout: int) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise ClassifyError("falta el paquete anthropic: uv sync --extra api") from e
    client = anthropic.Anthropic(timeout=timeout)
    try:
        resp = _api_create(client, system, user, model)
    except anthropic.RateLimitError as e:
        raise ClaudeUnavailable(f"api 429: {e}"[:200], "limit") from e
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise ClaudeUnavailable(f"api auth: {e}"[:200], "auth") from e
    if resp.stop_reason == "max_tokens":
        raise ClassifyError("respuesta truncada (max_tokens)")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json_text(text)


def _api_create(client, system: str, user: str, model: str):
    return client.messages.create(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": batch_output_schema()}},
    )


def _parse_json_text(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ClassifyError("la respuesta no contiene JSON")
    try:
        return json.loads(text[start:text.rfind("}") + 1])
    except json.JSONDecodeError as e:
        raise ClassifyError(f"JSON inválido: {e}") from e


def parse_batch(data: dict, jobs: list[Job]) -> dict[int, Verdict]:
    """Valida la salida y la asocia a cada oferta. Las ofertas sin veredicto no aparecen."""
    try:
        out = BatchOutput.model_validate(data)
    except ValidationError as e:
        raise ClassifyError(f"salida no válida: {str(e).splitlines()[0]}") from e
    ids = {str(j.id): j.id for j in jobs}
    res: dict[int, Verdict] = {}
    for bv in out.verdicts:
        ref = bv.job_ref.strip()
        if ref in ids:
            res[ids[ref]] = Verdict.model_validate(bv.model_dump(exclude={"job_ref"}))
    if len(jobs) == 1 and not res and len(out.verdicts) == 1:  # job_ref mal copiado
        res[jobs[0].id] = Verdict.model_validate(out.verdicts[0].model_dump(exclude={"job_ref"}))
    return res


class Classifier:
    def __init__(self, settings, profile: dict):
        self.s = settings
        self.system, self.prompt_version = load_prompt(profile)
        self.unavailable: ClaudeUnavailable | None = None

    def _call(self, jobs: list[Job]) -> dict:
        user = build_user_prompt(jobs)
        if self.s.llm_backend == "anthropic_api":
            return _call_anthropic_api(self.system, user, self.s.llm_model, self.s.llm_timeout)
        return _call_claude_cli(self.system, user, self.s.llm_model, self.s.claude_bin,
                                self.s.llm_timeout)

    def classify(self, jobs: list[Job]) -> tuple[dict[int, Verdict], dict[int, str]]:
        """Clasifica en lotes. Devuelve (veredictos, errores por job_id). Reintenta una vez;
        si un lote falla dos veces, prueba sus ofertas una a una. Si Claude no atiende (cuota
        agotada, token rechazado) para en seco: deja self.unavailable y no marca errores."""
        verdicts: dict[int, Verdict] = {}
        errors: dict[int, str] = {}
        n = self.s.classify_batch
        try:
            self._classify_batches(jobs, n, verdicts, errors)
        except ClaudeUnavailable as e:
            log.warning("Claude no disponible (%s): %s", e.kind, e)
            self.unavailable = e
            errors = {k: v for k, v in errors.items() if not unavailable_kind(v)}
        return verdicts, errors

    def _classify_batches(self, jobs, n, verdicts, errors) -> None:
        for i in range(0, len(jobs), n):
            batch = jobs[i:i + n]
            todo = batch
            for attempt in range(2):
                try:
                    got = parse_batch(self._call(todo), todo)
                    verdicts.update(got)
                    todo = [j for j in todo if j.id not in got]
                    if not todo:
                        break
                except ClaudeUnavailable:
                    raise
                except ClassifyError as e:
                    log.warning("lote %s intento %d: %s", [j.id for j in todo], attempt + 1, e)
                    for j in todo:
                        errors[j.id] = str(e)
            if todo and len(todo) > 1:
                for j in todo:
                    try:
                        got = parse_batch(self._call([j]), [j])
                        verdicts.update(got)
                    except ClaudeUnavailable:
                        raise
                    except ClassifyError as e:
                        errors[j.id] = str(e)
            for j in batch:
                if j.id in verdicts:
                    errors.pop(j.id, None)
                elif j.id not in errors:
                    errors[j.id] = "sin veredicto"
