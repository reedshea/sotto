"""SQLite database layer for Sotto job tracking."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(**dict(row))


# Valid status transitions matching the iOS lifecycle:
# pending -> transcribing -> summarizing -> completed
# Any state -> failed
VALID_STATUSES = {"pending", "transcribing", "summarizing", "completed", "failed"}


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
                duration_seconds REAL
            )
        """)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    def insert_job(self, uuid: str, filename: str, privacy: str = "standard") -> Job:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO jobs (uuid, filename, status, privacy, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?, ?)""",
            (uuid, filename, privacy, now, now),
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

    def update_job_result(
        self,
        uuid: str,
        title: str,
        summary: str,
        output_path: str,
        duration_seconds: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE jobs
               SET status = 'completed', title = ?, summary = ?, output_path = ?,
                   duration_seconds = ?, updated_at = ?
               WHERE uuid = ?""",
            (title, summary, output_path, duration_seconds, now, uuid),
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
