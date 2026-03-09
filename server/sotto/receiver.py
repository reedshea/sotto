"""FastAPI receiver — accepts audio uploads, writes to disk, records in SQLite."""

from __future__ import annotations

import uuid as uuid_lib
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .config import Config, load_config
from .db import Database
from .orchestrator import Orchestrator
from .worker import Worker

app = FastAPI(title="Sotto", version="0.1.0")

_config: Config | None = None
_db: Database | None = None
_orchestrator: Orchestrator | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
        _config.ensure_dirs()
    return _config


def get_db() -> Database:
    global _db
    if _db is None:
        config = get_config()
        _db = Database(config.storage.output_dir / "sotto.db")
        _db.connect()
    return _db


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(get_config())
    return _orchestrator


def init_app(config: Config) -> None:
    """Initialize the app with a specific config (used by CLI)."""
    global _config, _db, _orchestrator
    _config = config
    _config.ensure_dirs()
    _db = Database(config.storage.output_dir / "sotto.db")
    _db.connect()
    _orchestrator = Orchestrator(config)


def _check_auth(authorization: str | None, config: Config) -> None:
    if not config.auth.tokens:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token not in config.auth.tokens:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/upload")
async def upload_audio(
    file: UploadFile,
    privacy: str = Form(default="standard"),
    sync: bool = Query(default=False),
    transcribe_only: bool = Query(default=False),
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
    db: Database = Depends(get_db),
):
    """Accept an audio file upload.

    Query params:
        sync: If true, block until transcription completes and return the
              transcript in the response. Ideal for short dictation clips.
        transcribe_only: If true, skip LLM title/summary generation and
              return only the raw transcript. Reduces latency significantly.
    """
    _check_auth(authorization, config)

    if privacy not in ("private", "standard"):
        raise HTTPException(status_code=400, detail="privacy must be 'private' or 'standard'")

    job_uuid = str(uuid_lib.uuid4())
    suffix = Path(file.filename).suffix if file.filename else ".m4a"
    dest_filename = f"{job_uuid}{suffix}"
    dest_path = config.storage.incoming_dir / dest_filename

    # Stream upload to disk
    with open(dest_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    job = db.insert_job(
        uuid=job_uuid, filename=dest_filename, privacy=privacy,
        transcribe_only=transcribe_only,
    )

    if sync:
        # Process inline — block until transcription (and optionally summarization) finishes.
        worker = Worker(config, db)
        worker.process_job(job_uuid, transcribe_only=transcribe_only)

        job = db.get_job(job_uuid)
        response_content = {
            "uuid": job.uuid,
            "status": job.status,
            "transcript": job.transcript,
            "title": job.title,
            "summary": job.summary,
            "duration_seconds": job.duration_seconds,
            "error_message": job.error_message,
        }
        if job.reply_to:
            response_content["reply_to"] = job.reply_to
        return JSONResponse(status_code=201, content=response_content)

    return JSONResponse(
        status_code=201,
        content={
            "uuid": job.uuid,
            "status": job.status,
        },
    )


@app.get("/jobs/{job_uuid}")
async def get_job_status(
    job_uuid: str,
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
    db: Database = Depends(get_db),
):
    """Get the status of a transcription job. Used by iOS app for polling."""
    _check_auth(authorization, config)

    job = db.get_job(job_uuid)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = {
        "uuid": job.uuid,
        "status": job.status,
        "privacy": job.privacy,
        "created_at": job.created_at,
        "title": job.title,
        "summary": job.summary,
        "transcript": job.transcript,
        "duration_seconds": job.duration_seconds,
        "error_message": job.error_message,
        "reply_to": job.reply_to,
    }
    return result


@app.get("/jobs")
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
    db: Database = Depends(get_db),
):
    """List recent jobs. Used by iOS app to sync state."""
    _check_auth(authorization, config)

    jobs = db.list_jobs(limit=limit, offset=offset)
    return [
        {
            "uuid": j.uuid,
            "status": j.status,
            "privacy": j.privacy,
            "created_at": j.created_at,
            "title": j.title,
            "summary": j.summary,
            "duration_seconds": j.duration_seconds,
            "error_message": j.error_message,
        }
        for j in jobs
    ]


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# ------------------------------------------------------------------
# Orchestrator endpoints — async Claude Code CLI session management
# ------------------------------------------------------------------


@app.post("/orchestrator/submit")
async def orchestrator_submit(
    prompt: str = Form(...),
    project: str | None = Form(default=None),
    reply_to: str | None = Form(default=None),
    project_path: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
):
    """Submit a task to the Claude Code orchestrator.

    The task runs asynchronously — poll /orchestrator/tasks/{task_id} for results.
    If reply_to matches a previous session, Claude resumes that conversation.
    """
    _check_auth(authorization, config)
    orch = get_orchestrator()

    task_id = await orch.submit(
        prompt=prompt,
        project=project,
        reply_to=reply_to,
        project_path=project_path,
    )
    return {"task_id": task_id, "status": "queued"}


@app.get("/orchestrator/tasks/{task_id}")
async def orchestrator_task_status(
    task_id: str,
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
):
    """Check the status of an orchestrator task."""
    _check_auth(authorization, config)
    orch = get_orchestrator()

    status = orch.check(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")

    result = {
        "task_id": status.task_id,
        "state": status.state,
        "session_id": status.session_id,
        "project": status.project,
        "reply_to": status.reply_to,
        "created_at": status.created_at,
        "updated_at": status.updated_at,
    }
    if status.state in ("completed", "failed", "timeout"):
        result["output"] = status.output
        result["error"] = status.error
        result["report_path"] = status.report_path
    return result


@app.get("/orchestrator/tasks")
async def orchestrator_list_tasks(
    active_only: bool = Query(default=False),
    limit: int = Query(default=20),
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
):
    """List orchestrator tasks."""
    _check_auth(authorization, config)
    orch = get_orchestrator()

    tasks = orch.list_active() if active_only else orch.list_recent(limit)
    return [
        {
            "task_id": t.task_id,
            "state": t.state,
            "project": t.project,
            "reply_to": t.reply_to,
            "created_at": t.created_at,
        }
        for t in tasks
    ]
