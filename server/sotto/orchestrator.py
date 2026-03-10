"""Orchestrator — async Claude Code CLI session manager.

This is the glue layer between Sotto's dispatch output and Claude Code CLI.
It is NOT part of the core transcription/classification/dispatch pipeline.

The orchestrator:
  - Receives structured dispatches (intent, transcript, project, reply_to)
  - Maps reply_to IDs to existing Claude Code session IDs for continuity
  - Spawns Claude CLI processes (potentially many concurrently)
  - Tracks running tasks and their output
  - Writes completed reports to the Obsidian vault

Usage:
    orch = Orchestrator(config)
    task_id = await orch.submit(
        prompt="implement the auth changes we discussed",
        project="sotto",
        reply_to="A4F2",
    )
    # Later...
    status = orch.check(task_id)
    # status.state == "completed", status.output == "..."
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config, OrchestratorConfig

logger = logging.getLogger("sotto.orchestrator")


@dataclass
class TaskStatus:
    """Status of an orchestrator task."""

    task_id: str
    state: str  # queued, running, completed, failed, timeout
    session_id: str | None = None
    project: str | None = None
    project_path: str | None = None
    reply_to: str | None = None
    prompt: str = ""
    output: str | None = None
    error: str | None = None
    report_path: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SessionStore:
    """SQLite-backed store for orchestrator tasks and session mappings."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'queued',
                session_id TEXT,
                project TEXT,
                project_path TEXT,
                reply_to TEXT,
                prompt TEXT NOT NULL,
                output TEXT,
                error TEXT,
                report_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_map (
                reply_to TEXT NOT NULL,
                project TEXT,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (reply_to, project)
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
            CREATE INDEX IF NOT EXISTS idx_session_map_reply ON session_map(reply_to);
        """)

    def insert_task(self, task: TaskStatus) -> None:
        self.conn.execute(
            """INSERT INTO tasks
               (task_id, state, session_id, project, project_path, reply_to,
                prompt, output, error, report_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id, task.state, task.session_id, task.project,
                task.project_path, task.reply_to, task.prompt, task.output,
                task.error, task.report_path, task.created_at, task.updated_at,
            ),
        )
        self.conn.commit()

    def update_task(self, task_id: str, **kwargs: Any) -> None:
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [task_id]
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = ?", vals)
        self.conn.commit()

    def get_task(self, task_id: str) -> TaskStatus | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return TaskStatus(**dict(row))

    def get_session_id(self, reply_to: str, project: str | None = None) -> str | None:
        """Look up the most recent session_id for a reply_to + project pair."""
        if project:
            row = self.conn.execute(
                "SELECT session_id FROM session_map WHERE reply_to = ? AND project = ?",
                (reply_to, project),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT session_id FROM session_map WHERE reply_to = ? ORDER BY created_at DESC LIMIT 1",
                (reply_to,),
            ).fetchone()
        return row["session_id"] if row else None

    def save_session_mapping(
        self, reply_to: str, project: str | None, session_id: str, task_id: str
    ) -> None:
        """Store or update the reply_to -> session_id mapping."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO session_map (reply_to, project, session_id, task_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (reply_to, project or "", session_id, task_id, now),
        )
        self.conn.commit()

    def list_running(self) -> list[TaskStatus]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE state IN ('queued', 'running') ORDER BY created_at ASC"
        ).fetchall()
        return [TaskStatus(**dict(r)) for r in rows]

    def list_recent(self, limit: int = 20) -> list[TaskStatus]:
        rows = self.conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [TaskStatus(**dict(r)) for r in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class Orchestrator:
    """Manages concurrent Claude Code CLI sessions.

    The orchestrator is the bridge between Sotto dispatches and Claude Code.
    It maintains session continuity via reply_to IDs and can run multiple
    CLI instances concurrently up to a configurable limit.
    """

    def __init__(
        self,
        config: Config,
        orch_config: OrchestratorConfig | None = None,
    ):
        self.config = config
        self.orch_config = orch_config or getattr(config, "orchestrator", None) or OrchestratorConfig()

        # Resolve paths
        self._vault_root = self._resolve_vault_root()
        self._report_dir = self._resolve_report_dir()

        store_path = self.orch_config.session_store_path
        if store_path:
            db_path = Path(store_path).expanduser()
        else:
            db_path = config.storage.output_dir / "orchestrator.db"

        self.store = SessionStore(db_path)

        # Persistent background event loop for running CLI tasks.
        # This avoids the problem where asyncio.run() creates a temporary loop
        # that gets torn down (cancelling tasks) as soon as submit() returns.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="orchestrator-loop"
        )
        self._loop_thread.start()

        # Semaphore to limit concurrent Claude CLI processes.
        # Created lazily on first use inside the background loop.
        self._max_concurrent = self.orch_config.max_concurrent
        self._semaphore: asyncio.Semaphore | None = None

        # Track running asyncio tasks for cleanup
        self._running_tasks: dict[str, asyncio.Task] = {}

    def _resolve_vault_root(self) -> Path:
        destinations = getattr(self.config, "destinations", None)
        if destinations and destinations.get("obsidian_vault"):
            return Path(destinations["obsidian_vault"]).expanduser()
        return self.config.storage.output_dir / "vault"

    def _resolve_report_dir(self) -> Path:
        if self.orch_config.report_dir:
            return Path(self.orch_config.report_dir).expanduser()
        return self._vault_root / "reports"

    async def submit(
        self,
        prompt: str,
        project: str | None = None,
        reply_to: str | None = None,
        project_path: str | None = None,
    ) -> str:
        """Submit a task to be executed by Claude Code CLI.

        Args:
            prompt: The natural-language instruction for Claude.
            project: Project name (matched against config.projects).
            reply_to: Reply-to ID for session continuity.
            project_path: Explicit project path (overrides config lookup).

        Returns:
            task_id: A short identifier for tracking this task.
        """
        task_id = self._generate_task_id()
        now = datetime.now(timezone.utc).isoformat()

        # Resolve project path from config if not explicitly provided
        if not project_path and project:
            project_path = self._resolve_project_path(project)

        # Look up existing session for reply continuity
        session_id = None
        if reply_to:
            session_id = self.store.get_session_id(reply_to, project)
            if session_id:
                logger.info(
                    "Resuming session %s for reply_to=%s", session_id, reply_to
                )

        task = TaskStatus(
            task_id=task_id,
            state="queued",
            session_id=session_id,
            project=project,
            project_path=project_path,
            reply_to=reply_to,
            prompt=prompt,
            created_at=now,
            updated_at=now,
        )
        self.store.insert_task(task)

        # Launch the async execution on our persistent background loop.
        # asyncio.run_coroutine_threadsafe is safe to call from any thread.
        future = asyncio.run_coroutine_threadsafe(
            self._execute(task_id), self._loop
        )
        # Wrap the concurrent.futures.Future so we can track it
        self._running_tasks[task_id] = future
        future.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

        logger.info(
            "Submitted task %s (project=%s, reply_to=%s, resume=%s)",
            task_id, project, reply_to, session_id or "new",
        )
        return task_id

    def submit_sync(
        self,
        prompt: str,
        project: str | None = None,
        reply_to: str | None = None,
        project_path: str | None = None,
    ) -> str:
        """Synchronous wrapper for submit() — safe to call from any thread.

        Schedules the submit on the orchestrator's persistent loop and blocks
        until the task_id is returned. The actual _execute task continues
        running in the background.
        """
        future = asyncio.run_coroutine_threadsafe(
            self.submit(
                prompt=prompt,
                project=project,
                reply_to=reply_to,
                project_path=project_path,
            ),
            self._loop,
        )
        return future.result(timeout=10)

    def check(self, task_id: str) -> TaskStatus | None:
        """Check the status of a submitted task."""
        return self.store.get_task(task_id)

    def list_active(self) -> list[TaskStatus]:
        """List all currently queued or running tasks."""
        return self.store.list_running()

    def list_recent(self, limit: int = 20) -> list[TaskStatus]:
        """List recent tasks (any state)."""
        return self.store.list_recent(limit)

    async def wait(self, task_id: str) -> TaskStatus:
        """Wait for a task to complete and return its final status."""
        future = self._running_tasks.get(task_id)
        if future:
            # Wait for the concurrent.futures.Future from the background loop
            await asyncio.wrap_future(future)
        return self.store.get_task(task_id)

    async def shutdown(self) -> None:
        """Cancel all running tasks and clean up."""
        for task_id, future in list(self._running_tasks.items()):
            future.cancel()
            logger.info("Cancelled task %s", task_id)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        self.store.close()

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _execute(self, task_id: str) -> None:
        """Acquire semaphore slot and run the Claude CLI process."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        async with self._semaphore:
            self.store.update_task(task_id, state="running")
            task = self.store.get_task(task_id)

            try:
                session_id, output = await self._run_claude(
                    prompt=task.prompt,
                    cwd=task.project_path,
                    session_id=task.session_id,
                )

                # Write report
                report_path = self._write_report(task, output, session_id)

                # Update task
                self.store.update_task(
                    task_id,
                    state="completed",
                    session_id=session_id,
                    output=output,
                    report_path=str(report_path) if report_path else None,
                )

                # Store session mapping for future replies
                if task.reply_to and session_id:
                    self.store.save_session_mapping(
                        reply_to=task.reply_to,
                        project=task.project,
                        session_id=session_id,
                        task_id=task_id,
                    )
                    logger.info(
                        "Saved session mapping: %s -> %s", task.reply_to, session_id
                    )

                logger.info("Task %s completed (%d chars output)", task_id, len(output))

            except asyncio.TimeoutError:
                logger.warning("Task %s timed out", task_id)
                self.store.update_task(task_id, state="timeout", error="Timed out")
            except asyncio.CancelledError:
                logger.info("Task %s cancelled", task_id)
                self.store.update_task(task_id, state="failed", error="Cancelled")
                raise
            except Exception as e:
                logger.exception("Task %s failed", task_id)
                self.store.update_task(task_id, state="failed", error=str(e))

    async def _run_claude(
        self,
        prompt: str,
        cwd: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, str]:
        """Invoke the Claude Code CLI and return (session_id, output).

        Uses `claude -p` (headless/print mode) with JSON output to get
        structured results including the session ID for later resumption.
        """
        cmd = ["claude", "-p", prompt, "--output-format", "json"]

        if session_id:
            cmd.extend(["--resume", session_id])

        env = os.environ.copy()
        work_dir = cwd or str(Path.home())

        logger.debug("Running: %s (cwd=%s)", " ".join(cmd[:4]) + "...", work_dir)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.orch_config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Claude CLI exited with code {proc.returncode}: {stderr_text[:500]}"
            )

        # Parse JSON output to extract session_id and result text
        new_session_id, output_text = self._parse_claude_output(
            stdout_text, session_id
        )

        if stderr_text.strip():
            logger.debug("Claude CLI stderr: %s", stderr_text[:200])

        return new_session_id, output_text

    def _parse_claude_output(
        self, raw: str, fallback_session_id: str | None
    ) -> tuple[str, str]:
        """Parse Claude CLI JSON output.

        The JSON output from `claude -p --output-format json` contains:
          {"type": "result", "session_id": "...", "result": "..."}
        """
        try:
            data = json.loads(raw)
            session_id = data.get("session_id", fallback_session_id)
            result = data.get("result", "")
            # result can be a string or a list of content blocks
            if isinstance(result, list):
                parts = []
                for block in result:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                result = "\n".join(parts)
            return session_id or fallback_session_id or "", str(result)
        except (json.JSONDecodeError, KeyError):
            # If JSON parsing fails, treat entire output as plain text
            logger.warning("Could not parse Claude CLI JSON output, using raw text")
            return fallback_session_id or "", raw

    # ------------------------------------------------------------------
    # Report writing
    # ------------------------------------------------------------------

    def _write_report(
        self, task: TaskStatus, output: str, session_id: str
    ) -> Path | None:
        """Write a completed task's output as an Obsidian markdown report."""
        if not output.strip():
            return None

        self._report_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        # Build a filesystem-safe slug from the first line of output or prompt
        slug_source = task.prompt[:60] if task.prompt else task.task_id
        slug = self._slugify(slug_source)

        filename = f"{date_str}-{slug}.md"
        path = self._report_dir / filename

        # Avoid overwriting — append counter if needed
        counter = 1
        while path.exists():
            counter += 1
            filename = f"{date_str}-{slug}-{counter}.md"
            path = self._report_dir / filename

        lines = [
            "---",
            f'title: "Report: {slug_source}"',
            f"date: {now.strftime('%Y-%m-%dT%H:%M:%S')}",
            "type: orchestrator_report",
            f"task_id: {task.task_id}",
            f"session_id: {session_id}",
            f"project: {task.project or 'none'}",
        ]
        if task.reply_to:
            lines.append(f"reply_to: {task.reply_to}")
        lines.extend([
            "tags:",
            "  - sotto/report",
            "  - needs-review",
            "---",
            "",
            f"# Report: {slug_source}",
            "",
            f"> **Task:** {task.task_id}",
            f"> **Session:** `{session_id}`",
            f"> **Project:** {task.project or 'none'} (`{task.project_path or 'N/A'}`)",
            f"> **Generated:** {date_str} at {time_str}",
        ])
        if task.reply_to:
            lines.append(f"> **Reply to:** {task.reply_to}")
        lines.extend([
            "",
            "## Output",
            "",
            output,
            "",
            "---",
            "",
            "## Original Prompt",
            "",
            task.prompt,
            "",
        ])

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Report written to %s", path)
        return path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_project_path(self, project: str) -> str | None:
        """Look up a project path from Sotto config."""
        projects = getattr(self.config, "projects", {})
        if project in projects:
            return str(Path(projects[project].path).expanduser())
        return None

    @staticmethod
    def _generate_task_id() -> str:
        """Generate a short, human-friendly task ID."""
        return uuid_mod.uuid4().hex[:8].upper()

    @staticmethod
    def _slugify(text: str) -> str:
        import re

        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug[:60].rstrip("-")
