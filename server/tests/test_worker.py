"""Tests for the worker pipeline (with mocked transcription and LLM calls)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sotto.classifier import Classifier, ClassificationResult
from sotto.config import Config, DestinationsConfig, OllamaConfig, PipelineConfig, StorageConfig, WhisperConfig
from sotto.db import Database
from sotto.dispatcher import Dispatcher
from sotto.worker import Worker


@pytest.fixture
def worker_config(tmp_path):
    return Config(
        storage=StorageConfig(output_dir=tmp_path / "sotto-worker-test"),
        pipelines={
            "private": PipelineConfig(
                transcription="local", llm_backend="ollama", model="llama3.1:34b"
            ),
            "standard": PipelineConfig(
                transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
            ),
        },
        api_keys={"anthropic": "test-key"},
        ollama=OllamaConfig(endpoint="http://localhost:11434"),
        whisper=WhisperConfig(model="tiny", device="cpu"),
        destinations=DestinationsConfig(obsidian_vault=str(tmp_path / "vault")),
    )


@pytest.fixture
def worker_db(worker_config):
    worker_config.ensure_dirs()
    db = Database(worker_config.storage.output_dir / "sotto.db")
    db.connect()
    yield db
    db.close()


@pytest.fixture
def worker(worker_config, worker_db):
    return Worker(worker_config, worker_db)


def create_fake_audio(config, filename):
    """Create a fake audio file in the incoming directory."""
    audio_path = config.storage.incoming_dir / filename
    audio_path.write_bytes(b"fake audio content")
    return audio_path


class TestParseTitleSummary:
    def test_parse_valid_json(self, worker):
        text = '{"title": "My Title", "summary": "A brief summary."}'
        title, summary = worker._parse_title_summary(text)
        assert title == "My Title"
        assert summary == "A brief summary."

    def test_parse_json_with_surrounding_text(self, worker):
        text = 'Here is the result:\n{"title": "My Title", "summary": "A brief summary."}\nDone.'
        title, summary = worker._parse_title_summary(text)
        assert title == "My Title"
        assert summary == "A brief summary."

    def test_parse_invalid_json_fallback(self, worker):
        text = "Some Title\nSome summary text here."
        title, summary = worker._parse_title_summary(text)
        assert title == "Some Title"
        assert "summary" in summary.lower() or len(summary) > 0

    def test_parse_empty_string_fallback(self, worker):
        title, summary = worker._parse_title_summary("")
        assert title == "Untitled"
        assert summary == ""


class TestProcessJob:
    @patch.object(Dispatcher, "dispatch", return_value={"action": "filed_to_inbox"})
    @patch.object(Classifier, "classify", return_value=ClassificationResult(intent="general"))
    @patch.object(Worker, "_transcribe")
    @patch.object(Worker, "_generate_title_summary")
    def test_process_job_success(
        self, mock_title, mock_transcribe, mock_classify, mock_dispatch,
        worker, worker_config, worker_db,
    ):
        """Full pipeline with mocked transcription, classification, and LLM."""
        mock_transcribe.return_value = ("This is the transcript text.", 45.0)
        mock_title.return_value = ("Test Recording Title", "A test recording about something.")

        # Create fake audio and job
        create_fake_audio(worker_config, "test-uuid.m4a")
        worker_db.insert_job(uuid="test-uuid", filename="test-uuid.m4a", privacy="standard")

        worker.process_job("test-uuid")

        job = worker_db.get_job("test-uuid")
        assert job.status == "completed"
        assert job.title == "Test Recording Title"
        assert job.summary == "A test recording about something."
        assert job.duration_seconds == 45.0
        assert job.intent == "general"

    @patch.object(Dispatcher, "dispatch", return_value={"action": "filed_to_inbox"})
    @patch.object(Classifier, "classify", return_value=ClassificationResult(intent="general"))
    @patch.object(Worker, "_transcribe")
    @patch.object(Worker, "_generate_title_summary")
    def test_process_job_writes_output_files(
        self, mock_title, mock_transcribe, mock_classify, mock_dispatch,
        worker, worker_config, worker_db,
    ):
        """Verify .txt and .json output files are written."""
        transcript = "Hello, this is a test transcript."
        mock_transcribe.return_value = (transcript, 30.0)
        mock_title.return_value = ("Output Test", "Testing output files.")

        create_fake_audio(worker_config, "output-uuid.m4a")
        worker_db.insert_job(uuid="output-uuid", filename="output-uuid.m4a", privacy="private")

        worker.process_job("output-uuid")

        # Find the output files
        completed = worker_config.storage.completed_dir
        txt_files = list(completed.rglob("output-uuid.txt"))
        json_files = list(completed.rglob("output-uuid.json"))

        assert len(txt_files) == 1
        assert len(json_files) == 1

        assert txt_files[0].read_text() == transcript

        meta = json.loads(json_files[0].read_text())
        assert meta["uuid"] == "output-uuid"
        assert meta["title"] == "Output Test"
        assert meta["summary"] == "Testing output files."
        assert meta["transcript"] == transcript
        assert meta["privacy"] == "private"
        assert meta["pipeline_used"] == "private"
        assert meta["duration_seconds"] == 30.0

    def test_process_job_missing_audio(self, worker, worker_db):
        """Job with missing audio file should be marked failed."""
        worker_db.insert_job(uuid="missing-audio", filename="missing.m4a")
        worker.process_job("missing-audio")
        job = worker_db.get_job("missing-audio")
        assert job.status == "failed"

    def test_process_job_nonexistent_uuid(self, worker):
        """Processing a nonexistent job should not raise."""
        worker.process_job("does-not-exist")

    def test_process_job_no_pipeline(self, worker, worker_config, worker_db):
        """Job with unknown privacy mode should fail."""
        create_fake_audio(worker_config, "no-pipeline.m4a")
        worker_db.insert_job(uuid="no-pipeline", filename="no-pipeline.m4a", privacy="unknown")
        worker.process_job("no-pipeline")
        job = worker_db.get_job("no-pipeline")
        assert job.status == "failed"

    @patch.object(Dispatcher, "dispatch", return_value={"action": "filed_to_inbox"})
    @patch.object(Classifier, "classify", return_value=ClassificationResult(intent="general"))
    @patch.object(Worker, "_transcribe")
    @patch.object(Worker, "_generate_title_summary")
    def test_process_job_status_transitions(
        self, mock_title, mock_transcribe, mock_classify, mock_dispatch,
        worker, worker_config, worker_db,
    ):
        """Verify the job goes through correct status transitions."""
        statuses_seen = []

        original_update = worker_db.update_status

        def track_status(uuid, status):
            statuses_seen.append(status)
            original_update(uuid, status)

        worker_db.update_status = track_status

        mock_transcribe.return_value = ("Transcript.", 10.0)
        mock_title.return_value = ("Title", "Summary.")

        create_fake_audio(worker_config, "transition.m4a")
        worker_db.insert_job(uuid="transition", filename="transition.m4a")

        worker.process_job("transition")

        assert statuses_seen == ["transcribing", "classifying", "summarizing", "dispatching"]
        # Final status set via update_job_result
        job = worker_db.get_job("transition")
        assert job.status == "completed"


class TestTranscribeOnlyMode:
    @patch.object(Worker, "_transcribe")
    def test_transcribe_only_skips_llm(self, mock_transcribe, worker, worker_config, worker_db):
        """transcribe_only=True should never call LLM."""
        mock_transcribe.return_value = ("Dictated text for clipboard.", 4.0)

        create_fake_audio(worker_config, "dictation.m4a")
        worker_db.insert_job(uuid="dictation", filename="dictation.m4a", privacy="standard")

        with patch.object(Worker, "_generate_title_summary") as mock_llm:
            worker.process_job("dictation", transcribe_only=True)
            mock_llm.assert_not_called()

        job = worker_db.get_job("dictation")
        assert job.status == "completed"
        assert job.transcript == "Dictated text for clipboard."
        assert job.summary == ""
        assert job.duration_seconds == 4.0

    @patch.object(Worker, "_transcribe")
    def test_transcribe_only_skips_summarizing_status(
        self, mock_transcribe, worker, worker_config, worker_db
    ):
        """In transcribe_only mode, status should go pending -> transcribing -> completed (no summarizing)."""
        statuses_seen = []
        original_update = worker_db.update_status

        def track_status(uuid, status):
            statuses_seen.append(status)
            original_update(uuid, status)

        worker_db.update_status = track_status
        mock_transcribe.return_value = ("Short dictation.", 2.0)

        create_fake_audio(worker_config, "status-test.m4a")
        worker_db.insert_job(uuid="status-test", filename="status-test.m4a")

        worker.process_job("status-test", transcribe_only=True)

        assert statuses_seen == ["transcribing"]
        job = worker_db.get_job("status-test")
        assert job.status == "completed"

    @patch.object(Worker, "_transcribe")
    def test_transcribe_only_auto_title(self, mock_transcribe, worker, worker_config, worker_db):
        """Title should be auto-generated from transcript start."""
        long_text = "A" * 100
        mock_transcribe.return_value = (long_text, 10.0)

        create_fake_audio(worker_config, "title-test.m4a")
        worker_db.insert_job(uuid="title-test", filename="title-test.m4a")

        worker.process_job("title-test", transcribe_only=True)

        job = worker_db.get_job("title-test")
        assert len(job.title) <= 63  # 60 chars + "..."
        assert job.title.endswith("...")


class TestWorkerRunLoop:
    @patch.object(Worker, "process_job")
    def test_run_processes_pending_jobs(self, mock_process, worker, worker_config, worker_db):
        """Worker.run should pick up pending jobs."""
        create_fake_audio(worker_config, "run-test.m4a")
        worker_db.insert_job(uuid="run-test", filename="run-test.m4a")

        # Run one iteration then stop
        import threading

        def stop_after_delay():
            import time
            time.sleep(0.3)
            worker.stop()

        t = threading.Thread(target=stop_after_delay)
        t.start()
        worker.run(poll_interval=0.1)
        t.join()

        mock_process.assert_called_with("run-test", transcribe_only=False)

    def test_stop_flag(self, worker):
        """Calling stop() should set the running flag to False."""
        worker._running = True
        worker.stop()
        assert worker._running is False
