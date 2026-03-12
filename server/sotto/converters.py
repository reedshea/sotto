"""Format converters for Sotto output — markdown to HTML, etc."""

from __future__ import annotations

import html
import re


def md_to_html(markdown: str, title: str = "Sotto Report") -> str:
    """Convert markdown to a self-contained HTML document suitable for e-readers.

    Uses a lightweight regex-based converter (no external dependencies).
    Produces clean, readable HTML optimized for e-ink displays.
    """
    body = _convert_body(markdown)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{
  font-family: Georgia, serif;
  line-height: 1.6;
  max-width: 42em;
  margin: 0 auto;
  padding: 1em;
  color: #111;
}}
h1 {{ font-size: 1.4em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }}
h2 {{ font-size: 1.2em; margin-top: 1.5em; }}
h3 {{ font-size: 1.05em; margin-top: 1.2em; }}
pre {{ background: #f5f5f5; padding: 0.8em; overflow-x: auto; font-size: 0.85em; }}
code {{ background: #f0f0f0; padding: 0.15em 0.3em; font-size: 0.9em; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 3px solid #ccc; margin-left: 0; padding-left: 1em; color: #555; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: left; }}
th {{ background: #f5f5f5; }}
hr {{ border: none; border-top: 1px solid #ccc; margin: 2em 0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _convert_body(md: str) -> str:
    """Convert markdown body to HTML."""
    # Strip YAML frontmatter
    md = re.sub(r"^---\n.*?\n---\n?", "", md, count=1, flags=re.DOTALL)

    lines = md.split("\n")
    out: list[str] = []
    i = 0
    in_list = False
    list_type = ""  # "ul" or "ol"

    while i < len(lines):
        line = lines[i]

        # Fenced code blocks
        if line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(html.escape(lines[i]))
                i += 1
            i += 1  # skip closing ```
            if in_list:
                out.append(f"</{list_type}>")
                in_list = False
            code_block = "\n".join(code_lines)
            out.append(f"<pre><code>{code_block}</code></pre>")
            continue

        # Tables
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1]):
            if in_list:
                out.append(f"</{list_type}>")
                in_list = False
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            out.append(_convert_table(table_lines))
            continue

        # Headings
        heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading_match:
            if in_list:
                out.append(f"</{list_type}>")
                in_list = False
            level = len(heading_match.group(1))
            text = _inline(heading_match.group(2))
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^---+\s*$", line):
            if in_list:
                out.append(f"</{list_type}>")
                in_list = False
            out.append("<hr>")
            i += 1
            continue

        # Blockquote
        if line.startswith("> "):
            if in_list:
                out.append(f"</{list_type}>")
                in_list = False
            quote_lines = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(_inline(lines[i][2:]))
                i += 1
            br_join = "<br>\n".join(quote_lines)
            out.append(f"<blockquote><p>{br_join}</p></blockquote>")
            continue

        # Unordered list
        ul_match = re.match(r"^(\s*)[-*]\s+(\[[ x]\]\s+)?(.*)", line)
        if ul_match:
            if not in_list or list_type != "ul":
                if in_list:
                    out.append(f"</{list_type}>")
                out.append("<ul>")
                in_list = True
                list_type = "ul"
            checkbox = ul_match.group(2)
            text = ul_match.group(3)
            prefix = ""
            if checkbox:
                checked = "x" in checkbox
                prefix = "&#9745; " if checked else "&#9744; "
            out.append(f"<li>{prefix}{_inline(text)}</li>")
            i += 1
            continue

        # Ordered list
        ol_match = re.match(r"^\s*\d+\.\s+(.*)", line)
        if ol_match:
            if not in_list or list_type != "ol":
                if in_list:
                    out.append(f"</{list_type}>")
                out.append("<ol>")
                in_list = True
                list_type = "ol"
            out.append(f"<li>{_inline(ol_match.group(1))}</li>")
            i += 1
            continue

        # Close any open list on non-list line
        if in_list and line.strip() == "":
            out.append(f"</{list_type}>")
            in_list = False

        # Blank line
        if line.strip() == "":
            i += 1
            continue

        # Paragraph
        if in_list:
            out.append(f"</{list_type}>")
            in_list = False
        para_lines = []
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines[i]):
            para_lines.append(_inline(lines[i]))
            i += 1
        if para_lines:
            para_join = "<br>\n".join(para_lines)
            out.append(f"<p>{para_join}</p>")
        continue

    if in_list:
        out.append(f"</{list_type}>")

    return "\n".join(out)


def _is_block_start(line: str) -> bool:
    """Check if a line starts a new block element."""
    if line.startswith("#"):
        return True
    if line.startswith("```"):
        return True
    if line.startswith("> "):
        return True
    if re.match(r"^---+\s*$", line):
        return True
    if re.match(r"^[-*]\s+", line):
        return True
    if re.match(r"^\d+\.\s+", line):
        return True
    return False


def _inline(text: str) -> str:
    """Convert inline markdown to HTML."""
    # Bold + italic
    text = re.sub(r"\*\*\*(.*?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.*?)\*", r"<em>\1</em>", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _convert_table(lines: list[str]) -> str:
    """Convert markdown table lines to an HTML table."""
    if len(lines) < 2:
        return ""

    def parse_row(line: str) -> list[str]:
        cells = line.strip().strip("|").split("|")
        return [c.strip() for c in cells]

    headers = parse_row(lines[0])
    # lines[1] is the separator row, skip it
    rows = [parse_row(line) for line in lines[2:]]

    parts = ["<table>", "<thead><tr>"]
    for h in headers:
        parts.append(f"<th>{_inline(h)}</th>")
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{_inline(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)
