"""Tests for the FastAPI receiver endpoints."""

import io

import pytest
from fastapi.testclient import TestClient

from sotto.config import AuthConfig, Config, StorageConfig
from sotto.db import Database
from sotto.receiver import app, init_app


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
