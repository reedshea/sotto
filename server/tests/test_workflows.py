"""Tests for workflow loading, converters, and multi-destination output."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sotto.config import (
    Config,
    DestinationsConfig,
    OrchestratorConfig,
    PipelineConfig,
    ProjectConfig,
    StorageConfig,
    WorkflowConfig,
    WorkflowOutput,
    load_workflows,
    _load_workflow_file,
)
from sotto.converters import md_to_html
from sotto.orchestrator import Orchestrator, TaskStatus


# ------------------------------------------------------------------
# Workflow loading
# ------------------------------------------------------------------


class TestWorkflowLoading:
    def test_load_workflow_file(self, tmp_path):
        wf_file = tmp_path / "plan.yaml"
        wf_file.write_text("""\
name: plan
triggers:
  - intent: plan_request
prompt: |
  Plan this: {{transcript}}
outputs:
  - destination: obsidian_vault
    path: reports/
    format: markdown
  - destination: e-reader
    path: sotto-reports/
    format: html
""")
        wf = _load_workflow_file(wf_file)
        assert wf is not None
        assert wf.name == "plan"
        assert wf.matches_intent("plan_request")
        assert not wf.matches_intent("code_request")
        assert len(wf.outputs) == 2
        assert wf.outputs[0].destination == "obsidian_vault"
        assert wf.outputs[0].format == "markdown"
        assert wf.outputs[1].destination == "e-reader"
        assert wf.outputs[1].format == "html"
        assert "{{transcript}}" in wf.prompt

    def test_load_workflows_from_dir(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "plan.yaml").write_text("name: plan\ntriggers:\n  - intent: plan_request\nprompt: plan it\n")
        (wf_dir / "build.yaml").write_text("name: build\ntriggers:\n  - intent: code_request\nprompt: build it\n")
        (wf_dir / "not-yaml.txt").write_text("ignored")

        workflows = load_workflows(wf_dir)
        assert len(workflows) == 2
        names = {wf.name for wf in workflows}
        assert names == {"plan", "build"}

    def test_load_workflows_empty_dir(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        assert load_workflows(wf_dir) == []

    def test_load_workflows_nonexistent_dir(self, tmp_path):
        assert load_workflows(tmp_path / "nope") == []

    def test_load_workflow_invalid_yaml(self, tmp_path):
        wf_file = tmp_path / "bad.yaml"
        wf_file.write_text(":::\ninvalid: [yaml")
        wf = _load_workflow_file(wf_file)
        assert wf is None

    def test_workflow_matches_intent(self):
        wf = WorkflowConfig(
            name="test",
            triggers=[{"intent": "plan_request"}, {"intent": "code_request"}],
        )
        assert wf.matches_intent("plan_request")
        assert wf.matches_intent("code_request")
        assert not wf.matches_intent("note_to_self")

    def test_load_yml_extension(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "custom.yml").write_text("name: custom\ntriggers:\n  - intent: general\nprompt: do it\n")
        workflows = load_workflows(wf_dir)
        assert len(workflows) == 1
        assert workflows[0].name == "custom"


# ------------------------------------------------------------------
# Markdown to HTML converter
# ------------------------------------------------------------------


class TestMdToHtml:
    def test_basic_conversion(self):
        md = "# Hello\n\nSome text here."
        html = md_to_html(md, title="Test")
        assert "<h1>Hello</h1>" in html
        assert "<p>Some text here.</p>" in html
        assert "<title>Test</title>" in html

    def test_strips_frontmatter(self):
        md = "---\ntitle: foo\ndate: 2026-01-01\n---\n\n# Content"
        html = md_to_html(md)
        assert "title: foo" not in html
        assert "<h1>Content</h1>" in html

    def test_code_blocks(self):
        md = "```python\nprint('hello')\n```"
        html = md_to_html(md)
        assert "<pre><code>" in html
        assert "print(" in html

    def test_bold_and_italic(self):
        md = "This is **bold** and *italic* text."
        html = md_to_html(md)
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    def test_unordered_list(self):
        md = "- Item 1\n- Item 2\n- Item 3"
        html = md_to_html(md)
        assert "<ul>" in html
        assert "<li>Item 1</li>" in html
        assert "<li>Item 3</li>" in html

    def test_ordered_list(self):
        md = "1. First\n2. Second"
        html = md_to_html(md)
        assert "<ol>" in html
        assert "<li>First</li>" in html

    def test_blockquote(self):
        md = "> This is a quote"
        html = md_to_html(md)
        assert "<blockquote>" in html
        assert "This is a quote" in html

    def test_horizontal_rule(self):
        md = "Above\n\n---\n\nBelow"
        html = md_to_html(md)
        assert "<hr>" in html

    def test_table(self):
        md = "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        html = md_to_html(md)
        assert "<table>" in html
        assert "<th>Col1</th>" in html
        assert "<td>A</td>" in html

    def test_inline_code(self):
        md = "Use `foo()` here."
        html = md_to_html(md)
        assert "<code>foo()</code>" in html

    def test_checkbox_list(self):
        md = "- [ ] Todo\n- [x] Done"
        html = md_to_html(md)
        assert "&#9744;" in html  # unchecked
        assert "&#9745;" in html  # checked

    def test_e_reader_styles(self):
        html = md_to_html("# Test")
        assert "Georgia" in html  # serif font for e-ink
        assert "max-width" in html


# ------------------------------------------------------------------
# Multi-destination report writing
# ------------------------------------------------------------------


@pytest.fixture
def workflow_config(tmp_path):
    return Config(
        storage=StorageConfig(output_dir=tmp_path / "sotto-wf-test"),
        destinations=DestinationsConfig({
            "obsidian_vault": str(tmp_path / "vault"),
            "e-reader": str(tmp_path / "ereader"),
        }),
        pipelines={
            "standard": PipelineConfig(
                transcription="local", llm_backend="anthropic", model="claude-sonnet-4-6"
            ),
        },
        projects={
            "sotto": ProjectConfig(path=str(tmp_path / "projects" / "sotto"), aliases=[]),
        },
        orchestrator=OrchestratorConfig(
            max_concurrent=2,
            timeout_seconds=30,
            session_store_path=str(tmp_path / "orchestrator.db"),
            report_dir=str(tmp_path / "vault" / "reports"),
        ),
        workflows=[
            WorkflowConfig(
                name="plan",
                triggers=[{"intent": "plan_request"}],
                prompt="Plan: {{transcript}}",
                outputs=[
                    WorkflowOutput(destination="obsidian_vault", path="reports/", format="markdown"),
                    WorkflowOutput(destination="e-reader", path="sotto-reports/", format="html"),
                ],
            ),
        ],
    )


class TestMultiDestinationOutput:
    def test_writes_to_both_destinations(self, workflow_config, tmp_path):
        orch = Orchestrator(workflow_config)
        task = TaskStatus(
            task_id="A1B2",
            state="completed",
            prompt="Plan the auth refactor",
            project="sotto",
            intent="plan_request",
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )

        path = orch._write_report(task, "Here is the plan...", "sess-123")
        assert path is not None

        # Check markdown was written to vault
        vault_reports = list((tmp_path / "vault" / "reports").glob("*.md"))
        assert len(vault_reports) == 1
        md_content = vault_reports[0].read_text()
        assert "Here is the plan..." in md_content
        assert "task_id: A1B2" in md_content

        # Check HTML was written to e-reader
        ereader_reports = list((tmp_path / "ereader" / "sotto-reports").glob("*.html"))
        assert len(ereader_reports) == 1
        html_content = ereader_reports[0].read_text()
        assert "Here is the plan..." in html_content
        assert "<html" in html_content

    def test_falls_back_to_default_without_workflow(self, tmp_path):
        config = Config(
            storage=StorageConfig(output_dir=tmp_path / "sotto-fallback"),
            destinations=DestinationsConfig({"obsidian_vault": str(tmp_path / "vault")}),
            orchestrator=OrchestratorConfig(
                report_dir=str(tmp_path / "vault" / "reports"),
                session_store_path=str(tmp_path / "orchestrator.db"),
            ),
        )
        orch = Orchestrator(config)
        task = TaskStatus(
            task_id="C3D4",
            state="completed",
            prompt="Do something",
            intent=None,
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )

        path = orch._write_report(task, "Output text", "sess-456")
        assert path is not None
        assert path.suffix == ".md"
        assert "Output text" in path.read_text()

    def test_task_id_in_report_for_reply(self, workflow_config, tmp_path):
        orch = Orchestrator(workflow_config)
        task = TaskStatus(
            task_id="F9E8",
            state="completed",
            prompt="Plan something",
            project="sotto",
            intent="plan_request",
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )

        orch._write_report(task, "The plan output", "sess-abc")

        # Both formats should include the task ID for reply reference
        md_files = list((tmp_path / "vault" / "reports").glob("*.md"))
        assert any("F9E8" in f.read_text() for f in md_files)

        html_files = list((tmp_path / "ereader" / "sotto-reports").glob("*.html"))
        assert any("F9E8" in f.read_text() for f in html_files)

    def test_skips_unknown_destination(self, tmp_path):
        config = Config(
            storage=StorageConfig(output_dir=tmp_path / "sotto-skip"),
            destinations=DestinationsConfig({"obsidian_vault": str(tmp_path / "vault")}),
            orchestrator=OrchestratorConfig(
                report_dir=str(tmp_path / "vault" / "reports"),
                session_store_path=str(tmp_path / "orchestrator.db"),
            ),
            workflows=[
                WorkflowConfig(
                    name="broken",
                    triggers=[{"intent": "plan_request"}],
                    prompt="test",
                    outputs=[
                        WorkflowOutput(destination="nonexistent", path="x/", format="html"),
                        WorkflowOutput(destination="obsidian_vault", path="reports/", format="markdown"),
                    ],
                ),
            ],
        )
        orch = Orchestrator(config)
        task = TaskStatus(
            task_id="SKIP",
            state="completed",
            prompt="test",
            intent="plan_request",
            created_at="2026-03-12T10:00:00",
            updated_at="2026-03-12T10:00:00",
        )

        # Should succeed, skipping the unknown destination
        path = orch._write_report(task, "Output", "sess-x")
        assert path is not None
        assert path.suffix == ".md"
