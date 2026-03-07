"""SQLite database layer for Sotto job tracking."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sotto.db")


@dataclass
class Job:
    uuid: str
    filename: str
    status: str
    privacy: str
    created_at: str
    updated_at: str
    title: str | None = None
    summary: str | None = None
    output_path: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    transcript: str | None = None
    transcribe_only: int = 0
    intent: str | None = None
    intent_metadata: str | None = None  # JSON blob from classifier
    dispatch_status: str | None = None  # pending_dispatch, dispatched, dispatch_failed
    dispatch_result: str | None = None  # JSON blob from dispatcher

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(**dict(row))


# Valid status transitions:
# pending -> transcribing -> classifying -> summarizing -> dispatching -> completed
# Any state -> failed
VALID_STATUSES = {
    "pending", "transcribing", "classifying", "summarizing", "dispatching", "completed", "failed",
}


class Database:
    """Simple SQLite wrapper for the jobs table."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._migrate()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                uuid TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                privacy TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                output_path TEXT,
                duration_seconds REAL,
                error_message TEXT,
                transcript TEXT
            )
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that may be missing from older databases."""
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        migrations = {
            "error_message": "ALTER TABLE jobs ADD COLUMN error_message TEXT",
            "transcript": "ALTER TABLE jobs ADD COLUMN transcript TEXT",
            "transcribe_only": "ALTER TABLE jobs ADD COLUMN transcribe_only INTEGER NOT NULL DEFAULT 0",
            "intent": "ALTER TABLE jobs ADD COLUMN intent TEXT",
            "intent_metadata": "ALTER TABLE jobs ADD COLUMN intent_metadata TEXT",
            "dispatch_status": "ALTER TABLE jobs ADD COLUMN dispatch_status TEXT",
            "dispatch_result": "ALTER TABLE jobs ADD COLUMN dispatch_result TEXT",
        }
        for col, sql in migrations.items():
            if col not in existing:
                logger.info("Migrating DB: adding '%s' column", col)
                self._conn.execute(sql)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    def insert_job(
        self, uuid: str, filename: str, privacy: str = "standard", transcribe_only: bool = False,
    ) -> Job:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO jobs (uuid, filename, status, privacy, created_at, updated_at, transcribe_only)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (uuid, filename, privacy, now, now, int(transcribe_only)),
        )
        self.conn.commit()
        return self.get_job(uuid)

    def get_job(self, uuid: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE uuid = ?", (uuid,)).fetchone()
        return Job.from_row(row) if row else None

    def get_pending_jobs(self) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        return [Job.from_row(r) for r in rows]

    def update_status(self, uuid: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE uuid = ?",
            (status, now, uuid),
        )
        self.conn.commit()

    def update_job_error(self, uuid: str, error_message: str) -> None:
        """Mark a job as failed and store the error message."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, updated_at = ? WHERE uuid = ?",
            (error_message, now, uuid),
        )
        self.conn.commit()

    def update_job_result(
        self,
        uuid: str,
        title: str,
        summary: str,
        output_path: str,
        duration_seconds: float | None = None,
        transcript: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE jobs
               SET status = 'completed', title = ?, summary = ?, output_path = ?,
                   duration_seconds = ?, transcript = ?, error_message = ?, updated_at = ?
               WHERE uuid = ?""",
            (title, summary, output_path, duration_seconds, transcript, error_message, now, uuid),
        )
        self.conn.commit()

    def update_job_classification(
        self,
        uuid: str,
        intent: str,
        intent_metadata: str,
    ) -> None:
        """Store classification results for a job."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE jobs
               SET intent = ?, intent_metadata = ?, dispatch_status = 'pending_dispatch',
                   updated_at = ?
               WHERE uuid = ?""",
            (intent, intent_metadata, now, uuid),
        )
        self.conn.commit()

    def update_job_dispatch(
        self,
        uuid: str,
        dispatch_result: str,
        dispatch_status: str = "dispatched",
    ) -> None:
        """Store dispatch results for a job."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE jobs
               SET dispatch_status = ?, dispatch_result = ?, updated_at = ?
               WHERE uuid = ?""",
            (dispatch_status, dispatch_result, now, uuid),
        )
        self.conn.commit()

    def list_jobs(self, limit: int = 50, offset: int = 0) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [Job.from_row(r) for r in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
