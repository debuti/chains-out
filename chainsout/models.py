"""Modelos comunes: Job (oferta normalizada) y Verdict (salida del clasificador)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


@dataclass
class Job:
    source: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    is_remote: bool | None = None
    job_type: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = ""
    salary_interval: str = ""
    description: str = ""
    posted_at: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    # Calculados en normalize
    url_canonical: str = ""
    fingerprint: str = ""
    id: int | None = None


Fit = Literal["yes", "no", "unclear"]


def _legacy(data, renames: dict, prefix_renames: dict):
    """Acepta veredictos guardados con nombres de campo antiguos."""
    if not isinstance(data, dict):
        return data
    data = dict(data)
    for old, new in renames.items():
        if new not in data and old in data:
            data[new] = data[old]
    for prefix, new in prefix_renames.items():
        if new not in data:
            old = next((k for k in data if k.startswith(prefix)), None)
            if old:
                data[new] = data[old]
    return data


class Salary(BaseModel):
    published: bool
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    # ¿Llega al umbral de salary_threshold del perfil? (publicado o estimado)
    meets_threshold: Literal["likely", "unlikely", "unknown"]

    @model_validator(mode="before")
    @classmethod
    def _old(cls, data):
        return _legacy(data, {}, {"estimate_over": "meets_threshold"})


class Verdict(BaseModel):
    decision: Literal["match", "maybe", "reject"]
    score: int = Field(ge=0, le=10)
    hard_fails: list[str] = []
    location_fit: Fit          # ¿puede trabajar desde donde vive con esa modalidad?
    location_note: str         # alcance real, p. ej. "remote UE", "solo Alemania", "híbrido Lisboa"
    domain_fit: Literal["yes", "partial", "no"]
    preferences_met: list[str] = []  # ids de los criterios `preference` que cumple
    salary: Salary
    summary: str
    warnings: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _old(cls, data):
        data = _legacy(data, {"summary_es": "summary", "warnings_es": "warnings",
                              "remote_scope": "location_note"}, {})
        if isinstance(data, dict):
            data.setdefault("location_fit", "unclear")
            data.setdefault("location_note", "")
            data.setdefault("domain_fit", "partial")
        return data


class BatchVerdict(Verdict):
    job_ref: str


class BatchOutput(BaseModel):
    verdicts: list[BatchVerdict]


def _inline_refs(schema: dict) -> dict:
    """Resuelve $defs/$ref para obtener un JSON Schema plano (lo aceptan mejor los backends)."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(dict(defs[name]))
            out = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}))
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def batch_output_schema() -> dict:
    return _inline_refs(BatchOutput.model_json_schema())
