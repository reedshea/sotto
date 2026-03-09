"""Tests for reply-to ID parsing and LLM-based context extraction."""

import json
from unittest.mock import MagicMock, patch

import pytest

from sotto.reply_parser import (
    ExtractionResult,
    ReplyParseResult,
    _parse_extraction_response,
    extract_context,
    parse_reply,
)


class TestParseReply:
    """Test the core parse_reply function with various input formats."""

    def test_re_colon_with_id(self):
        result = parse_reply("Re: A4F2 The task is complete.")
        assert result.reply_to == "A4F2"
        assert result.body == "The task is complete."

    def test_reply_to_with_id(self):
        result = parse_reply("Reply to B7X3, here are the results.")
        assert result.reply_to == "B7X3"
        assert result.body == "here are the results."

    def test_re_colon_lowercase(self):
        result = parse_reply("re: a4f2 status update on the deployment")
        assert result.reply_to == "A4F2"
        assert result.body == "status update on the deployment"

    def test_reply_to_mixed_case(self):
        result = parse_reply("Reply To x9Z1 I've reviewed the report and it looks good.")
        assert result.reply_to == "X9Z1"
        assert result.body == "I've reviewed the report and it looks good."

    def test_no_reply_prefix(self):
        result = parse_reply("Just a normal transcript about the project.")
        assert result.reply_to is None
        assert result.body == "Just a normal transcript about the project."

    def test_empty_string(self):
        result = parse_reply("")
        assert result.reply_to is None
        assert result.body == ""

    def test_re_with_dash_separator(self):
        result = parse_reply("Re: C3D8 - the analysis is done")
        assert result.reply_to == "C3D8"
        assert result.body == "the analysis is done"

    def test_re_with_colon_after_id(self):
        result = parse_reply("Re: F1G2: here is my response")
        assert result.reply_to == "F1G2"
        assert result.body == "here is my response"

    def test_whitespace_around_transcript(self):
        result = parse_reply("  Re: A4F2  The task is complete.  ")
        assert result.reply_to == "A4F2"
        assert result.body == "The task is complete."

    def test_id_only_digits(self):
        result = parse_reply("Re: 1234 done with the review")
        assert result.reply_to == "1234"
        assert result.body == "done with the review"

    def test_id_only_letters(self):
        result = parse_reply("Re: ABCD confirmed the findings")
        assert result.reply_to == "ABCD"
        assert result.body == "confirmed the findings"

    def test_single_char_id(self):
        result = parse_reply("Re: A yes I agree")
        assert result.reply_to == "A"
        assert result.body == "yes I agree"

    def test_max_length_id(self):
        result = parse_reply("Re: ABCDEFGHIJKL the rest of the message")
        assert result.reply_to == "ABCDEFGHIJKL"
        assert result.body == "the rest of the message"

    def test_id_too_long_no_match(self):
        """IDs longer than 12 chars should not match."""
        result = parse_reply("Re: ABCDEFGHIJKLM this is too long")
        assert result.reply_to is None

    def test_re_without_id_no_match(self):
        """'Re:' followed by a non-alphanumeric should not match."""
        result = parse_reply("Re: , some text")
        assert result.reply_to is None

    def test_re_in_middle_no_match(self):
        """Reply prefix must be at the start of the transcript."""
        result = parse_reply("I want to say Re: A4F2 something")
        assert result.reply_to is None

    def test_multiline_body(self):
        result = parse_reply("Re: Q7R3 First line.\nSecond line.\nThird line.")
        assert result.reply_to == "Q7R3"
        assert "First line." in result.body
        assert "Third line." in result.body

    def test_reply_to_with_extra_spaces(self):
        result = parse_reply("Reply   to   Z9Y8   got it")
        assert result.reply_to == "Z9Y8"
        assert result.body == "got it"

    def test_re_id_with_body_only_whitespace(self):
        """Reply with an ID but empty body."""
        result = parse_reply("Re: A4F2")
        assert result.reply_to == "A4F2"
        assert result.body == ""


