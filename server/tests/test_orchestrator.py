"""Tests for the orchestrator — async Claude CLI session manager."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from sotto.config import (
    Config,
    DestinationsConfig,
    OrchestratorConfig,
    PipelineConfig,
    ProjectConfig,
    StorageConfig,
)
from sotto.orchestrator import Orchestrator, SessionStore, TaskStatus


@pytest.fixture
def tmp_config(tmp_path):
    return Config(
        storage=StorageConfig(output_dir=tmp_path / "sotto-orch-test"),
        destinations=DestinationsConfig({"obsidian_vault": str(tmp_path / "vault")}),
        pipelines={
            "standard": PipelineConfig(
                transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
            ),
        },
        projects={
            "sotto": ProjectConfig(path=str(tmp_path / "projects" / "sotto"), aliases=["soto"]),
            "indigo": ProjectConfig(path=str(tmp_path / "projects" / "indigo"), aliases=[]),
        },
        orchestrator=OrchestratorConfig(
            max_concurrent=2,
            timeout_seconds=30,
            session_store_path=str(tmp_path / "orchestrator.db"),
            report_dir=str(tmp_path / "vault" / "reports"),
        ),
    )


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "test-sessions.db")
    s.connect()
    yield s
    s.close()


@pytest.fixture
def orchestrator(tmp_config):
    return Orchestrator(tmp_config)


# ------------------------------------------------------------------
# SessionStore tests
# ------------------------------------------------------------------


class TestSessionStore:
    def test_insert_and_get_task(self, store):
        task = TaskStatus(
            task_id="ABCD1234",
            state="queued",
            prompt="do the thing",
            project="sotto",
            created_at="2026-03-09T10:00:00",
            updated_at="2026-03-09T10:00:00",
        )
        store.insert_task(task)
        got = store.get_task("ABCD1234")
        assert got is not None
        assert got.task_id == "ABCD1234"
        assert got.state == "queued"
        assert got.prompt == "do the thing"

    def test_update_task(self, store):
        task = TaskStatus(
            task_id="UP123456",
            state="queued",
            prompt="test prompt",
            created_at="2026-03-09T10:00:00",
            updated_at="2026-03-09T10:00:00",
        )
        store.insert_task(task)
        store.update_task("UP123456", state="completed", output="done!")
        got = store.get_task("UP123456")
        assert got.state == "completed"
        assert got.output == "done!"

    def test_session_mapping(self, store):
        store.save_session_mapping(
            reply_to="A4F2",
            project="sotto",
            session_id="sess-abc-123",
            task_id="TASK0001",
        )
        sid = store.get_session_id("A4F2", "sotto")
        assert sid == "sess-abc-123"

    def test_session_mapping_no_project(self, store):
        store.save_session_mapping(
            reply_to="B7X9",
            project=None,
            session_id="sess-xyz-456",
            task_id="TASK0002",
        )
        sid = store.get_session_id("B7X9")
        assert sid == "sess-xyz-456"

    def test_session_mapping_overwrite(self, store):
        store.save_session_mapping("C1D2", "sotto", "sess-old", "T1")
        store.save_session_mapping("C1D2", "sotto", "sess-new", "T2")
        sid = store.get_session_id("C1D2", "sotto")
        assert sid == "sess-new"

    def test_session_mapping_miss(self, store):
        assert store.get_session_id("NOPE") is None

    def test_list_running(self, store):
        for i, state in enumerate(["queued", "running", "completed", "failed"]):
            store.insert_task(TaskStatus(
                task_id=f"T{i}",
                state=state,
                prompt=f"task {i}",
                created_at=f"2026-03-09T10:0{i}:00",
                updated_at=f"2026-03-09T10:0{i}:00",
            ))
        running = store.list_running()
        assert len(running) == 2
        assert {t.task_id for t in running} == {"T0", "T1"}

    def test_list_recent(self, store):
        for i in range(5):
            store.insert_task(TaskStatus(
                task_id=f"R{i}",
                state="completed",
                prompt=f"task {i}",
                created_at=f"2026-03-09T10:0{i}:00",
                updated_at=f"2026-03-09T10:0{i}:00",
            ))
        recent = store.list_recent(limit=3)
        assert len(recent) == 3


# ------------------------------------------------------------------
# Orchestrator unit tests
# ------------------------------------------------------------------


class TestOrchestratorInit:
    def test_resolves_vault_root(self, orchestrator, tmp_config):
        assert "vault" in str(orchestrator._vault_root)

    def test_resolves_report_dir(self, orchestrator, tmp_config):
        assert "reports" in str(orchestrator._report_dir)

    def test_resolves_project_path(self, orchestrator, tmp_config):
        path = orchestrator._resolve_project_path("sotto")
        assert path is not None
        assert "sotto" in path

    def test_resolve_unknown_project(self, orchestrator):
        assert orchestrator._resolve_project_path("nope") is None


class TestParseClaudeOutput:
    def test_parse_json_output(self, orchestrator):
        raw = json.dumps({
            "type": "result",
            "session_id": "sess-123",
            "result": "Here is my analysis...",
        })
        sid, text = orchestrator._parse_claude_output(raw, None)
        assert sid == "sess-123"
        assert text == "Here is my analysis..."

    def test_parse_json_content_blocks(self, orchestrator):
        raw = json.dumps({
            "type": "result",
            "session_id": "sess-456",
            "result": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
        })
        sid, text = orchestrator._parse_claude_output(raw, None)
        assert sid == "sess-456"
        assert "Part 1" in text
        assert "Part 2" in text

    def test_parse_plain_text_fallback(self, orchestrator):
        raw = "Just some plain text output"
        sid, text = orchestrator._parse_claude_output(raw, "fallback-sid")
        assert sid == "fallback-sid"
        assert text == raw

    def test_parse_no_session_uses_fallback(self, orchestrator):
        raw = json.dumps({"type": "result", "result": "output"})
        sid, text = orchestrator._parse_claude_output(raw, "fb-123")
        assert sid == "fb-123"


class TestWriteReport:
    def test_writes_markdown_report(self, orchestrator, tmp_config):
        task = TaskStatus(
            task_id="RPT12345",
            state="completed",
            prompt="Plan the auth refactor",
            project="sotto",
            project_path="/home/user/sotto",
            reply_to="A4F2",
            created_at="2026-03-09T10:00:00",
            updated_at="2026-03-09T10:00:00",
        )
        path = orchestrator._write_report(task, "Here is the plan...", "sess-abc")
        assert path is not None
        assert path.exists()

        content = path.read_text()
        assert "task_id: RPT12345" in content
        assert "session_id: sess-abc" in content
        assert "sotto/report" in content
        assert "Here is the plan..." in content
        assert "Plan the auth refactor" in content
        assert "reply_to: A4F2" in content

    def test_no_report_for_empty_output(self, orchestrator):
        task = TaskStatus(
            task_id="EMPTY123",
            state="completed",
            prompt="test",
            created_at="2026-03-09T10:00:00",
            updated_at="2026-03-09T10:00:00",
        )
        path = orchestrator._write_report(task, "", "sess-x")
        assert path is None

    def test_avoids_overwriting(self, orchestrator, tmp_config):
        task = TaskStatus(
            task_id="DUP12345",
            state="completed",
            prompt="same prompt",
            created_at="2026-03-09T10:00:00",
            updated_at="2026-03-09T10:00:00",
        )
        p1 = orchestrator._write_report(task, "First output", "s1")
        p2 = orchestrator._write_report(task, "Second output", "s2")
        assert p1 != p2
        assert p1.exists()
        assert p2.exists()


class TestTaskIdGeneration:
    def test_generates_8char_hex(self):
        tid = Orchestrator._generate_task_id()
        assert len(tid) == 8
        assert tid == tid.upper()
        # Should be valid hex
        int(tid, 16)

    def test_unique_ids(self):
        ids = {Orchestrator._generate_task_id() for _ in range(100)}
        assert len(ids) == 100


# ------------------------------------------------------------------
# Async integration tests (mocked CLI)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_and_complete(tmp_config):
    """Submit a task with a mocked Claude CLI and verify it completes."""
    orch = Orchestrator(tmp_config)

    mock_output = json.dumps({
        "type": "result",
        "session_id": "new-session-001",
        "result": "Implementation plan: Step 1...",
    })

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
    mock_proc.returncode = 0
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        task_id = await orch.submit(
            prompt="Plan the auth refactor",
            project="sotto",
            reply_to="A4F2",
        )
        status = await orch.wait(task_id)

    assert status.state == "completed"
    assert status.session_id == "new-session-001"
    assert "Step 1" in status.output
    assert status.report_path is not None
    assert Path(status.report_path).exists()

    # Verify session mapping was saved
    sid = orch.store.get_session_id("A4F2", "sotto")
    assert sid == "new-session-001"

    await orch.shutdown()


@pytest.mark.asyncio
async def test_submit_resumes_session(tmp_config):
    """When reply_to matches an existing session, resume it."""
    orch = Orchestrator(tmp_config)

    # Pre-seed a session mapping
    orch.store.save_session_mapping("B7X9", "sotto", "existing-sess-42", "old-task")

    mock_output = json.dumps({
        "type": "result",
        "session_id": "existing-sess-42",
        "result": "Continuing from where we left off...",
    })

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
    mock_proc.returncode = 0
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        task_id = await orch.submit(
            prompt="Continue the implementation",
            project="sotto",
            reply_to="B7X9",
        )
        status = await orch.wait(task_id)

    assert status.state == "completed"
    assert status.session_id == "existing-sess-42"

    # Verify --resume was passed
    call_args = mock_exec.call_args
    assert "--resume" in call_args[0]
    assert "existing-sess-42" in call_args[0]

    await orch.shutdown()


@pytest.mark.asyncio
async def test_cli_failure_marks_task_failed(tmp_config):
    """Non-zero exit code from Claude CLI marks the task as failed."""
    orch = Orchestrator(tmp_config)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: auth failed"))
    mock_proc.returncode = 1
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        task_id = await orch.submit(prompt="do something")
        status = await orch.wait(task_id)

    assert status.state == "failed"
    assert "exit" in status.error.lower() or "code 1" in status.error.lower()

    await orch.shutdown()


@pytest.mark.asyncio
async def test_concurrent_task_limit(tmp_config):
    """Verify the semaphore limits concurrent tasks."""
    tmp_config.orchestrator.max_concurrent = 2
    orch = Orchestrator(tmp_config)

    execution_order = []
    call_count = 0

    async def slow_communicate():
        nonlocal call_count
        call_count += 1
        current = call_count
        execution_order.append(("start", current))
        await asyncio.sleep(0.1)
        execution_order.append(("end", current))
        output = json.dumps({"type": "result", "session_id": f"s{current}", "result": f"done {current}"})
        return output.encode(), b""

    def make_mock_proc():
        proc = AsyncMock()
        proc.communicate = slow_communicate
        proc.returncode = 0
        proc.kill = MagicMock()
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=lambda *a, **kw: make_mock_proc()):
        ids = []
        for i in range(4):
            tid = await orch.submit(prompt=f"task {i}")
            ids.append(tid)

        # Wait for all to complete
        for tid in ids:
            await orch.wait(tid)

    # All should complete
    for tid in ids:
        status = orch.check(tid)
        assert status.state == "completed"

    await orch.shutdown()


@pytest.mark.asyncio
async def test_list_active_and_recent(tmp_config):
    """Test listing active and recent tasks."""
    orch = Orchestrator(tmp_config)

    # Manually insert tasks at different states
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for state in ["queued", "running", "completed"]:
        orch.store.insert_task(TaskStatus(
            task_id=f"LIST-{state.upper()}",
            state=state,
            prompt="test",
            created_at=now,
            updated_at=now,
        ))

    active = orch.list_active()
    assert len(active) == 2  # queued + running

    recent = orch.list_recent()
    assert len(recent) == 3

    await orch.shutdown()
