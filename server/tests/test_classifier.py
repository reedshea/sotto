"""Tests for the intent classifier."""

import json
from unittest.mock import patch

import pytest

from sotto.classifier import Classifier, ClassificationResult, INTENTS
from sotto.config import Config, OllamaConfig, PipelineConfig, StorageConfig, WhisperConfig


@pytest.fixture
def config():
    return Config(
        storage=StorageConfig(output_dir="/tmp/sotto-classifier-test"),
        pipelines={
            "standard": PipelineConfig(
                transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
            ),
        },
        api_keys={"anthropic": "test-key"},
        ollama=OllamaConfig(endpoint="http://localhost:11434"),
    )


@pytest.fixture
def classifier(config):
    return Classifier(config)


class TestClassificationResult:
    def test_to_dict_roundtrip(self):
        result = ClassificationResult(
            intent="draft_request",
            subject="Feature spec for auth system",
            urgency="high",
            entities={"people": ["Alice"], "projects": ["AuthV2"], "dates": ["2026-04-01"]},
            action_items=["Draft auth spec", "Review with team"],
            reasoning="User asked for a draft",
        )
        d = result.to_dict()
        restored = ClassificationResult.from_dict(d)
        assert restored.intent == "draft_request"
        assert restored.subject == "Feature spec for auth system"
        assert restored.urgency == "high"
        assert restored.entities["people"] == ["Alice"]
        assert len(restored.action_items) == 2

    def test_from_dict_defaults(self):
        result = ClassificationResult.from_dict({})
        assert result.intent == "general"
        assert result.urgency == "normal"
        assert result.action_items == []


class TestParseResponse:
    def test_parse_valid_json(self, classifier):
        raw = json.dumps({
            "intent": "note_to_self",
            "subject": "Grocery list",
            "urgency": "low",
            "entities": {"people": [], "projects": [], "dates": []},
            "action_items": ["Buy milk", "Buy eggs"],
            "reasoning": "Simple note",
        })
        result = classifier._parse_response(raw)
        assert result.intent == "note_to_self"
        assert result.subject == "Grocery list"
        assert len(result.action_items) == 2

    def test_parse_json_with_surrounding_text(self, classifier):
        raw = 'Here is the classification:\n' + json.dumps({
            "intent": "journal",
            "subject": "Daily reflection",
            "urgency": "normal",
            "entities": {"people": [], "projects": [], "dates": []},
            "action_items": [],
            "reasoning": "Reflective content",
        }) + '\nDone.'
        result = classifier._parse_response(raw)
        assert result.intent == "journal"

    def test_parse_unknown_intent_defaults_to_general(self, classifier):
        raw = json.dumps({
            "intent": "totally_unknown",
            "subject": "Test",
            "urgency": "normal",
            "entities": {"people": [], "projects": [], "dates": []},
            "action_items": [],
            "reasoning": "Unknown",
        })
        result = classifier._parse_response(raw)
        assert result.intent == "general"

    def test_parse_invalid_json_returns_default(self, classifier):
        result = classifier._parse_response("This is not JSON at all.")
        assert result.intent == "general"


class TestPatternMatching:
    def test_no_patterns_returns_none(self, classifier):
        result = classifier._check_patterns("note to self buy milk")
        assert result is None

    def test_pattern_match(self, config):
        from sotto.config import PatternConfig
        config.patterns = [
            PatternConfig(trigger="note to self", intent="note_to_self"),
            PatternConfig(trigger="draft", intent="draft_request"),
        ]
        classifier = Classifier(config)

        result = classifier._check_patterns("Note to self: remember to call dentist")
        assert result is not None
        assert result.intent == "note_to_self"

    def test_pattern_no_match(self, config):
        from sotto.config import PatternConfig
        config.patterns = [
            PatternConfig(trigger="note to self", intent="note_to_self"),
        ]
        classifier = Classifier(config)

        result = classifier._check_patterns("I had a meeting today about the roadmap")
        assert result is None


class TestClassify:
    @patch.object(Classifier, "_call_anthropic")
    def test_classify_with_anthropic(self, mock_call, classifier):
        mock_call.return_value = json.dumps({
            "intent": "idea",
            "subject": "New app concept",
            "urgency": "normal",
            "entities": {"people": [], "projects": ["NewApp"], "dates": []},
            "action_items": ["Prototype the concept"],
            "reasoning": "Describes a new idea",
        })
        pipeline = PipelineConfig(llm_backend="anthropic", model="claude-sonnet-4-6")
        result = classifier.classify("I have an idea for a new app that...", pipeline)
        assert result.intent == "idea"
        mock_call.assert_called_once()

    def test_classify_with_pattern_fast_path(self, config):
        from sotto.config import PatternConfig
        config.patterns = [
            PatternConfig(trigger="draft", intent="draft_request"),
        ]
        classifier = Classifier(config)
        pipeline = PipelineConfig(llm_backend="anthropic", model="claude-sonnet-4-6")

        with patch.object(Classifier, "_call_anthropic") as mock_call:
            result = classifier.classify("Draft a proposal for the new API design", pipeline)
            # Should NOT call LLM because pattern matched
            mock_call.assert_not_called()
            assert result.intent == "draft_request"

    @patch.object(Classifier, "_call_anthropic")
    def test_classify_llm_failure_returns_default(self, mock_call, classifier):
        mock_call.side_effect = Exception("API error")
        pipeline = PipelineConfig(llm_backend="anthropic", model="claude-sonnet-4-6")
        result = classifier.classify("Some transcript text", pipeline)
        assert result.intent == "general"
        assert "failed" in result.reasoning.lower()
