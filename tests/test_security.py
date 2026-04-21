"""Security-focused tests: path escape, permissions, null bytes."""

from __future__ import annotations

import json
import stat

import pytest

from handoff.agents.claude import ClaudeInjector, encode_project_dir
from handoff.agents.codex import CodexInjector
from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso


def _transcript(cwd: str, source: str = "codex") -> CanonicalTranscript:
    return CanonicalTranscript(
        metadata=Metadata(
            session_id="src",
            source_agent=source,
            source_session_path="/tmp/src.jsonl",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=1,
            cwd=cwd,
        ),
        transcript=[
            Message(id="1", timestamp=now_iso(), author="user", type="message", content="hi"),
        ],
    )


def test_encode_project_dir_rejects_null_byte():
    with pytest.raises(ValueError):
        encode_project_dir("/foo\x00/bar")


def test_encode_project_dir_produces_no_separators():
    encoded = encode_project_dir("/Users/me/a")
    assert "/" not in encoded
    assert "\\" not in encoded
    assert encoded.startswith("-")


def test_claude_injected_file_is_0600(tmp_path):
    inj = ClaudeInjector(tmp_path)
    out = inj.inject(_transcript("/Users/me/a"))
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_codex_injected_file_is_0600(tmp_path):
    inj = CodexInjector(tmp_path)
    out = inj.inject(_transcript("/Users/me/a", source="claude"))
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_claude_inject_stays_within_home(tmp_path):
    """A malicious source with cwd='../etc' must not escape the target home."""
    inj = ClaudeInjector(tmp_path)
    # Path-traversal-looking cwd. encode_project_dir absolutises non-absolute
    # strings; to force a traversal attempt we give it something absolute
    # containing '..' which Path resolution normalises away.
    out = inj.inject(_transcript("/Users/me/../../etc"))
    # Resolve both; out must be a descendant of tmp_path
    assert str(out.resolve()).startswith(str(tmp_path.resolve()))


def test_redaction_is_applied_in_injected_content(tmp_path):
    from copy import deepcopy

    from handoff.redact import Redactor

    t = _transcript("/Users/me/a")
    t.transcript.append(
        Message(
            id="2",
            timestamp=now_iso(),
            author="user",
            type="message",
            content="My key is sk-abcdef1234567890abcdef",
        )
    )
    Redactor().redact_transcript(deepcopy(t))  # doesn't mutate original

    inj = ClaudeInjector(tmp_path)
    redacted = deepcopy(t)
    Redactor().redact_transcript(redacted)
    out = inj.inject(redacted)

    content = "".join(
        json.loads(line).get("message", {}).get("content", "")
        if json.loads(line).get("type") == "user"
        else ""
        for line in out.read_text().splitlines()
        if line.strip()
    )
    assert "sk-abcdef" not in content
