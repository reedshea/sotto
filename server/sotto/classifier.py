"""Intent classifier — analyzes transcripts to determine what to do with them.

Takes a raw transcript and produces structured metadata:
- intent: what kind of recording this is
- subject: free-text topic
- urgency: low / normal / high
- entities: people, projects, dates mentioned
- action_items: extracted tasks or requests
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from .config import Config, PipelineConfig

logger = logging.getLogger("sotto.classifier")

# Intents the classifier can assign
INTENTS = {
    "note_to_self": "A quick personal reminder or thought to capture",
    "meeting_debrief": "Notes from a conversation or meeting with others",
    "journal": "Reflective thinking, stream of consciousness, personal observations",
    "draft_request": "A request to draft something — a proposal, email, plan, feature spec, etc.",
    "plan_request": "A request to investigate a codebase and create an implementation plan for a feature, refactor, or bug fix",
    "task": "One or more actionable items with clear next steps",
    "idea": "An idea for a project, feature, product, or creative endeavor",
    "general": "Anything that doesn't fit the above categories",
}

CLASSIFICATION_PROMPT = """You are an intent classifier for a voice transcription system. A user has recorded a voice memo and it has been transcribed. Your job is to analyze the transcript and classify it.

Available intents:
- note_to_self: A quick personal reminder or thought
- meeting_debrief: Notes from a conversation or meeting
- journal: Reflective thinking, stream of consciousness
- draft_request: A request to draft something (proposal, email, plan, feature spec, code architecture)
- plan_request: A request to investigate a codebase/project and create an implementation plan (feature, refactor, bug fix, architecture change). The user is describing work they want done on a specific software project.
- task: Actionable items with clear next steps
- idea: An idea for a project, feature, or creative endeavor
- general: Anything else

Respond in exactly this JSON format, nothing else:
{{
  "intent": "<one of the intents above>",
  "subject": "<brief topic description, 5-15 words>",
  "urgency": "<low|normal|high>",
  "entities": {{
    "people": ["<names mentioned>"],
    "projects": ["<projects or products mentioned>"],
    "dates": ["<dates or deadlines mentioned>"]
  }},
  "action_items": ["<extracted tasks or requests, if any>"],
  "reasoning": "<one sentence explaining why you chose this intent>"
}}

Transcript:
{transcript}"""


@dataclass
class ClassificationResult:
    intent: str = "general"
    subject: str = ""
    urgency: str = "normal"
    entities: dict[str, list[str]] = field(default_factory=lambda: {
        "people": [], "projects": [], "dates": [],
    })
    action_items: list[str] = field(default_factory=list)
    reasoning: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "subject": self.subject,
            "urgency": self.urgency,
            "entities": self.entities,
            "action_items": self.action_items,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClassificationResult:
        return cls(
            intent=data.get("intent", "general"),
            subject=data.get("subject", ""),
            urgency=data.get("urgency", "normal"),
            entities=data.get("entities", {"people": [], "projects": [], "dates": []}),
            action_items=data.get("action_items", []),
            reasoning=data.get("reasoning", ""),
        )


class Classifier:
    """Classifies transcripts by intent using an LLM backend."""

    def __init__(self, config: Config):
        self.config = config

    def classify(self, transcript: str, pipeline: PipelineConfig) -> ClassificationResult:
        """Classify a transcript using the configured LLM backend.

        First checks for fast-path pattern matches in the config, then falls
        back to LLM classification.
        """
        # Fast-path: check user-defined trigger patterns
        fast_result = self._check_patterns(transcript)
        if fast_result:
            logger.info("Fast-path classification: %s", fast_result.intent)
            return fast_result

        # LLM classification
        prompt = CLASSIFICATION_PROMPT.format(transcript=transcript[:8000])

        try:
            if pipeline.llm_backend == "ollama":
                raw = self._call_ollama(prompt, pipeline.model)
            elif pipeline.llm_backend == "anthropic":
                raw = self._call_anthropic(prompt, pipeline.model)
            elif pipeline.llm_backend == "openai":
                raw = self._call_openai(prompt, pipeline.model)
            else:
                logger.warning("Unknown LLM backend: %s", pipeline.llm_backend)
                return ClassificationResult()

            return self._parse_response(raw)

        except Exception as e:
            logger.warning("Classification failed: %s. Defaulting to 'general'.", e)
            return ClassificationResult(reasoning=f"Classification failed: {e}")

    def _check_patterns(self, transcript: str) -> ClassificationResult | None:
        """Check transcript against user-defined trigger patterns from config."""
        patterns = getattr(self.config, "patterns", None)
        if not patterns:
            return None

        lower = transcript.lower().strip()
        for pattern in patterns:
            trigger = (pattern.trigger if hasattr(pattern, "trigger") else pattern.get("trigger", "")).lower()
            if trigger and lower.startswith(trigger):
                intent = pattern.intent if hasattr(pattern, "intent") else pattern.get("intent", "general")
                return ClassificationResult(
                    intent=intent,
                    subject=transcript[:80],
                    reasoning=f"Matched trigger pattern: '{trigger}'",
                )
        return None

    def _parse_response(self, text: str) -> ClassificationResult:
        """Parse the LLM JSON response into a ClassificationResult."""
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])

            # Validate intent
            intent = data.get("intent", "general")
            if intent not in INTENTS:
                logger.warning("Unknown intent '%s', defaulting to 'general'", intent)
                intent = "general"

            return ClassificationResult(
                intent=intent,
                subject=data.get("subject", ""),
                urgency=data.get("urgency", "normal"),
                entities=data.get("entities", {"people": [], "projects": [], "dates": []}),
                action_items=data.get("action_items", []),
                reasoning=data.get("reasoning", ""),
                raw_response=text,
            )
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning("Failed to parse classification response")
            return ClassificationResult(raw_response=text)

    def _call_ollama(self, prompt: str, model: str) -> str:
        endpoint = self.config.ollama.endpoint
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]

    def _call_anthropic(self, prompt: str, model: str) -> str:
        api_key = self.config.api_keys.get("anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not configured")

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _call_openai(self, prompt: str, model: str) -> str:
        api_key = self.config.api_keys.get("openai")
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 512,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
