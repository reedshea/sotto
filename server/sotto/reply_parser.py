"""Reply-to parser — extracts reply IDs from transcription text.

When a transcription begins with "Re:" or "Reply to" followed by a short
alphanumeric identifier (e.g. "Re: A4F2"), this module separates that ID
from the body text so downstream routing can match the response to the
correct agent session or report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Match "Re:" or "Reply to" (case-insensitive), optional whitespace,
# then a short alphanumeric ID (1-12 chars, letters and digits),
# followed by optional punctuation and whitespace before the body.
_REPLY_PATTERN = re.compile(
    r"^(?:re:\s*|reply\s+to\s+)([A-Za-z0-9]{1,12})(?=[^A-Za-z0-9]|$)[\s,.:;—\-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ReplyParseResult:
    """Result of parsing a transcript for a reply-to prefix."""

    reply_to: str | None
    """The extracted alphanumeric reply ID, uppercased, or None."""

    body: str
    """The transcript body with the reply prefix removed."""


def parse_reply(transcript: str) -> ReplyParseResult:
    """Parse a transcript for a reply-to prefix.

    Examples:
        >>> parse_reply("Re: A4F2 The task is complete.")
        ReplyParseResult(reply_to='A4F2', body='The task is complete.')

        >>> parse_reply("Reply to B7X3, here are the results.")
        ReplyParseResult(reply_to='B7X3', body='here are the results.')

        >>> parse_reply("Just a normal transcript.")
        ReplyParseResult(reply_to=None, body='Just a normal transcript.')
    """
    stripped = transcript.strip()
    match = _REPLY_PATTERN.match(stripped)

    if match:
        reply_id = match.group(1).upper()
        body = match.group(2).strip()
        return ReplyParseResult(reply_to=reply_id, body=body)

    return ReplyParseResult(reply_to=None, body=transcript)
