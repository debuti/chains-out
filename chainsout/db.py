"""SQLite: jobs, verdicts, notifications, feedback, runs y un kv para estado (offset de Telegram...)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import Job, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    url_canonical TEXT NOT NULL UNIQUE,
    company TEXT, title TEXT, location TEXT,
    is_remote INTEGER, job_type TEXT,
    salary_min REAL, salary_max REAL, currency TEXT, salary_interval TEXT,
    description TEXT, posted_at TEXT, fetched_at TEXT,
    fingerprint TEXT NOT NULL,
    duplicate_of INTEGER REFERENCES jobs(id),
    -- new | prefiltered | pending | classified | error | duplicate
    status TEXT NOT NULL DEFAULT 'new',
    status_reason TEXT
);
CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    model TEXT, score INTEGER, decision TEXT, reasons TEXT,
    salary_estimate TEXT, raw TEXT, prompt_version TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS verdicts_job ON verdicts(job_id);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    sent_at TEXT, telegram_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    value TEXT NOT NULL CHECK (value IN ('interesa', 'no_interesa')),
    note TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT, finished_at TEXT, dry_run INTEGER,
    stats TEXT
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

_JOB_COLS = [
    "source", "url", "url_canonical", "company", "title", "location", "is_remote", "job_type",
    "salary_min", "salary_max", "currency", "salary_interval", "description", "posted_at",
    "fetched_at", "fingerprint",
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- jobs -------------------------------------------------------------------------------
    def insert_job(self, job: Job) -> tuple[str, int | None]:
        """Inserta una oferta normalizada.

        Devuelve ("seen", id) si la URL ya estaba, ("duplicate", id) si es una oferta ya conocida
        con otra URL (mismo fingerprint), o ("new", id).
        """
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE url_canonical = ?", (job.url_canonical,)
        ).fetchone()
        if row:
            return "seen", row["id"]
        dup = self.conn.execute(
            "SELECT id FROM jobs WHERE fingerprint = ? AND duplicate_of IS NULL ORDER BY id LIMIT 1",
            (job.fingerprint,),
        ).fetchone()
        data = asdict(job)
        values = [data[c] for c in _JOB_COLS]
        status = "duplicate" if dup else "new"
        cur = self.conn.execute(
            f"INSERT INTO jobs ({', '.join(_JOB_COLS)}, duplicate_of, status) "
            f"VALUES ({', '.join('?' for _ in _JOB_COLS)}, ?, ?)",
            [*values, dup["id"] if dup else None, status],
        )
        self.conn.commit()
        return ("duplicate" if dup else "new"), cur.lastrowid

    def set_status(self, job_id: int, status: str, reason: str | None = None) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, status_reason = ? WHERE id = ?", (status, reason, job_id)
        )
        self.conn.commit()

    def get_job(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row_to_job(row) if row else None

    def jobs_by_status(self, *statuses: str) -> list[Job]:
        rows = self.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({', '.join('?' for _ in statuses)}) ORDER BY id",
            statuses,
        ).fetchall()
        return [row_to_job(r) for r in rows]

    # --- verdicts ---------------------------------------------------------------------------
    def save_verdict(self, job_id: int, v: Verdict, model: str, prompt_version: str) -> None:
        self.conn.execute(
            "INSERT INTO verdicts (job_id, model, score, decision, reasons, salary_estimate, raw,"
            " prompt_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, model, v.score, v.decision,
                json.dumps({"hard_fails": v.hard_fails, "summary": v.summary,
                            "warnings": v.warnings}, ensure_ascii=False),
                v.salary.meets_threshold, v.model_dump_json(), prompt_version, now(),
            ),
        )
        self.conn.execute("UPDATE jobs SET status = 'classified', status_reason = NULL WHERE id = ?",
                          (job_id,))
        self.conn.commit()

    def latest_verdict(self, job_id: int) -> Verdict | None:
        row = self.conn.execute(
            "SELECT raw FROM verdicts WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
        ).fetchone()
        return Verdict.model_validate_json(row["raw"]) if row else None

    def pending_notifications(self, umbral_maybe: int) -> list[tuple[Job, Verdict]]:
        rows = self.conn.execute(
            """
            SELECT j.*, v.raw AS verdict_raw FROM jobs j
            JOIN verdicts v ON v.id = (SELECT MAX(id) FROM verdicts WHERE job_id = j.id)
            LEFT JOIN notifications n ON n.job_id = j.id
            WHERE n.id IS NULL AND j.status = 'classified'
              AND (v.decision = 'match' OR (v.decision = 'maybe' AND v.score >= ?))
            ORDER BY v.score DESC, j.id
            """,
            (umbral_maybe,),
        ).fetchall()
        return [(row_to_job(r), Verdict.model_validate_json(r["verdict_raw"])) for r in rows]

    def mark_notified(self, job_id: int, message_id: int | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notifications (job_id, sent_at, telegram_message_id)"
            " VALUES (?, ?, ?)",
            (job_id, now(), message_id),
        )
        self.conn.commit()

    # --- feedback ---------------------------------------------------------------------------
    def save_feedback(self, job_id: int, value: str, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO feedback (job_id, value, note, created_at) VALUES (?, ?, ?, ?)",
            (job_id, value, note, now()),
        )
        self.conn.commit()

    # --- runs / kv --------------------------------------------------------------------------
    def start_run(self, dry_run: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, dry_run) VALUES (?, ?)", (now(), int(dry_run))
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, stats = ? WHERE id = ?",
            (now(), json.dumps(stats, ensure_ascii=False), run_id),
        )
        self.conn.commit()

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()


def row_to_job(row: sqlite3.Row) -> Job:
    keys = row.keys()
    job = Job(**{c: row[c] for c in _JOB_COLS if c in keys})
    job.is_remote = None if row["is_remote"] is None else bool(row["is_remote"])
    job.id = row["id"]
    return job
