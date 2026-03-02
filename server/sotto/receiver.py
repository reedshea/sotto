"""FastAPI receiver — accepts audio uploads, writes to disk, records in SQLite."""

from __future__ import annotations

import logging
import tempfile
import uuid as uuid_lib
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .config import Config, load_config
from .db import Database

logger = logging.getLogger("sotto.receiver")

app = FastAPI(title="Sotto", version="0.1.0")

_config: Config | None = None
_db: Database | None = None
_whisper_model = None


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


def init_app(config: Config) -> None:
    """Initialize the app with a specific config (used by CLI)."""
    global _config, _db
    _config = config
    _config.ensure_dirs()
    _db = Database(config.storage.output_dir / "sotto.db")
    _db.connect()


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
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
    db: Database = Depends(get_db),
):
    """Accept an audio file upload. Returns a UUID for tracking."""
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

    job = db.insert_job(uuid=job_uuid, filename=dest_filename, privacy=privacy)

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


def _get_whisper_model(config: Config):
    """Lazy-load and cache the Whisper model for sync transcription."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        device = config.whisper.device
        compute_type = "float16" if device == "cuda" else "int8"
        try:
            _whisper_model = WhisperModel(
                config.whisper.model, device=device, compute_type=compute_type
            )
        except RuntimeError as e:
            if "library" in str(e).lower() and device == "cuda":
                logger.warning("CUDA failed, falling back to CPU for Whisper model")
                _whisper_model = WhisperModel(
                    config.whisper.model, device="cpu", compute_type="int8"
                )
            else:
                raise
        logger.info("Whisper model loaded: %s on %s", config.whisper.model, device)
    return _whisper_model


@app.post("/transcribe")
async def transcribe_sync(
    file: UploadFile,
    authorization: str | None = Header(default=None),
    config: Config = Depends(get_config),
):
    """Synchronous transcribe-only endpoint. Accepts audio, returns transcript immediately.

    Designed for low-latency dictation — no LLM summarization, no job queue.
    """
    _check_auth(authorization, config)

    suffix = Path(file.filename).suffix if file.filename else ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)

    try:
        model = _get_whisper_model(config)
        segments, info = model.transcribe(str(tmp_path), beam_size=5)
        text_parts = [segment.text.strip() for segment in segments]
        transcript = " ".join(text_parts)
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "text": transcript,
        "duration_seconds": info.duration,
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
