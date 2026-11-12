"""Prefiltro barato antes del LLM. Debe pecar de dejar pasar: solo descarta lo obvio."""

from __future__ import annotations

import re

from .models import Job


class Prefilter:
    def __init__(self, profile: dict):
        pf = profile.get("prefilter", {})
        self.title_reject = [re.compile(p, re.I) for p in pf.get("title_reject", [])]
        self.job_type_reject = {t.lower().replace("-", "").replace("_", "")
                                for t in pf.get("job_type_reject", [])}
        self.domain_any = [re.compile(p, re.I) for p in pf.get("domain_any", [])]
        self.remote_any = [re.compile(p, re.I) for p in pf.get("remote_any", [])]
        self.company_reject = [re.compile(p, re.I) for p in profile.get("company_reject", [])]

    def company_excluded(self, job: Job) -> str | None:
        for rx in self.company_reject:
            if rx.search(job.company or ""):
                return f"company:{rx.pattern}"
        return None

    def check(self, job: Job) -> str | None:
        """None si pasa; si no, el motivo del descarte."""
        if reason := self.company_excluded(job):
            return reason
        for rx in self.title_reject:
            if rx.search(job.title or ""):
                return f"title:{rx.pattern}"
        types = {t.strip().lower().replace("-", "").replace("_", "")
                 for t in (job.job_type or "").split(",") if t.strip()}
        # Solo descartamos si TODOS los tipos declarados son malos (p. ej. "fulltime, parttime" pasa).
        if types and types <= self.job_type_reject:
            return f"job_type:{job.job_type}"
        text = f"{job.title}\n{job.description or ''}"
        if self.domain_any and not any(rx.search(text) for rx in self.domain_any):
            return "no_domain_keywords"
        full = f"{job.title}\n{job.location or ''}\n{job.description or ''}"
        if self.remote_any and job.is_remote is not True and not any(
                rx.search(full) for rx in self.remote_any):
            return "no_remote_mention"
        return None
