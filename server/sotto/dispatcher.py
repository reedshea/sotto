"""Dispatcher — routes classified transcriptions to their output destinations.

After a transcript is classified by intent, the dispatcher determines what to do
with it: write Obsidian-formatted markdown, generate a draft via Claude, extract
tasks, append to a journal, etc.

Output destinations are organized for Obsidian vault consumption:
  vault_root/
    inbox/          <- items needing review
    notes/          <- note_to_self
    meetings/       <- meeting_debrief
    journal/        <- daily journal entries (appended)
    drafts/         <- draft_request outputs (Claude-generated)
    ideas/          <- idea captures
    tasks/          <- extracted action items
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .classifier import ClassificationResult
from .config import Config, PipelineConfig

logger = logging.getLogger("sotto.dispatcher")


class Dispatcher:
    """Routes completed, classified transcriptions to output destinations."""

    def __init__(self, config: Config):
        self.config = config
        self._vault_root = self._resolve_vault_root()

    def _resolve_vault_root(self) -> Path:
        """Determine the Obsidian vault output root from config."""
        destinations = getattr(self.config, "destinations", None)
        if destinations and hasattr(destinations, "obsidian_vault"):
            return Path(destinations.obsidian_vault).expanduser()
        # Default: output_dir/vault
        return self.config.storage.output_dir / "vault"

    def dispatch(
        self,
        uuid: str,
        transcript: str,
        classification: ClassificationResult,
        title: str,
        summary: str,
        duration: float,
        privacy: str,
        pipeline: PipelineConfig,
        created_at: str,
        reply_to: str | None = None,
        resolved_project: str | None = None,
    ) -> dict:
        """Dispatch a classified transcript to the appropriate handler.

        Returns a dict with dispatch results (paths written, actions taken).
        """
        intent = classification.intent
        now = datetime.now(timezone.utc)

        handler = {
            "note_to_self": self._handle_note,
            "meeting_debrief": self._handle_meeting,
            "journal": self._handle_journal,
            "draft_request": self._handle_draft_request,
            "plan_request": self._handle_plan_request,
            "task": self._handle_task,
            "idea": self._handle_idea,
            "general": self._handle_general,
        }.get(intent, self._handle_general)

        try:
            result = handler(
                uuid=uuid,
                transcript=transcript,
                classification=classification,
                title=title,
                summary=summary,
                duration=duration,
                privacy=privacy,
                pipeline=pipeline,
                created_at=created_at,
                now=now,
                reply_to=reply_to,
                resolved_project=resolved_project,
            )
            result["intent"] = intent
            result["dispatched_at"] = now.isoformat()
            logger.info("Dispatched %s as '%s' -> %s", uuid, intent, result.get("path", "n/a"))
            return result
        except Exception as e:
            logger.exception("Dispatch failed for %s", uuid)
            return {"intent": intent, "error": str(e), "dispatched_at": now.isoformat()}

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    def _handle_note(self, **kwargs) -> dict:
        """Write a note-to-self as an Obsidian markdown file."""
        dest_dir = self._vault_root / "notes"
        path = self._write_markdown(dest_dir, **kwargs)
        return {"path": str(path), "action": "note_created"}

    def _handle_meeting(self, **kwargs) -> dict:
        """Write meeting debrief as structured markdown."""
        dest_dir = self._vault_root / "meetings"
        path = self._write_markdown(dest_dir, **kwargs)
        return {"path": str(path), "action": "meeting_notes_created"}

    def _handle_journal(self, **kwargs) -> dict:
        """Append to a daily journal file."""
        dest_dir = self._vault_root / "journal"
        dest_dir.mkdir(parents=True, exist_ok=True)

        now = kwargs["now"]
        date_str = now.strftime("%Y-%m-%d")
        journal_path = dest_dir / f"{date_str}.md"

        entry = self._format_journal_entry(**kwargs)

        if journal_path.exists():
            existing = journal_path.read_text(encoding="utf-8")
            journal_path.write_text(existing + "\n\n---\n\n" + entry, encoding="utf-8")
        else:
            header = f"# Journal — {date_str}\n\n"
            journal_path.write_text(header + entry, encoding="utf-8")

        return {"path": str(journal_path), "action": "journal_appended"}

    def _handle_draft_request(self, **kwargs) -> dict:
        """Send transcript to Claude for drafting, save result as markdown."""
        dest_dir = self._vault_root / "drafts"
        transcript = kwargs["transcript"]
        classification = kwargs["classification"]
        pipeline = kwargs["pipeline"]
        uuid = kwargs["uuid"]

        # Generate the draft using Claude
        draft = self._generate_draft(transcript, classification, pipeline)

        # Write both the original request and the draft
        dest_dir.mkdir(parents=True, exist_ok=True)
        now = kwargs["now"]
        date_str = now.strftime("%Y-%m-%d")
        slug = self._slugify(kwargs["title"])
        path = dest_dir / f"{date_str}-{slug}.md"

        content = self._format_draft_output(draft=draft, **kwargs)
        path.write_text(content, encoding="utf-8")

        return {"path": str(path), "action": "draft_generated", "draft_length": len(draft)}

    def _handle_plan_request(self, **kwargs) -> dict:
        """Resolve a project, invoke Claude Code CLI to explore and plan, save result."""
        transcript = kwargs["transcript"]
        classification = kwargs["classification"]
        title = kwargs["title"]
        uuid = kwargs["uuid"]
        now = kwargs["now"]

        # Resolve which project the user is talking about
        resolved_project = kwargs.get("resolved_project")
        project_name, project_path = self._resolve_project(classification, resolved_project)

        # Write initial plan-request note to vault
        dest_dir = self._vault_root / "plans"
        dest_dir.mkdir(parents=True, exist_ok=True)
        date_str = now.strftime("%Y-%m-%d")
        slug = self._slugify(title)
        plan_vault_path = dest_dir / f"{date_str}-{slug}.md"

        # If we have a project path, invoke Claude Code CLI
        plan_output = None
        if project_path:
            plan_output = self._invoke_claude_plan(transcript, project_path, classification)

        # Write the plan file
        content = self._format_plan_output(
            transcript=transcript,
            classification=classification,
            title=title,
            uuid=uuid,
            now=now,
            project_name=project_name,
            project_path=project_path,
            plan_output=plan_output,
        )
        plan_vault_path.write_text(content, encoding="utf-8")

        # Also write a PLAN.md into the project repo if we have a path
        plan_repo_path = None
        if project_path and plan_output:
            repo_plan_dir = Path(project_path).expanduser() / ".claude" / "plans"
            repo_plan_dir.mkdir(parents=True, exist_ok=True)
            plan_repo_path = repo_plan_dir / f"{date_str}-{slug}.md"
            plan_repo_path.write_text(content, encoding="utf-8")
            logger.info("Plan written to repo: %s", plan_repo_path)

        result = {
            "path": str(plan_vault_path),
            "action": "plan_generated" if plan_output else "plan_request_saved",
            "project": project_name,
            "project_path": project_path,
        }
        if plan_repo_path:
            result["repo_plan_path"] = str(plan_repo_path)
        return result

    def _resolve_project(
        self,
        classification: ClassificationResult,
        resolved_project: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Match mentioned projects in the classification to configured projects.

        If `resolved_project` is provided (from LLM extraction in the worker),
        it takes priority over classification entity matching.

        Returns (project_name, project_path) or (None, None) if no match.
        """
        projects = getattr(self.config, "projects", {})
        if not projects:
            return None, None

        # Prefer the LLM-resolved project from the extraction step
        if resolved_project and resolved_project in projects:
            proj = projects[resolved_project]
            return resolved_project, str(Path(proj.path).expanduser())

        # Fallback: check entities.projects from the classifier
        mentioned = [p.lower() for p in classification.entities.get("projects", [])]

        for name, proj in projects.items():
            # Match by project config key
            if name.lower() in mentioned:
                return name, str(Path(proj.path).expanduser())
            # Match by aliases
            for alias in proj.aliases:
                if alias.lower() in mentioned:
                    return name, str(Path(proj.path).expanduser())

        # If only one project is configured, use it as default
        if len(projects) == 1:
            name = next(iter(projects))
            proj = projects[name]
            return name, str(Path(proj.path).expanduser())

        return None, None

    def _invoke_claude_plan(
        self, transcript: str, project_path: str, classification: ClassificationResult
    ) -> str | None:
        """Run Claude Code CLI to explore the codebase and create a plan."""
        prompt = f"""You received the following voice dictation from the user describing work they want planned on this codebase. Please:

1. Explore the relevant parts of the codebase to understand the current state
2. Create a detailed implementation plan based on what they described
3. Output the plan in markdown format

The user dictated:

{transcript}

Context from classification:
- Subject: {classification.subject}
- Key entities: {json.dumps(classification.entities)}
- Action items: {json.dumps(classification.action_items)}

Please explore the codebase and produce a concrete, actionable implementation plan."""

        try:
            result = subprocess.run(
                ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=project_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.info("Claude Code plan generated (%d chars)", len(result.stdout))
                return result.stdout.strip()
            else:
                logger.warning(
                    "Claude Code returned code %d: %s",
                    result.returncode,
                    result.stderr[:500] if result.stderr else "no stderr",
                )
                return None
        except FileNotFoundError:
            logger.warning("Claude CLI not found on PATH — skipping plan generation")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Claude Code timed out after 600s")
            return None
        except Exception as e:
            logger.warning("Claude Code invocation failed: %s", e)
            return None

    def _format_plan_output(
        self,
        transcript: str,
        classification: ClassificationResult,
        title: str,
        uuid: str,
        now: datetime,
        project_name: str | None,
        project_path: str | None,
        plan_output: str | None,
    ) -> str:
        """Format a plan request as an Obsidian markdown note."""
        date_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        status = "plan-ready" if plan_output else "plan-pending"

        tags = ["sotto/plan_request"]
        if plan_output:
            tags.append("needs-review")
        else:
            tags.append("needs-planning")

        lines = [
            "---",
            f'title: "Plan: {title}"',
            f"date: {date_str}",
            "intent: plan_request",
            f"subject: {classification.subject}",
            f"status: {status}",
            f"project: {project_name or 'unknown'}",
            "tags:",
        ]
        for tag in tags:
            lines.append(f"  - {tag}")
        lines.extend([
            f"uuid: {uuid}",
            "---",
            "",
            f"# Plan: {title}",
            "",
            f"> **Status:** {status}",
            f"> **Project:** {project_name or 'not resolved'} (`{project_path or 'N/A'}`)",
            f"> **Generated from voice memo on** {now.strftime('%Y-%m-%d at %H:%M')}",
            "",
        ])

        if plan_output:
            lines.extend([
                "## Implementation Plan",
                "",
                plan_output,
                "",
                "---",
                "",
            ])
        else:
            lines.extend([
                "## Plan",
                "",
                "*Plan generation pending — Claude CLI was not available or no project was resolved.*",
                "",
                "---",
                "",
            ])

        lines.extend([
            "## Original Dictation",
            "",
            transcript,
            "",
            "---",
            "",
            "## Classification Details",
            "",
            f"- **Subject:** {classification.subject}",
            f"- **Action items:** {', '.join(classification.action_items) if classification.action_items else 'None'}",
            f"- **Reasoning:** {classification.reasoning}",
            "",
        ])

        return "\n".join(lines)

    def _handle_task(self, **kwargs) -> dict:
        """Extract and write action items."""
        dest_dir = self._vault_root / "tasks"
        path = self._write_markdown(dest_dir, **kwargs)
        return {"path": str(path), "action": "tasks_extracted"}

    def _handle_idea(self, **kwargs) -> dict:
        """Capture an idea as markdown."""
        dest_dir = self._vault_root / "ideas"
        path = self._write_markdown(dest_dir, **kwargs)
        return {"path": str(path), "action": "idea_captured"}

    def _handle_general(self, **kwargs) -> dict:
        """Default handler — write to inbox for review."""
        dest_dir = self._vault_root / "inbox"
        path = self._write_markdown(dest_dir, **kwargs)
        return {"path": str(path), "action": "filed_to_inbox"}

    # ------------------------------------------------------------------
    # Markdown formatting
    # ------------------------------------------------------------------

    def _write_markdown(self, dest_dir: Path, **kwargs) -> Path:
        """Write a standard Obsidian markdown note."""
        dest_dir.mkdir(parents=True, exist_ok=True)

        now = kwargs["now"]
        date_str = now.strftime("%Y-%m-%d")
        slug = self._slugify(kwargs["title"])
        path = dest_dir / f"{date_str}-{slug}.md"

        content = self._format_markdown(**kwargs)
        path.write_text(content, encoding="utf-8")
        return path

    def _format_markdown(self, **kwargs) -> str:
        """Format a transcript as an Obsidian-compatible markdown note with YAML frontmatter."""
        classification: ClassificationResult = kwargs["classification"]
        title = kwargs["title"]
        summary = kwargs["summary"]
        transcript = kwargs["transcript"]
        uuid = kwargs["uuid"]
        duration = kwargs["duration"]
        created_at = kwargs["created_at"]
        now = kwargs["now"]
        reply_to = kwargs.get("reply_to")

        # YAML frontmatter for Obsidian
        tags = [f"sotto/{classification.intent}"]
        if classification.urgency == "high":
            tags.append("urgent")
        if reply_to:
            tags.append("sotto/reply")

        frontmatter_data = {
            "title": title,
            "date": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "intent": classification.intent,
            "subject": classification.subject,
            "urgency": classification.urgency,
            "tags": tags,
            "uuid": uuid,
            "duration_seconds": round(duration, 1),
            "captured_at": created_at,
        }
        if reply_to:
            frontmatter_data["reply_to"] = reply_to

        # Build frontmatter manually for clean YAML
        fm_lines = ["---"]
        for key, val in frontmatter_data.items():
            if isinstance(val, list):
                fm_lines.append(f"{key}:")
                for item in val:
                    fm_lines.append(f"  - {item}")
            else:
                fm_lines.append(f"{key}: {val}")
        fm_lines.append("---")
        frontmatter = "\n".join(fm_lines)

        # Body
        sections = [frontmatter, "", f"# {title}", ""]

        if summary:
            sections.extend([f"> {summary}", ""])

        # People and projects as links
        entities = classification.entities
        if entities.get("people"):
            people_links = ", ".join(f"[[{p}]]" for p in entities["people"])
            sections.append(f"**People:** {people_links}")
        if entities.get("projects"):
            project_links = ", ".join(f"[[{p}]]" for p in entities["projects"])
            sections.append(f"**Projects:** {project_links}")
        if entities.get("people") or entities.get("projects"):
            sections.append("")

        # Action items as checkboxes
        if classification.action_items:
            sections.append("## Action Items")
            for item in classification.action_items:
                sections.append(f"- [ ] {item}")
            sections.append("")

        # Transcript
        sections.extend(["## Transcript", "", transcript, ""])

        return "\n".join(sections)

    def _format_journal_entry(self, **kwargs) -> str:
        """Format a journal entry (no frontmatter, just content for appending)."""
        now = kwargs["now"]
        title = kwargs["title"]
        transcript = kwargs["transcript"]
        classification: ClassificationResult = kwargs["classification"]

        time_str = now.strftime("%H:%M")
        lines = [f"## {time_str} — {title}", ""]

        if classification.subject:
            lines.append(f"*{classification.subject}*")
            lines.append("")

        lines.append(transcript)

        if classification.action_items:
            lines.append("")
            lines.append("**Action items:**")
            for item in classification.action_items:
                lines.append(f"- [ ] {item}")

        return "\n".join(lines)

    def _format_draft_output(self, **kwargs) -> str:
        """Format a draft request output with both the original dictation and the generated draft."""
        classification: ClassificationResult = kwargs["classification"]
        title = kwargs["title"]
        transcript = kwargs["transcript"]
        draft = kwargs["draft"]
        uuid = kwargs["uuid"]
        now = kwargs["now"]

        frontmatter = f"""---
title: "Draft: {title}"
date: {now.strftime("%Y-%m-%dT%H:%M:%S")}
intent: draft_request
subject: {classification.subject}
tags:
  - sotto/draft_request
  - needs-review
uuid: {uuid}
---"""

        return f"""{frontmatter}

# Draft: {title}

> **Status:** Needs review
> **Generated from voice memo on** {now.strftime("%Y-%m-%d at %H:%M")}

## Generated Draft

{draft}

---

## Original Dictation

{transcript}

---

## Classification Details

- **Subject:** {classification.subject}
- **Action items:** {", ".join(classification.action_items) if classification.action_items else "None"}
- **Reasoning:** {classification.reasoning}
"""

    # ------------------------------------------------------------------
    # Draft generation (Claude)
    # ------------------------------------------------------------------

    def _generate_draft(
        self, transcript: str, classification: ClassificationResult, pipeline: PipelineConfig
    ) -> str:
        """Send the transcript to an LLM to generate a draft based on the dictated request."""
        prompt = f"""You are helping a user who has dictated their thoughts about something they want drafted. They spoke into a voice recorder and this is the transcription of what they said.

Your job: Produce a well-structured draft based on what they described. This could be:
- A feature spec or technical proposal
- An architectural plan
- A project brief
- An email or message
- A document outline

Infer the appropriate format from the content. Be thorough but concise. Use markdown formatting.

If they mentioned specific requirements, constraints, or context, incorporate all of it.
If they described an architectural approach, produce a structured technical plan with clear sections.

Here is what they dictated:

{transcript}

Additional context from classification:
- Subject: {classification.subject}
- Key entities: {json.dumps(classification.entities)}
- Extracted action items: {json.dumps(classification.action_items)}

Produce the draft now:"""

        try:
            if pipeline.llm_backend == "anthropic":
                return self._call_anthropic_draft(prompt, pipeline.model)
            elif pipeline.llm_backend == "ollama":
                return self._call_ollama_draft(prompt, pipeline.model)
            elif pipeline.llm_backend == "openai":
                return self._call_openai_draft(prompt, pipeline.model)
            else:
                return f"*Draft generation not available for backend: {pipeline.llm_backend}*"
        except Exception as e:
            logger.warning("Draft generation failed: %s", e)
            return f"*Draft generation failed: {e}*\n\nOriginal transcript preserved above for manual drafting."

    def _call_anthropic_draft(self, prompt: str, model: str) -> str:
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
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _call_ollama_draft(self, prompt: str, model: str) -> str:
        endpoint = self.config.ollama.endpoint
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]

    def _call_openai_draft(self, prompt: str, model: str) -> str:
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
                "max_tokens": 4096,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a filesystem-safe slug."""
        import re
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug[:60].rstrip("-")
