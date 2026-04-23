"""Render a canonical transcript as human-readable markdown.

This is also used as the payload for cross-agent injection: the receiving
agent reads a single "catch-up" message containing this markdown.
"""

from __future__ import annotations

from handoff.canonical import CanonicalTranscript, Message


def to_markdown(t: CanonicalTranscript, *, include_header: bool = True) -> str:
    out: list[str] = []
    m = t.metadata
    if include_header:
        out.append(f"# Handoff context from {m.source_agent}\n")
        out.append(f"- Session: `{m.session_id}`")
        out.append(f"- Project: `{m.cwd}`")
        out.append(f"- Started: {m.created_at}")
        out.append(f"- Last activity: {m.last_activity}")
        out.append(f"- Messages: {m.message_count}")
        if m.model:
            out.append(f"- Model: {m.model}")
        if m.git_branch:
            out.append(f"- Git branch: {m.git_branch}")
        if t.artifacts.files_modified:
            out.append("- Files touched:")
            for f in t.artifacts.files_modified:
                out.append(f"  - `{f}`")
        if t.artifacts.task_state and t.artifacts.task_state.items:
            out.append("- Current task state:")
            if t.artifacts.task_state.source:
                out.append(f"  - Source: `{t.artifacts.task_state.source}`")
            if t.artifacts.task_state.explanation:
                out.append(f"  - Note: {t.artifacts.task_state.explanation}")
            for item in t.artifacts.task_state.items:
                out.append(f"  - [{item.status}] {item.content}")
        out.append("")
        out.append("---")
        out.append("")

    for msg in t.transcript:
        out.append(_render_message(msg))
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _render_message(msg: Message) -> str:
    label = _label_for(msg)
    if msg.type == "message":
        return f"## {label}\n\n{msg.content}"
    if msg.type == "reasoning":
        return f"## {label} (reasoning)\n\n> {_indent_quote(msg.content)}"
    if msg.type == "tool_call":
        tool = msg.metadata.get("tool_name", "tool")
        return f"## {label} → `{tool}`\n\n```\n{msg.content}\n```"
    if msg.type == "tool_result":
        return f"## tool result\n\n```\n{_truncate(msg.content, 4000)}\n```"
    return f"## {label}\n\n{msg.content}"


def _label_for(msg: Message) -> str:
    labels = {
        "user": "User",
        "agent": "Assistant",
        "developer": "Developer",
        "system": "System",
    }
    return labels.get(msg.author) or msg.author


def _indent_quote(text: str) -> str:
    return "\n> ".join(text.splitlines())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"
