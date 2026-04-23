from __future__ import annotations

import json
import uuid

from handoff.agents.claude import ClaudeExtractor, ClaudeInjector, encode_project_dir


def _user(text: str, uid: str | None = None) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "uuid": uid or str(uuid.uuid4()),
        "timestamp": "2026-04-01T12:00:00Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
        "gitBranch": "main",
    }


def _assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": "2026-04-01T12:00:01Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
    }


def _assistant_tool_use(name: str, input_obj: dict, call_id: str = "toolu_1") -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": input_obj}],
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": "2026-04-01T12:00:02Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
    }


def _assistant_todowrite(items: list[tuple[str, str]]) -> dict:
    return _assistant_tool_use(
        "TodoWrite",
        {
            "todos": [
                {"content": content, "status": status}
                for content, status in items
            ]
        },
        call_id="toolu_todo",
    )


def _tool_result(call_id: str, content: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": content}],
        },
        "uuid": str(uuid.uuid4()),
        "timestamp": "2026-04-01T12:00:03Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
    }


def _away_summary(text: str) -> dict:
    return {
        "type": "system",
        "subtype": "away_summary",
        "content": text,
        "timestamp": "2026-04-01T12:00:04Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
    }


def _task_reminder(items: list[tuple[str, str]]) -> dict:
    return {
        "type": "attachment",
        "attachment": {
            "type": "task_reminder",
            "itemCount": len(items),
            "content": [
                {"subject": content, "status": status}
                for content, status in items
            ],
        },
        "timestamp": "2026-04-01T12:00:05Z",
        "sessionId": "00000000-1111-2222-3333-444455556666",
        "cwd": "/Users/me/a",
    }


def test_encode_project_dir_translates_slashes():
    assert encode_project_dir("/Users/me/a") == "-Users-me-a"
    assert encode_project_dir("/foo/bar/baz") == "-foo-bar-baz"


def test_list_sessions_for_cwd(claude_home, claude_session_factory):
    claude_session_factory("/Users/me/a", [_user("hello")])
    claude_session_factory("/Users/me/b", [_user("world")])

    ex = ClaudeExtractor(claude_home)
    from pathlib import Path

    refs = ex.list_sessions(Path("/Users/me/a"))
    assert len(refs) == 1
    assert refs[0].cwd == "/Users/me/a"


def test_extract_handles_mixed_content(claude_home, claude_session_factory):
    path = claude_session_factory(
        "/Users/me/a",
        [
            _user("Fix the bug"),
            _assistant_text("Looking now"),
            _assistant_tool_use("Bash", {"command": "ls"}, call_id="toolu_1"),
            _tool_result("toolu_1", "file1\nfile2"),
            _assistant_text("Found it"),
        ],
    )
    from pathlib import Path

    ex = ClaudeExtractor(claude_home)
    ref = ex.find_latest(Path("/Users/me/a"))
    t = ex.extract(ref)

    types = [(m.author, m.type) for m in t.transcript]
    assert ("user", "message") in types
    assert ("agent", "message") in types
    assert ("agent", "tool_call") in types
    assert ("system", "tool_result") in types
    assert t.metadata.source_session_path == str(path)
    assert t.metadata.git_branch == "main"


def test_extract_captures_todo_state(claude_home, claude_session_factory):
    claude_session_factory(
        "/Users/me/a",
        [
            _assistant_todowrite(
                [("Inspect bug", "completed"), ("Add regression test", "in_progress")]
            )
        ],
    )

    ex = ClaudeExtractor(claude_home)
    from pathlib import Path

    t = ex.extract(ex.find_latest(Path("/Users/me/a")))
    assert t.artifacts.task_state is not None
    assert t.artifacts.task_state.source == "TodoWrite"
    assert [(i.content, i.status) for i in t.artifacts.task_state.items] == [
        ("Inspect bug", "completed"),
        ("Add regression test", "in_progress"),
    ]


def test_extract_captures_task_reminder_and_away_summary(claude_home, claude_session_factory):
    claude_session_factory(
        "/Users/me/a",
        [
            _task_reminder(
                [("Add RuntimeClassName field", "in_progress"), ("Patch CRD schema", "pending")]
            ),
            _away_summary("Goal: resume the interrupted Anchor isolation upgrade."),
        ],
    )

    ex = ClaudeExtractor(claude_home)
    from pathlib import Path

    t = ex.extract(ex.find_latest(Path("/Users/me/a")))
    assert t.artifacts.task_state is not None
    assert t.artifacts.task_state.source == "task_reminder"
    assert [(i.content, i.status) for i in t.artifacts.task_state.items] == [
        ("Add RuntimeClassName field", "in_progress"),
        ("Patch CRD schema", "pending"),
    ]
    assert "resume the interrupted Anchor isolation upgrade" in (
        t.artifacts.task_state.explanation or ""
    )


def test_inject_writes_well_formed_jsonl(claude_home, claude_session_factory):
    claude_session_factory(
        "/Users/me/a",
        [_user("Please help"), _assistant_text("On it")],
    )
    from pathlib import Path

    ex = ClaudeExtractor(claude_home)
    t = ex.extract(ex.find_latest(Path("/Users/me/a")))
    t.metadata.source_agent = "codex"

    inj = ClaudeInjector(claude_home)
    out = inj.inject(t)
    assert out.exists()
    assert out.parent.name == "-Users-me-a"

    records = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert records[0]["type"] == "permission-mode"
    user_recs = [r for r in records if r.get("type") == "user"]
    assert user_recs, "expected at least one user record"
    assert "[handoff]" in user_recs[0]["message"]["content"]
    assert user_recs[0]["cwd"] == "/Users/me/a"
    assert user_recs[0]["sessionId"] == out.stem
