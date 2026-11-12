"""LinkedIn / Indeed / Google vía python-jobspy (versión fijada en pyproject/uv.lock)."""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Callable

from ..models import Job

log = logging.getLogger("chainsout.jobspy")


def _val(v):
    """Convierte NaN/None de pandas a None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def row_to_job(row: dict, site: str) -> Job:
    g = lambda k: _val(row.get(k))  # noqa: E731
    return Job(
        source=g("site") or site,
        url=g("job_url") or "",
        title=g("title") or "",
        company=g("company") or "",
        location=g("location") or "",
        is_remote=g("is_remote"),
        job_type=g("job_type") or "",
        salary_min=g("min_amount"),
        salary_max=g("max_amount"),
        currency=g("currency") or "",
        salary_interval=g("interval") or "",
        description=g("description") or "",
        posted_at=str(g("date_posted") or ""),
    )


class _Capture(logging.Handler):
    """JobSpy registra los bloqueos (429) como log y devuelve vacío en vez de lanzar: los capturamos."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _scrape(scrape_jobs: Callable, site: str, **kw) -> list[Job]:
    df = scrape_jobs(site_name=[site], verbose=0, description_format="markdown", **kw)
    return [row_to_job(r, site) for r in df.to_dict("records")]


def fetch(cfg: dict, stats: dict) -> list[Job]:
    """Ejecuta todas las búsquedas de searches.yaml. Tolera fallos por búsqueda y por sitio."""
    from jobspy import scrape_jobs

    d = cfg.get("defaults", {})
    hours_old = int(d.get("hours_old", 24))
    wanted = int(d.get("results_wanted", 25))
    pmin, pmax = d.get("pause_seconds", [4, 9])
    srcs = cfg.get("sources", {})
    jobs: list[Job] = []

    plan: list[tuple[str, dict]] = []
    for term in cfg.get("terms", []):
        if srcs.get("linkedin", {}).get("enabled"):
            for loc in srcs["linkedin"].get("locations", []):
                plan.append(("linkedin", dict(search_term=term, location=loc,
                                              fetch_description=True)))
        if srcs.get("indeed", {}).get("enabled"):
            for country in srcs["indeed"].get("countries", []):
                # Indeed solo admite UNO de: hours_old | (job_type + is_remote). Usamos
                # hours_old y metemos "remote" en la búsqueda.
                plan.append(("indeed", dict(search_term=f"{term} remote",
                                            country_indeed=country)))
        if srcs.get("google", {}).get("enabled"):
            for loc in srcs["google"].get("locations", []):
                plan.append(("google", dict(search_term=term,
                                            google_search_term=f"{term} remote jobs in {loc}")))

    cap = _Capture()
    jlog = logging.getLogger("jobspy")
    jlog.addHandler(cap)
    jlog.propagate = False
    try:
        return _run_plan(scrape_jobs, plan, stats, hours_old, wanted, pmin, pmax, cap, jobs)
    finally:
        jlog.removeHandler(cap)


def _run_plan(scrape_jobs, plan, stats, hours_old, wanted, pmin, pmax, cap, jobs):
    dead: set[str] = set()  # sitios que nos están bloqueando en esta ejecución
    strikes: dict[str, int] = {}  # bloqueos consecutivos (los 429 sueltos son habituales)
    for site, kw in plan:
        if site in dead:
            continue
        st = stats.setdefault(site, {"queries": 0, "jobs": 0, "errors": 0, "empty": 0})
        st["queries"] += 1
        cap.messages.clear()
        try:
            got = _scrape(scrape_jobs, site, hours_old=hours_old, results_wanted=wanted, **kw)
            st["jobs"] += len(got)
            jobs.extend(got)
            blocked = [m for m in cap.messages if "429" in m or "blocked" in m.lower()]
            if blocked:
                raise RuntimeError(blocked[0])
            strikes[site] = 0
            if not got:
                st["empty"] += 1
        except Exception as e:  # noqa: BLE001 - una fuente rota no debe parar la ejecución
            st["errors"] += 1
            msg = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            log.warning("%s %r: %s", site, kw.get("search_term"), msg)
            if "429" in msg or "blocked" in msg.lower():
                strikes[site] = strikes.get(site, 0) + 1
                if strikes[site] >= 3:
                    log.warning("%s bloqueado (3 x 429 seguidos); se omite el resto", site)
                    dead.add(site)
        time.sleep(random.uniform(pmin, pmax))
    return jobs
