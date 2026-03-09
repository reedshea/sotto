"""Tests for reply-to ID parsing from transcription text."""

import pytest

from sotto.reply_parser import ReplyParseResult, parse_reply


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
