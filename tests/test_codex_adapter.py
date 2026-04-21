from __future__ import annotations

import json
from datetime import datetime, timezone

from handoff.agents.codex import CodexExtractor, CodexInjector


def _user_msg(text: str) -> dict:
    return {
        "timestamp": "2026-04-01T12:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _assistant_msg(text: str) -> dict:
    return {
        "timestamp": "2026-04-01T12:00:01Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _function_call(call_id: str, name: str, args: str) -> dict:
    return {
        "timestamp": "2026-04-01T12:00:02Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": name,
            "arguments": args,
            "call_id": call_id,
        },
    }


def _function_call_output(call_id: str, output: str) -> dict:
    return {
        "timestamp": "2026-04-01T12:00:03Z",
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }


def test_list_sessions_filters_by_cwd(codex_home, codex_session_factory):
    codex_session_factory(cwd="/Users/me/a", session_id="01")
    codex_session_factory(cwd="/Users/me/b", session_id="02")

    ex = CodexExtractor(codex_home)
    from pathlib import Path

    sessions = ex.list_sessions(Path("/Users/me/a"))
    assert len(sessions) == 1
    assert sessions[0].session_id == "01"
    assert sessions[0].cwd == "/Users/me/a"


def test_list_sessions_returns_newest_first(codex_home, codex_session_factory):
    codex_session_factory(
        cwd="/Users/me/a", session_id="old", when=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    codex_session_factory(
        cwd="/Users/me/a", session_id="new", when=datetime(2026, 4, 1, tzinfo=timezone.utc)
    )

    ex = CodexExtractor(codex_home)
    ids = [s.session_id for s in ex.list_sessions()]
    assert ids[0] == "new"


def test_extract_parses_messages_and_tool_calls(codex_home, codex_session_factory):
    path = codex_session_factory(
        cwd="/Users/me/a",
        messages=[
            _user_msg("Fix the build"),
            _assistant_msg("Looking at it now"),
            _function_call("call_1", "shell_command", '{"command": "ls"}'),
            _function_call_output("call_1", "file1\nfile2"),
            _assistant_msg("Found the issue"),
        ],
    )
    from pathlib import Path

    ex = CodexExtractor(codex_home)
    ref = ex.find_latest(Path("/Users/me/a"))
    assert ref is not None
    t = ex.extract(ref)

    types = [(m.author, m.type) for m in t.transcript]
    assert ("user", "message") in types
    assert ("agent", "message") in types
    assert ("agent", "tool_call") in types
    assert ("system", "tool_result") in types
    assert t.metadata.model == "gpt-5.4"
    assert t.metadata.git_branch == "main"
    assert t.metadata.cwd == "/Users/me/a"
    assert t.metadata.source_session_path == str(path)


def test_inject_produces_valid_jsonl_with_session_meta(codex_home, codex_session_factory):
    codex_session_factory(
        cwd="/Users/me/a",
        messages=[_user_msg("hello"), _assistant_msg("world")],
    )
    from pathlib import Path

    ex = CodexExtractor(codex_home)
    ref = ex.find_latest(Path("/Users/me/a"))
    t = ex.extract(ref)
    t.metadata.source_agent = "claude"  # simulate cross-agent

    inj = CodexInjector(codex_home)
    out = inj.inject(t)
    assert out.exists()

    records = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert records[0]["type"] == "session_meta"
    assert records[0]["payload"]["cwd"] == "/Users/me/a"
    # At least one user message should have been replayed
    user_msgs = [
        r
        for r in records
        if r.get("type") == "response_item"
        and r["payload"].get("type") == "message"
        and r["payload"].get("role") == "user"
    ]
    assert user_msgs, "expected at least one replayed user message"

    # File lives under the expected YYYY/MM/DD hierarchy
    assert out.parent.parent.parent.parent == codex_home / "sessions"