class TestReplyParseResultDataclass:
    def test_dataclass_fields(self):
        r = ReplyParseResult(reply_to="ABC", body="hello")
        assert r.reply_to == "ABC"
        assert r.body == "hello"

    def test_none_reply_to(self):
        r = ReplyParseResult(reply_to=None, body="hello")
        assert r.reply_to is None


# ---------------------------------------------------------------------------
# LLM-based extraction tests
# ---------------------------------------------------------------------------


class TestParseExtractionResponse:
    """Test parsing of LLM JSON responses."""

    def test_valid_response_with_both_fields(self):
        raw = json.dumps({
            "reply_to": "A4F2",
            "project": "indigo-lease",
            "body": "The deployment is complete.",
        })
        result = _parse_extraction_response(raw, ["indigo-lease", "sotto"], "original")
        assert result.reply_to == "A4F2"
        assert result.project == "indigo-lease"
        assert result.body == "The deployment is complete."

    def test_valid_response_reply_only(self):
        raw = json.dumps({
            "reply_to": "B7X3",
            "project": None,
            "body": "Got it, thanks.",
        })
        result = _parse_extraction_response(raw, ["sotto"], "original")
        assert result.reply_to == "B7X3"
        assert result.project is None
        assert result.body == "Got it, thanks."

    def test_valid_response_project_only(self):
        raw = json.dumps({
            "reply_to": None,
            "project": "sotto",
            "body": "I want to add a new feature to sotto.",
        })
        result = _parse_extraction_response(raw, ["sotto", "indigo-lease"], "original")
        assert result.reply_to is None
        assert result.project == "sotto"

    def test_project_case_insensitive_match(self):
        raw = json.dumps({
            "reply_to": None,
            "project": "Indigo-Lease",
            "body": "Update the lease workflow.",
        })
        result = _parse_extraction_response(raw, ["indigo-lease"], "original")
        assert result.project == "indigo-lease"

    def test_hallucinated_project_rejected(self):
        """If LLM returns a project not in the valid list, it should be None."""
        raw = json.dumps({
            "reply_to": None,
            "project": "nonexistent-project",
            "body": "Some text.",
        })
        result = _parse_extraction_response(raw, ["sotto", "indigo-lease"], "original")
        assert result.project is None

    def test_reply_to_normalized_uppercase(self):
        raw = json.dumps({
            "reply_to": "a4f2",
            "project": None,
            "body": "Done.",
        })
        result = _parse_extraction_response(raw, [], "original")
        assert result.reply_to == "A4F2"

    def test_reply_to_with_extra_chars_stripped(self):
        """LLM might include quotes or spaces in the ID."""
        raw = json.dumps({
            "reply_to": " A4-F2 ",
            "project": None,
            "body": "Done.",
        })
        result = _parse_extraction_response(raw, [], "original")
        assert result.reply_to == "A4F2"

    def test_null_string_treated_as_none(self):
        raw = json.dumps({
            "reply_to": "null",
            "project": "null",
            "body": "Normal transcript.",
        })
        result = _parse_extraction_response(raw, ["sotto"], "original")
        assert result.reply_to is None
        assert result.project is None

    def test_unparseable_response_falls_back_to_regex(self):
        raw = "This is not JSON at all"
        result = _parse_extraction_response(
            raw, ["sotto"], "Re: A4F2 Some transcript"
        )
        # Should fall back to regex parsing
        assert result.reply_to == "A4F2"
        assert result.project is None

    def test_empty_body_falls_back_to_original(self):
        raw = json.dumps({
            "reply_to": "A4F2",
            "project": None,
            "body": "",
        })
        result = _parse_extraction_response(raw, [], "Original transcript text")
        assert result.body == "Original transcript text"

    def test_json_wrapped_in_markdown(self):
        """LLMs sometimes wrap JSON in markdown code blocks."""
        raw = '```json\n{"reply_to": "X9Z1", "project": "sotto", "body": "the plan"}\n```'
        result = _parse_extraction_response(raw, ["sotto"], "original")
        assert result.reply_to == "X9Z1"
        assert result.project == "sotto"


