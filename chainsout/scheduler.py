"""Bucle de programación para entornos sin cron: ejecuta `run` cada N horas en una franja horaria."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("chainsout.daemon")


def next_slot(now: datetime, every_h: int, start_h: int, end_h: int) -> datetime:
    """Siguiente hora h con start_h <= h <= end_h y (h - start_h) % every_h == 0 (como cron 8-22/3)."""
    hours = list(range(start_h, end_h + 1, every_h))
    base = now.replace(minute=0, second=0, microsecond=0)
    for day in range(0, 2):
        d = base + timedelta(days=day)
        for h in hours:
            t = d.replace(hour=h)
            if t > now:
                return t
    raise AssertionError("unreachable")


def loop(every_h: int, start_h: int, end_h: int, tz: str, run_now: bool, args: list[str]) -> None:
    zone = ZoneInfo(tz)
    cmd = [sys.executable, "-m", "chainsout.cli", "run", *args]
    if run_now:
        subprocess.run(cmd, check=False)
    while True:
        now = datetime.now(zone)
        t = next_slot(now, every_h, start_h, end_h)
        log.info("próxima ejecución: %s", t.strftime("%Y-%m-%d %H:%M %Z"))
        while (remaining := (t - datetime.now(zone)).total_seconds()) > 0:
            time.sleep(min(remaining, 300))  # dormir a trozos tolera suspensiones y cambios de hora
        rc = subprocess.run(cmd, check=False).returncode
        if rc:
            log.warning("run terminó con código %d", rc)
