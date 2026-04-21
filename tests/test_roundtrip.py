"""Roundtrip tests: extract → inject → extract and verify content survives."""

from __future__ import annotations

from pathlib import Path

from handoff.agents.claude import ClaudeExtractor, ClaudeInjector
from handoff.agents.codex import CodexExtractor, CodexInjector


def _make_codex_session(factory, cwd: str) -> Path:
    return factory(
        cwd=cwd,
        messages=[
            {
                "timestamp": "2026-04-01T12:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Add auth"}],
                },
            },
            {
                "timestamp": "2026-04-01T12:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "I will add JWT auth."}],
                },
            },
        ],
    )


def test_codex_to_claude_preserves_user_text(codex_home, codex_session_factory, claude_home):
    _make_codex_session(codex_session_factory, "/Users/me/a")

    cex = CodexExtractor(codex_home)
    t = cex.extract(cex.find_latest(Path("/Users/me/a")))

    inj = ClaudeInjector(claude_home)
    out = inj.inject(t)

    cex2 = ClaudeExtractor(claude_home)
    refs = cex2.list_sessions(Path("/Users/me/a"))
    assert any(r.path == out for r in refs)

    t2 = cex2.extract(next(r for r in refs if r.path == out))
    combined = "\n".join(m.content for m in t2.transcript)
    assert "Add auth" in combined
    assert "JWT auth" in combined


def test_claude_to_codex_preserves_user_text(claude_home, claude_session_factory, codex_home):
    claude_session_factory(
        "/Users/me/a",
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "Add auth"},
                "uuid": "u1",
                "timestamp": "2026-04-01T12:00:00Z",
                "sessionId": "s1",
                "cwd": "/Users/me/a",
            },
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-7",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "I will add JWT auth."}],
                },
                "uuid": "u2",
                "timestamp": "2026-04-01T12:00:01Z",
                "sessionId": "s1",
                "cwd": "/Users/me/a",
            },
        ],
    )

    ex = ClaudeExtractor(claude_home)
    t = ex.extract(ex.find_latest(Path("/Users/me/a")))

    inj = CodexInjector(codex_home)
    out = inj.inject(t)

    cx = CodexExtractor(codex_home)
    refs = cx.list_sessions(Path("/Users/me/a"))
    assert any(r.path == out for r in refs)
    t2 = cx.extract(next(r for r in refs if r.path == out))
    combined = "\n".join(m.content for m in t2.transcript)
    assert "Add auth" in combined
    assert "JWT auth" in combined
