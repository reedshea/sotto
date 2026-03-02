"""Worker — polls for pending jobs, transcribes audio, generates title/summary, writes output."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import Config, PipelineConfig
from .db import Database

logger = logging.getLogger("sotto.worker")

TITLE_SUMMARY_PROMPT = """You are processing a voice transcript. Given the following transcript, produce:
1. A short, descriptive title (under 80 characters)
2. A 1-2 sentence summary

Respond in exactly this JSON format, nothing else:
{{"title": "...", "summary": "..."}}

Transcript:
{transcript}"""


class Worker:
    """Processes pending transcription jobs."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._running = False
        
        # Windows-specific: Prepare CUDA DLLs if using GPU
        if self.config.whisper.device == "cuda":
            self._prepare_cuda_env()

    def _prepare_cuda_env(self) -> None:
        """Registers NVIDIA DLLs from pip packages on Windows to prevent cublas64_12.dll errors."""
        if sys.platform != "win32":
            return

        import site
        # Search in all potential site-packages locations (Standard + User)
        search_paths = site.getsitepackages()
        if site.getusersitepackages():
            search_paths.append(site.getusersitepackages())

        found_binaries = False
        for base_path in search_paths:
            nvidia_root = Path(base_path) / "nvidia"
            if nvidia_root.is_dir():
                for bin_dir in nvidia_root.glob("**/bin"):
                    if bin_dir.is_dir():
                        # PATH is needed because CTranslate2 loads cublas via
                        # standard DLL search, not AddDllDirectory.
                        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                        os.add_dll_directory(str(bin_dir))
                        logger.debug("Registered NVIDIA DLL directory: %s", bin_dir)
                        found_binaries = True

        if not found_binaries:
            logger.warning("CUDA requested, but no 'nvidia-*' pip packages found.")

    def run(self, poll_interval: float = 2.0) -> None:
        """Poll for pending jobs and process them. Runs until stopped."""
        self._running = True
        logger.info("Worker started, polling every %.1fs", poll_interval)

        while self._running:
            jobs = self.db.get_pending_jobs()
            for job in jobs:
                try:
                    self.process_job(job.uuid)
                except Exception as e:
                    logger.exception("Failed to process job %s", job.uuid)
                    self.db.update_job_error(job.uuid, str(e))

            time.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False

    def process_job(self, uuid: str) -> None:
        """Run the full pipeline for a single job."""
        job = self.db.get_job(uuid)
        if not job:
            logger.warning("Job %s not found", uuid)
            return

        pipeline_name = job.privacy
        pipeline = self.config.pipelines.get(pipeline_name)
        if not pipeline:
            logger.error("No pipeline configured for privacy=%s", pipeline_name)
            self.db.update_job_error(uuid, f"No pipeline configured for privacy={pipeline_name}")
            return

        audio_path = self.config.storage.incoming_dir / job.filename

        if not audio_path.exists():
            logger.error("Audio file missing: %s", audio_path)
            self.db.update_job_error(uuid, f"Audio file missing: {audio_path}")
            return

        # Step 1: Transcribe
        self.db.update_status(uuid, "transcribing")
        logger.info("Transcribing %s", uuid)
        transcript, duration = self._transcribe(audio_path)

        # Step 2: Generate title and summary (graceful fallback if LLM fails)
        self.db.update_status(uuid, "summarizing")
        logger.info("Generating title/summary for %s", uuid)
        error_msg = None
        try:
            title, summary = self._generate_title_summary(transcript, pipeline)
        except Exception as e:
            logger.warning("LLM failed for %s: %s. Using fallback.", uuid, e)
            title = transcript[:60].strip() + "..." if len(transcript) > 60 else transcript
            summary = ""
            error_msg = f"LLM summarization failed: {e}"

        # Step 3: Write output files
        output_path = self._write_output(uuid, job, transcript, title, summary, duration)

        # Step 4: Mark complete (transcript is always saved, even if LLM failed)
        self.db.update_job_result(
            uuid=uuid,
            title=title,
            summary=summary,
            output_path=str(output_path),
            duration_seconds=duration,
            transcript=transcript,
            error_message=error_msg,
        )
        if error_msg:
            logger.info("Completed %s with LLM fallback: %s", uuid, title)
        else:
            logger.info("Completed %s: %s", uuid, title)

    def _transcribe(self, audio_path: Path) -> tuple[str, float]:
        """Transcribe audio using faster-whisper. Returns (transcript, duration_seconds)."""
        from faster_whisper import WhisperModel

        device = self.config.whisper.device
        compute_type = "float16" if device == "cuda" else "int8"

        try:
            model = WhisperModel(
                self.config.whisper.model,
                device=device,
                compute_type=compute_type,
            )
        except RuntimeError as e:
            # Fallback to CPU if GPU libraries fail to load
            if "library" in str(e).lower() and device == "cuda":
                logger.error("CUDA failed (missing DLLs?). Falling back to CPU for this job.")
                model = WhisperModel(self.config.whisper.model, device="cpu", compute_type="int8")
            else:
                raise e

        segments, info = model.transcribe(str(audio_path), beam_size=5)
        text_parts = [segment.text.strip() for segment in segments]

        transcript = " ".join(text_parts)
        return transcript, info.duration

    def _generate_title_summary(self, transcript: str, pipeline: PipelineConfig) -> tuple[str, str]:
        """Generate a title and summary via LLM."""
        prompt = TITLE_SUMMARY_PROMPT.format(transcript=transcript[:8000])

        if pipeline.llm_backend == "ollama":
            return self._call_ollama(prompt, pipeline.model)
        elif pipeline.llm_backend == "anthropic":
            return self._call_anthropic(prompt, pipeline.model)
        elif pipeline.llm_backend == "openai":
            return self._call_openai(prompt, pipeline.model)
        else:
            logger.warning("Unknown backend: %s, using fallback", pipeline.llm_backend)
            return transcript[:60] + "...", transcript[:150] + "..."

    def _call_ollama(self, prompt: str, model: str) -> tuple[str, str]:
        endpoint = self.config.ollama.endpoint
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
        text = resp.json()["response"]
        return self._parse_title_summary(text)

    def _call_anthropic(self, prompt: str, model: str) -> tuple[str, str]:
        api_key = self.config.api_keys.get("anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not configured")

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        return self._parse_title_summary(text)

    def _call_openai(self, prompt: str, model: str) -> tuple[str, str]:
        api_key = self.config.api_keys.get("openai")
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 256,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return self._parse_title_summary(text)

    def _parse_title_summary(self, text: str) -> tuple[str, str]:
        """Extract title and summary from LLM JSON response."""
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
            return data["title"], data["summary"]
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning("Failed to parse LLM response, using fallback")
            lines = [line for line in text.strip().split("\n") if line.strip()]
            title = lines[0][:80] if lines else "Untitled"
            summary = " ".join(lines[1:3])[:200] if len(lines) > 1 else ""
            return title, summary

    def _write_output(self, uuid: str, job, transcript: str, title: str, summary: str, duration: float) -> Path:
        """Write .txt and .json output files."""
        now = datetime.now(timezone.utc)
        month_dir = self.config.storage.completed_dir / str(now.year) / f"{now.month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)

        txt_path = month_dir / f"{uuid}.txt"
        txt_path.write_text(transcript, encoding="utf-8")

        meta = {
            "uuid": uuid,
            "captured_at": job.created_at,
            "duration_seconds": duration,
            "privacy": job.privacy,
            "title": title,
            "summary": summary,
            "transcript": transcript,
            "pipeline_used": job.privacy,
            "model_used": self.config.pipelines[job.privacy].model,
        }
        json_path = month_dir / f"{uuid}.json"
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        return month_dir
