"""Turn a release's Jira tickets and PRs into a human-readable narrative.

Used by release_to_jira.py. Given the ticket/PR context it collects from Jira
and GitHub, this asks Claude for a release summary plus per-theme sections
saying what was delivered, why it mattered, and which epic/theme it supports.

Fail-open by design: build_narrative() returns None on any error (no API key,
rate limit, bad response) so the release notes still publish with the plain
ticket and PR lists.
"""

import json
import os
import sys

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

# Per-item caps so a novel-length PR description can't blow up the request.
MAX_DESC_CHARS = 2000
MAX_BODY_CHARS = 2000

SYSTEM = """\
You write release notes for MLPA, the ML Proxy API that Mozilla's AI Platform \
team runs to authenticate and proxy LLM traffic through LiteLLM with per-user \
budgets. Your readers are a mix of engineers on the team and stakeholders \
outside it who want to know what changed and why it was worth doing.

Ground every statement in the ticket and PR data you are given. Do not invent \
user impact, metrics, or motivations that are not supported by that data. When \
a change's significance genuinely isn't inferable from the input, describe what \
it does plainly rather than inflating it — "internal test coverage for budget \
enforcement" is a better answer than a speculative business benefit.

Group the work into themes. Prefer the Jira epic as the theme when tickets \
share one; otherwise group changes that serve the same purpose. Put \
user-visible or operationally significant work first, and dependency bumps, \
chores, and version bumps last (or fold them into one "Maintenance" theme).

Write in plain prose, past tense, no marketing voice, no bullet fragments. \
Refer to tickets by key (AIPLAT-123) when it helps the reader, and never use \
the word "leverage"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "2-3 sentences on what this release is about overall, for "
                "someone who reads only this paragraph."
            ),
        },
        "themes": {
            "type": "array",
            "description": "One entry per theme, most significant first.",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "Short theme name, e.g. 'Per-country metrics'.",
                    },
                    "delivered": {
                        "type": "string",
                        "description": "1-3 sentences: what actually shipped.",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": (
                            "1-2 sentences: who benefits and how, or what it "
                            "unblocks. Stay within what the input supports."
                        ),
                    },
                    "epic_key": {
                        "type": "string",
                        "description": (
                            "Jira key of the epic this supports, or empty "
                            "string if the tickets have no epic."
                        ),
                    },
                    "epic_name": {
                        "type": "string",
                        "description": "Epic summary, or empty string.",
                    },
                    "tickets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Jira keys covered by this theme.",
                    },
                },
                "required": [
                    "theme",
                    "delivered",
                    "why_it_matters",
                    "epic_key",
                    "epic_name",
                    "tickets",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "themes"],
    "additionalProperties": False,
}


def adf_to_text(node):
    """Flatten a Jira Atlassian Document Format body to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    out = node.get("text", "")
    out += adf_to_text(node.get("content"))
    # Block-level nodes get a newline so paragraphs and list items stay apart.
    if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock"):
        out += "\n"
    return out


def _clip(s, limit):
    s = (s or "").strip()
    return s if len(s) <= limit else s[:limit] + " …[truncated]"


def build_context(tag, tickets, prs):
    """Render the ticket/PR facts as the text block Claude reasons over.

    tickets: list of dicts with key, summary, description, issue_type, labels,
             epic_key, epic_name.
    prs:     list of dicts with number, title, body, keys.
    """
    lines = [f"Release tag: {tag}", "", "=== Jira tickets ==="]
    if not tickets:
        lines.append("(none linked)")
    for t in tickets:
        lines.append(f"\n[{t['key']}] {t['summary']}")
        lines.append(f"  Type: {t.get('issue_type') or 'unknown'}")
        if t.get("epic_key"):
            lines.append(f"  Epic: {t['epic_key']} — {t.get('epic_name') or ''}")
        if t.get("labels"):
            lines.append(f"  Labels: {', '.join(t['labels'])}")
        desc = _clip(t.get("description"), MAX_DESC_CHARS)
        lines.append(f"  Description: {desc or '(empty)'}")

    lines += ["", "=== Pull requests ==="]
    if not prs:
        lines.append("(none referenced)")
    for p in prs:
        keys = ", ".join(p.get("keys") or []) or "no ticket"
        lines.append(f"\n[PR #{p['number']}] {p['title']}  ({keys})")
        lines.append(f"  Body: {_clip(p.get('body'), MAX_BODY_CHARS) or '(empty)'}")
    return "\n".join(lines)


def build_narrative(tag, tickets, prs):
    """Ask Claude for the release narrative. Returns a dict or None on failure."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        print("  ! ANTHROPIC_API_KEY not set, skipping narrative", file=sys.stderr)
        return None
    if not tickets and not prs:
        print("  ! no tickets or PRs to summarize, skipping narrative")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic()
        context = build_context(tag, tickets, prs)
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {
                    "type": "json_schema",
                    "schema": SCHEMA,
                },
            },
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Write the release narrative for MLPA {tag} from the "
                        f"following release data.\n\n{context}"
                    ),
                }
            ],
        )
        if response.stop_reason == "refusal":
            print(f"  ! narrative refused: {response.stop_details}", file=sys.stderr)
            return None
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        u = response.usage
        print(
            f"  narrative: {len(data['themes'])} themes "
            f"({u.input_tokens} in / {u.output_tokens} out tokens)"
        )
        return data
    except Exception as e:  # fail open — notes publish without the narrative
        # APIConnectionError stringifies to a bare "Connection error." — the
        # actionable detail (bad proxy, stale certs, a base_url override
        # pointing somewhere dead) is only in the wrapped cause.
        detail, cause = f"{type(e).__name__}: {e}", e.__cause__
        while cause is not None:
            detail += f" <- {type(cause).__name__}: {cause}"
            cause = cause.__cause__
        base = os.environ.get("ANTHROPIC_BASE_URL")
        if base:
            detail += f" [ANTHROPIC_BASE_URL={base}]"
        print(f"  ! narrative generation failed ({detail})", file=sys.stderr)
        return None


def render_html(narrative, site, esc):
    """Render the narrative as Confluence storage-format HTML."""
    if not narrative:
        return ""

    def issue_link(key):
        return f'<a href="{esc(site)}/browse/{esc(key)}">{esc(key)}</a>'

    parts = [f"<h3>Summary</h3><p>{esc(narrative['summary'])}</p>"]
    if narrative["themes"]:
        parts.append("<h3>What shipped</h3>")
    for t in narrative["themes"]:
        parts.append(f"<h4>{esc(t['theme'])}</h4>")
        parts.append(f"<p>{esc(t['delivered'])}</p>")
        parts.append(
            f"<p><strong>Why it matters:</strong> {esc(t['why_it_matters'])}</p>"
        )
        meta = []
        if t.get("epic_key"):
            name = f" — {esc(t['epic_name'])}" if t.get("epic_name") else ""
            meta.append(f"<strong>Epic:</strong> {issue_link(t['epic_key'])}{name}")
        if t.get("tickets"):
            links = ", ".join(issue_link(k) for k in t["tickets"])
            meta.append(f"<strong>Tickets:</strong> {links}")
        if meta:
            parts.append(f"<p>{' · '.join(meta)}</p>")
    parts.append(
        "<p><em>Summary and themes written by Claude from the linked Jira "
        "tickets and pull requests listed below.</em></p>"
    )
    return "".join(parts)
