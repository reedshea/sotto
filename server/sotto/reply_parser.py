"""Reply-to parser and context extractor — extracts reply IDs and project
references from transcription text.

Two extraction modes:

1. **Regex fast-path** (`parse_reply`): Cheap, no network call. Catches clean
   "Re: A4F2" / "Reply to A4F2" prefixes when Whisper transcribes them literally.

2. **LLM extraction** (`extract_context`): Calls local Ollama to fuzzy-match
   both the reply-to identifier and the project name against a list of known
   projects. Handles spoken variations like "indigo lease" matching config key
   "indigo-lease".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("sotto.reply_parser")

# ---------------------------------------------------------------------------
# Regex fast-path
# ---------------------------------------------------------------------------

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
    """Parse a transcript for a reply-to prefix using regex.

    This is the fast-path — no LLM call. Use `extract_context` for
    fuzzy matching of both reply IDs and project names.

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


# ---------------------------------------------------------------------------
# LLM-based extraction (Ollama)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are a metadata extractor for a voice transcription system. A user dictated a voice memo and it was transcribed. Your job is to extract two pieces of structured metadata from the transcript.

1. **reply_to**: If the user is replying to or referencing a previous report/session by a short alphanumeric identifier (like "A4F2", "B7X3", "7G2K"), extract that identifier. Look for phrases like "Re:", "Reply to", "Regarding", "Response to", "about report", "for ticket", "on ID", or any indication they are referencing a specific identifier. The identifier is typically 2-8 alphanumeric characters. Return it in UPPERCASE. If there is no identifier being referenced, return null.

2. **project**: The user may mention which project they are talking about. Match what they said to one of the known project names below. Spoken names may differ from config names (e.g. "indigo lease" should match "indigo-lease", "sotto" might be said as "soto"). Use fuzzy matching — pick the best match. If no project is mentioned or nothing matches, return null.

Known projects:
{projects_list}

Respond in exactly this JSON format, nothing else:
{{"reply_to": "<ID or null>", "project": "<matched project name from the list above, or null>", "body": "<the transcript with any reply prefix removed>"}}

Transcript:
{transcript}"""


@dataclass
class ExtractionResult:
    """Result of LLM-based context extraction from a transcript."""

    reply_to: str | None = None
    """The extracted reply identifier, uppercased, or None."""

    project: str | None = None
    """The matched project name (as it appears in config), or None."""

    body: str = ""
    """The transcript body with any reply prefix removed."""


def extract_context(
    transcript: str,
    project_names: list[str],
    ollama_endpoint: str,
    model: str = "llama3.1:8b",
    timeout: float = 30.0,
) -> ExtractionResult:
    """Use local Ollama LLM to extract reply-to ID and project from a transcript.

    Falls back to regex-only parsing if Ollama is unreachable or returns
    an unparseable response.

    Args:
        transcript: The raw transcription text.
        project_names: List of configured project names to match against.
        ollama_endpoint: Ollama API endpoint (e.g. "http://localhost:11434").
        model: Ollama model to use.
        timeout: Request timeout in seconds.

    Returns:
        ExtractionResult with reply_to, project, and cleaned body.
    """
    # If no projects are configured, skip the LLM call and use regex
    if not project_names:
        regex_result = parse_reply(transcript)
        return ExtractionResult(
            reply_to=regex_result.reply_to,
            project=None,
            body=regex_result.body,
        )

    projects_list = "\n".join(f"- {name}" for name in project_names)
    prompt = EXTRACTION_PROMPT.format(
        projects_list=projects_list,
        transcript=transcript[:4000],
    )

    try:
        resp = httpx.post(
            f"{ollama_endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()["response"]
        result = _parse_extraction_response(raw, project_names, transcript)
        logger.info(
            "LLM extraction: reply_to=%s, project=%s",
            result.reply_to, result.project,
        )
        return result

    except Exception as e:
        logger.warning("LLM extraction failed (%s), falling back to regex", e)
        regex_result = parse_reply(transcript)
        return ExtractionResult(
            reply_to=regex_result.reply_to,
            project=None,
            body=regex_result.body,
        )


def _parse_extraction_response(
    raw: str,
    valid_projects: list[str],
    original_transcript: str,
) -> ExtractionResult:
    """Parse the LLM JSON response into an ExtractionResult.

    Validates that the project name matches one of the known projects
    (case-insensitive). Normalizes the reply_to ID to uppercase.
    """
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("Failed to parse LLM extraction response")
        regex_result = parse_reply(original_transcript)
        return ExtractionResult(
            reply_to=regex_result.reply_to,
            project=None,
            body=regex_result.body,
        )

    # Validate and normalize reply_to
    reply_to = data.get("reply_to")
    if reply_to and isinstance(reply_to, str) and reply_to.lower() != "null":
        # Strip any non-alphanumeric chars the LLM might have added
        cleaned = re.sub(r"[^A-Za-z0-9]", "", reply_to)
        reply_to = cleaned.upper() if cleaned else None
    else:
        reply_to = None

    # Validate project against known list (case-insensitive)
    project = data.get("project")
    if project and isinstance(project, str) and project.lower() != "null":
        project_lower = project.lower().strip()
        matched = None
        for valid_name in valid_projects:
            if valid_name.lower() == project_lower:
                matched = valid_name
                break
        project = matched  # None if LLM hallucinated a project name
    else:
        project = None

    # Use the body from LLM response, fall back to original
    body = data.get("body", "").strip()
    if not body:
        body = original_transcript

    return ExtractionResult(
        reply_to=reply_to,
        project=project,
        body=body,
    )
