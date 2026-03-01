"""Tests for the SQLite database layer."""

import pytest

from sotto.db import Database, Job, VALID_STATUSES


@pytest.fixture
def db(tmp_path):
    """Create a fresh database for each test."""
    database = Database(tmp_path / "test.db")
    database.connect()
    yield database
    database.close()


class TestDatabaseConnection:
    def test_connect_creates_db_file(self, tmp_path):
        db = Database(tmp_path / "new.db")
        db.connect()
        assert (tmp_path / "new.db").exists()
        db.close()

    def test_connect_creates_parent_dirs(self, tmp_path):
        db = Database(tmp_path / "subdir" / "deep" / "test.db")
        db.connect()
        assert (tmp_path / "subdir" / "deep" / "test.db").exists()
        db.close()

    def test_lazy_connect_on_property_access(self, tmp_path):
        db = Database(tmp_path / "lazy.db")
        # Accessing .conn should trigger connect
        assert db.conn is not None
        assert (tmp_path / "lazy.db").exists()
        db.close()


class TestInsertJob:
    def test_insert_job_returns_job(self, db):
        job = db.insert_job(uuid="test-uuid-1", filename="test.m4a")
        assert isinstance(job, Job)
        assert job.uuid == "test-uuid-1"
        assert job.filename == "test.m4a"
        assert job.status == "pending"
        assert job.privacy == "standard"

    def test_insert_job_with_privacy(self, db):
        job = db.insert_job(uuid="test-uuid-2", filename="test.m4a", privacy="private")
        assert job.privacy == "private"

    def test_insert_job_sets_timestamps(self, db):
        job = db.insert_job(uuid="test-uuid-3", filename="test.m4a")
        assert job.created_at is not None
        assert job.updated_at is not None

    def test_insert_duplicate_uuid_raises(self, db):
        db.insert_job(uuid="dup-uuid", filename="a.m4a")
        with pytest.raises(Exception):
            db.insert_job(uuid="dup-uuid", filename="b.m4a")


class TestGetJob:
    def test_get_existing_job(self, db):
        db.insert_job(uuid="get-test", filename="test.m4a")
        job = db.get_job("get-test")
        assert job is not None
        assert job.uuid == "get-test"

    def test_get_nonexistent_job_returns_none(self, db):
        result = db.get_job("does-not-exist")
        assert result is None


class TestUpdateStatus:
    def test_update_status(self, db):
        db.insert_job(uuid="status-test", filename="test.m4a")
        db.update_status("status-test", "transcribing")
        job = db.get_job("status-test")
        assert job.status == "transcribing"

    def test_update_status_updates_timestamp(self, db):
        db.insert_job(uuid="ts-test", filename="test.m4a")
        original = db.get_job("ts-test")
        db.update_status("ts-test", "transcribing")
        updated = db.get_job("ts-test")
        assert updated.updated_at >= original.updated_at

    def test_all_valid_statuses(self, db):
        for i, status in enumerate(VALID_STATUSES):
            uuid = f"valid-status-{i}"
            db.insert_job(uuid=uuid, filename="test.m4a")
            db.update_status(uuid, status)
            job = db.get_job(uuid)
            assert job.status == status

    def test_invalid_status_raises(self, db):
        db.insert_job(uuid="invalid-status", filename="test.m4a")
        with pytest.raises(ValueError, match="Invalid status"):
            db.update_status("invalid-status", "bogus")


class TestUpdateJobResult:
    def test_update_job_result(self, db):
        db.insert_job(uuid="result-test", filename="test.m4a")
        db.update_job_result(
            uuid="result-test",
            title="Test Title",
            summary="Test summary of recording.",
            output_path="/tmp/output/result-test.txt",
            duration_seconds=120.5,
        )
        job = db.get_job("result-test")
        assert job.status == "completed"
        assert job.title == "Test Title"
        assert job.summary == "Test summary of recording."
        assert job.output_path == "/tmp/output/result-test.txt"
        assert job.duration_seconds == 120.5

    def test_update_job_result_without_duration(self, db):
        db.insert_job(uuid="no-dur", filename="test.m4a")
        db.update_job_result(
            uuid="no-dur",
            title="No Duration",
            summary="Summary.",
            output_path="/tmp/output/no-dur.txt",
        )
        job = db.get_job("no-dur")
        assert job.status == "completed"
        assert job.duration_seconds is None


class TestGetPendingJobs:
    def test_get_pending_jobs_empty(self, db):
        jobs = db.get_pending_jobs()
        assert jobs == []

    def test_get_pending_jobs_returns_only_pending(self, db):
        db.insert_job(uuid="p1", filename="a.m4a")
        db.insert_job(uuid="p2", filename="b.m4a")
        db.insert_job(uuid="p3", filename="c.m4a")
        db.update_status("p2", "transcribing")

        pending = db.get_pending_jobs()
        uuids = [j.uuid for j in pending]
        assert "p1" in uuids
        assert "p3" in uuids
        assert "p2" not in uuids

    def test_get_pending_jobs_ordered_by_created_at(self, db):
        db.insert_job(uuid="first", filename="a.m4a")
        db.insert_job(uuid="second", filename="b.m4a")
        db.insert_job(uuid="third", filename="c.m4a")

        pending = db.get_pending_jobs()
        assert pending[0].uuid == "first"
        assert pending[1].uuid == "second"
        assert pending[2].uuid == "third"


class TestListJobs:
    def test_list_jobs_empty(self, db):
        jobs = db.list_jobs()
        assert jobs == []

    def test_list_jobs_respects_limit(self, db):
        for i in range(10):
            db.insert_job(uuid=f"list-{i}", filename=f"{i}.m4a")
        jobs = db.list_jobs(limit=3)
        assert len(jobs) == 3

    def test_list_jobs_respects_offset(self, db):
        for i in range(5):
            db.insert_job(uuid=f"offset-{i}", filename=f"{i}.m4a")
        all_jobs = db.list_jobs()
        offset_jobs = db.list_jobs(offset=2)
        assert len(offset_jobs) == 3
        assert offset_jobs[0].uuid == all_jobs[2].uuid

    def test_list_jobs_ordered_desc_by_created_at(self, db):
        db.insert_job(uuid="old", filename="a.m4a")
        db.insert_job(uuid="new", filename="b.m4a")
        jobs = db.list_jobs()
        # Most recent first
        assert jobs[0].uuid == "new"
        assert jobs[1].uuid == "old"


class TestDatabaseClose:
    def test_close_and_reconnect(self, tmp_path):
        db = Database(tmp_path / "reconnect.db")
        db.connect()
        db.insert_job(uuid="persist", filename="test.m4a")
        db.close()

        # Reconnect and verify data persists
        db2 = Database(tmp_path / "reconnect.db")
        db2.connect()
        job = db2.get_job("persist")
        assert job is not None
        assert job.uuid == "persist"
        db2.close()
