"""Tests for the dispatcher — markdown output and routing."""

import json
from pathlib import Path

import pytest

from sotto.classifier import ClassificationResult
from sotto.config import Config, DestinationsConfig, PipelineConfig, StorageConfig
from sotto.dispatcher import Dispatcher


@pytest.fixture
def config(tmp_path):
    return Config(
        storage=StorageConfig(output_dir=tmp_path / "sotto-dispatch-test"),
        destinations=DestinationsConfig({"obsidian_vault": str(tmp_path / "vault")}),
        pipelines={
            "standard": PipelineConfig(
                transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
            ),
        },
        api_keys={"anthropic": "test-key"},
    )


@pytest.fixture
def dispatcher(config):
    return Dispatcher(config)


@pytest.fixture
def sample_classification():
    return ClassificationResult(
        intent="note_to_self",
        subject="Remember to update the API docs",
        urgency="normal",
        entities={"people": ["Alice"], "projects": ["APIv3"], "dates": []},
        action_items=["Update API docs", "Notify team"],
        reasoning="User is reminding themselves of a task",
    )


@pytest.fixture
def sample_kwargs(sample_classification):
    return dict(
        uuid="test-uuid-123",
        transcript="Note to self: remember to update the API docs for the v3 release. "
                   "Also tell Alice about the changes.",
        classification=sample_classification,
        title="Update API Docs Reminder",
        summary="A reminder to update API documentation for v3.",
        duration=15.0,
        privacy="standard",
        pipeline=PipelineConfig(llm_backend="anthropic", model="claude-sonnet-4-6"),
        created_at="2026-03-07T10:00:00+00:00",
    )


class TestDispatchRouting:
    def test_dispatch_note_to_self(self, dispatcher, sample_kwargs, tmp_path):
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "note_created"
        assert result["intent"] == "note_to_self"
        assert Path(result["path"]).exists()

        # Verify markdown content
        content = Path(result["path"]).read_text()
        assert "---" in content  # frontmatter
        assert "Update API Docs Reminder" in content
        assert "[[Alice]]" in content  # Obsidian wiki link
        assert "[[APIv3]]" in content
        assert "- [ ] Update API docs" in content  # checkbox
        assert "Note to self:" in content

    def test_dispatch_meeting_debrief(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(
            intent="meeting_debrief",
            subject="Q3 planning with team",
        )
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "meeting_notes_created"
        assert "meetings" in result["path"]

    def test_dispatch_journal_appends(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(intent="journal", subject="Daily thoughts")

        # First dispatch creates the file
        result1 = dispatcher.dispatch(**sample_kwargs)
        assert result1["action"] == "journal_appended"
        path = Path(result1["path"])
        assert path.exists()
        content1 = path.read_text()
        assert "# Journal" in content1

        # Second dispatch appends
        result2 = dispatcher.dispatch(**sample_kwargs)
        content2 = Path(result2["path"]).read_text()
        assert content2.count("---") > content1.count("---")

    def test_dispatch_task(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(
            intent="task",
            subject="Sprint tasks",
            action_items=["Fix login bug", "Deploy to staging"],
        )
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "tasks_extracted"
        content = Path(result["path"]).read_text()
        assert "- [ ] Fix login bug" in content
        assert "- [ ] Deploy to staging" in content

    def test_dispatch_idea(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(intent="idea", subject="New app concept")
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "idea_captured"
        assert "ideas" in result["path"]

    def test_dispatch_general_goes_to_inbox(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(intent="general")
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "filed_to_inbox"
        assert "inbox" in result["path"]

    def test_dispatch_unknown_intent_goes_to_inbox(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(intent="completely_unknown")
        result = dispatcher.dispatch(**sample_kwargs)
        assert result["action"] == "filed_to_inbox"


class TestMarkdownFormatting:
    def test_frontmatter_contains_required_fields(self, dispatcher, sample_kwargs):
        result = dispatcher.dispatch(**sample_kwargs)
        content = Path(result["path"]).read_text()

        assert "intent: note_to_self" in content
        assert "uuid: test-uuid-123" in content
        assert "sotto/note_to_self" in content  # tag

    def test_high_urgency_adds_urgent_tag(self, dispatcher, sample_kwargs):
        sample_kwargs["classification"] = ClassificationResult(
            intent="task", subject="Urgent fix", urgency="high",
        )
        result = dispatcher.dispatch(**sample_kwargs)
        content = Path(result["path"]).read_text()
        assert "urgent" in content


class TestDraftRequest:
    def test_draft_request_calls_llm(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(
            intent="draft_request",
            subject="Feature proposal for auth",
            action_items=["Write spec", "Get review"],
        )

        # Mock the LLM call
        from unittest.mock import patch
        with patch.object(Dispatcher, "_call_anthropic_draft") as mock_draft:
            mock_draft.return_value = "# Auth Feature Proposal\n\nThis proposal outlines..."
            result = dispatcher.dispatch(**sample_kwargs)

        assert result["action"] == "draft_generated"
        content = Path(result["path"]).read_text()
        assert "Auth Feature Proposal" in content
        assert "needs-review" in content
        assert "Original Dictation" in content

    def test_draft_request_handles_llm_failure(self, dispatcher, sample_kwargs, tmp_path):
        sample_kwargs["classification"] = ClassificationResult(
            intent="draft_request",
            subject="Some draft",
        )

        from unittest.mock import patch
        with patch.object(Dispatcher, "_call_anthropic_draft") as mock_draft:
            mock_draft.side_effect = Exception("API timeout")
            result = dispatcher.dispatch(**sample_kwargs)

        assert result["action"] == "draft_generated"
        content = Path(result["path"]).read_text()
        assert "Draft generation failed" in content


class TestSlugify:
    def test_basic_slug(self):
        assert Dispatcher._slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        slug = Dispatcher._slugify("Feature: Auth System (v2)")
        assert ":" not in slug
        assert "(" not in slug

    def test_long_title_truncated(self):
        slug = Dispatcher._slugify("A" * 100)
        assert len(slug) <= 60

    def test_empty_string(self):
        assert Dispatcher._slugify("") == ""