class TestExtractContext:
    """Test the extract_context function (with mocked Ollama calls)."""

    def test_no_projects_skips_llm(self):
        """When no projects are configured, skip LLM and use regex."""
        result = extract_context(
            transcript="Re: A4F2 The task is done.",
            project_names=[],
            ollama_endpoint="http://localhost:11434",
        )
        assert result.reply_to == "A4F2"
        assert result.project is None
        assert result.body == "The task is done."

    def test_no_projects_no_reply(self):
        result = extract_context(
            transcript="Just a normal note.",
            project_names=[],
            ollama_endpoint="http://localhost:11434",
        )
        assert result.reply_to is None
        assert result.project is None
        assert result.body == "Just a normal note."

    @patch("sotto.reply_parser.httpx.post")
    def test_successful_llm_extraction(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps({
                "reply_to": "A4F2",
                "project": "indigo-lease",
                "body": "The lease workflow is updated.",
            })
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = extract_context(
            transcript="Reply to A4F2, the indigo lease workflow is updated.",
            project_names=["indigo-lease", "sotto"],
            ollama_endpoint="http://localhost:11434",
            model="llama3.1:8b",
        )
        assert result.reply_to == "A4F2"
        assert result.project == "indigo-lease"
        assert "lease workflow" in result.body

    @patch("sotto.reply_parser.httpx.post")
    def test_fuzzy_project_match(self, mock_post):
        """Simulates Whisper transcribing 'indigo lease' (two words) matching 'indigo-lease'."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps({
                "reply_to": None,
                "project": "indigo-lease",
                "body": "I want to update the indigo lease deployment pipeline.",
            })
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = extract_context(
            transcript="I want to update the indigo lease deployment pipeline.",
            project_names=["indigo-lease", "sotto"],
            ollama_endpoint="http://localhost:11434",
        )
        assert result.project == "indigo-lease"
        assert result.reply_to is None

    @patch("sotto.reply_parser.httpx.post")
    def test_ollama_failure_falls_back_to_regex(self, mock_post):
        """If Ollama is down, fall back to regex parsing."""
        mock_post.side_effect = Exception("Connection refused")

        result = extract_context(
            transcript="Re: B7X3 The analysis results are in.",
            project_names=["sotto"],
            ollama_endpoint="http://localhost:11434",
        )
        # Should still get the reply_to from regex fallback
        assert result.reply_to == "B7X3"
        assert result.project is None
        assert "analysis results" in result.body

    @patch("sotto.reply_parser.httpx.post")
    def test_ollama_returns_garbage(self, mock_post):
        """If Ollama returns nonsense, fall back to regex."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "I don't understand the question."}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = extract_context(
            transcript="Re: C3D8 status update",
            project_names=["sotto"],
            ollama_endpoint="http://localhost:11434",
        )
        # Falls back to regex due to JSON parse failure in _parse_extraction_response
        assert result.reply_to == "C3D8"

    @patch("sotto.reply_parser.httpx.post")
    def test_prompt_includes_project_names(self, mock_post):
        """Verify the prompt sent to Ollama includes the project names."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps({
                "reply_to": None,
                "project": None,
                "body": "test",
            })
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        extract_context(
            transcript="test transcript",
            project_names=["indigo-lease", "sotto", "my-app"],
            ollama_endpoint="http://localhost:11434",
        )

        # Check the prompt includes our project names
        call_args = mock_post.call_args
        prompt = call_args[1]["json"]["prompt"] if "json" in call_args[1] else call_args[0][0]
        assert "indigo-lease" in prompt
        assert "sotto" in prompt
        assert "my-app" in prompt


class TestExtractionResultDataclass:
    def test_defaults(self):
        r = ExtractionResult()
        assert r.reply_to is None
        assert r.project is None
        assert r.body == ""

    def test_all_fields(self):
        r = ExtractionResult(reply_to="A4F2", project="sotto", body="hello")
        assert r.reply_to == "A4F2"
        assert r.project == "sotto"
        assert r.body == "hello"
