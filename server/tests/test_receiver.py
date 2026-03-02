"""Tests for the FastAPI receiver endpoints."""

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sotto.config import AuthConfig, Config, StorageConfig
from sotto.db import Database
from sotto.receiver import app, init_app
from sotto.worker import Worker


@pytest.fixture
def test_config(tmp_path):
    """Create a test config with temp directories."""
    return Config(
        storage=StorageConfig(output_dir=tmp_path / "sotto-test"),
        auth=AuthConfig(tokens=["test-token"]),
    )


@pytest.fixture
def test_db(test_config):
    """Create a test database."""
    db = Database(test_config.storage.output_dir / "sotto.db")
    return db


@pytest.fixture
def client(test_config, test_db):
    """Create a test client with initialized app."""
    init_app(test_config)
    return TestClient(app)


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


class TestHealthEndpoint:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestUploadEndpoint:
    def test_upload_success(self, client, auth_header, test_config):
        audio_content = b"fake audio data for testing"
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(audio_content), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert "uuid" in data
        assert data["status"] == "pending"

        # Verify file was written to incoming dir
        incoming_files = list(test_config.storage.incoming_dir.iterdir())
        assert len(incoming_files) == 1
        assert incoming_files[0].read_bytes() == audio_content

    def test_upload_private(self, client, auth_header):
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "private"},
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    def test_upload_invalid_privacy(self, client, auth_header):
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "invalid"},
            headers=auth_header,
        )
        assert response.status_code == 400

    def test_upload_no_auth(self, client):
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
        )
        assert response.status_code == 401

    def test_upload_bad_token(self, client):
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_upload_default_privacy_is_standard(self, client, auth_header):
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            headers=auth_header,
        )
        assert response.status_code == 201


class TestJobStatusEndpoint:
    def test_get_job_status(self, client, auth_header):
        # Upload first
        upload_resp = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        uuid = upload_resp.json()["uuid"]

        # Check status
        response = client.get(f"/jobs/{uuid}", headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert data["uuid"] == uuid
        assert data["status"] == "pending"
        assert data["privacy"] == "standard"

    def test_get_nonexistent_job(self, client, auth_header):
        response = client.get("/jobs/nonexistent-uuid", headers=auth_header)
        assert response.status_code == 404

    def test_get_job_no_auth(self, client):
        response = client.get("/jobs/some-uuid")
        assert response.status_code == 401


class TestListJobsEndpoint:
    def test_list_jobs_empty(self, client, auth_header):
        response = client.get("/jobs", headers=auth_header)
        assert response.status_code == 200
        assert response.json() == []

    def test_list_jobs_after_upload(self, client, auth_header):
        # Upload a few files
        for _ in range(3):
            client.post(
                "/upload",
                files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
                data={"privacy": "standard"},
                headers=auth_header,
            )

        response = client.get("/jobs", headers=auth_header)
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 3

    def test_list_jobs_with_limit(self, client, auth_header):
        for _ in range(5):
            client.post(
                "/upload",
                files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
                headers=auth_header,
            )

        response = client.get("/jobs?limit=2", headers=auth_header)
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_list_jobs_no_auth(self, client):
        response = client.get("/jobs")
        assert response.status_code == 401


class TestSyncUpload:
    """Tests for sync=true mode which blocks until transcription completes."""

    @patch.object(Worker, "_transcribe")
    @patch.object(Worker, "_generate_title_summary")
    def test_sync_upload_returns_transcript(
        self, mock_title, mock_transcribe, client, auth_header
    ):
        mock_transcribe.return_value = ("Hello world from dictation.", 3.5)
        mock_title.return_value = ("Dictation Test", "A short dictation.")

        response = client.post(
            "/upload?sync=true",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "completed"
        assert data["transcript"] == "Hello world from dictation."
        assert data["title"] == "Dictation Test"
        assert data["summary"] == "A short dictation."
        assert data["duration_seconds"] == 3.5

    @patch.object(Worker, "_transcribe")
    def test_sync_upload_transcribe_only(self, mock_transcribe, client, auth_header):
        """sync + transcribe_only skips LLM and returns raw transcript."""
        mock_transcribe.return_value = ("Quick note to self about the project.", 5.0)

        response = client.post(
            "/upload?sync=true&transcribe_only=true",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "completed"
        assert data["transcript"] == "Quick note to self about the project."
        assert data["summary"] == ""
        assert data["duration_seconds"] == 5.0
        # Title should be auto-generated from transcript (no LLM)
        assert data["title"] is not None

    def test_async_upload_unchanged(self, client, auth_header):
        """Default upload (no sync) still returns just uuid+status."""
        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"
        assert "transcript" not in data

    def test_transcribe_only_stored_on_job(self, client, auth_header, test_db):
        """transcribe_only flag is persisted on the job record."""
        response = client.post(
            "/upload?transcribe_only=true",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
            data={"privacy": "standard"},
            headers=auth_header,
        )
        assert response.status_code == 201
        uuid = response.json()["uuid"]

        job = test_db.get_job(uuid)
        assert job.transcribe_only == 1


class TestAuthWithNoTokens:
    def test_no_auth_required_when_no_tokens_configured(self, tmp_path):
        """When no tokens are configured, auth is not required."""
        config = Config(
            storage=StorageConfig(output_dir=tmp_path / "no-auth-test"),
            auth=AuthConfig(tokens=[]),
        )
        init_app(config)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200

        response = client.post(
            "/upload",
            files={"file": ("test.m4a", io.BytesIO(b"audio"), "audio/m4a")},
        )
        assert response.status_code == 201
