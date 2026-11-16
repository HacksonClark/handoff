from __future__ import annotations

import json

from handoff.atif import from_atif, to_atif, validate_atif
from handoff.canonical import (
    Artifacts,
    CanonicalTranscript,
    Message,
    Metadata,
    TaskItem,
    TaskState,
    now_iso,
)


def _sample() -> CanonicalTranscript:
    return CanonicalTranscript(
        metadata=Metadata(
            session_id="sess-1",
            source_agent="codex",
            source_session_path="/tmp/rollout.jsonl",
            created_at="2026-01-01T00:00:00Z",
            last_activity="2026-01-01T00:05:00Z",
            message_count=4,
            cwd="/tmp/app",
            git_branch="main",
            model="gpt-5.4",
        ),
        transcript=[
            Message(
                id="1",
                timestamp="2026-01-01T00:00:00Z",
                author="user",
                type="message",
                content="Create hello.txt",
            ),
            Message(
                id="2",
                timestamp="2026-01-01T00:00:01Z",
                author="agent",
                type="message",
                content="I'll write the file.",
            ),
            Message(
                id="3",
                timestamp="2026-01-01T00:00:02Z",
                author="agent",
                type="tool_call",
                content='{"path": "hello.txt", "content": "hi"}',
                metadata={
                    "tool_name": "write_file",
                    "tool_call_id": "call-1",
                    "arguments": {"path": "hello.txt", "content": "hi"},
                },
            ),
            Message(
                id="4",
                timestamp="2026-01-01T00:00:03Z",
                author="agent",
                type="tool_result",
                content="File created",
                metadata={"tool_call_id": "call-1"},
            ),
        ],
        artifacts=Artifacts(
            files_modified=["hello.txt"],
            task_state=TaskState(
                items=[TaskItem(content="Write hello.txt", status="completed")],
                source="plan",
            ),
        ),
    )


def test_to_atif_groups_tool_calls_into_agent_step():
    traj = to_atif(_sample())
    assert traj["schema_version"].startswith("ATIF-")
    assert traj["session_id"] == "sess-1"
    assert traj["agent"]["name"] == "codex"
    assert traj["agent"]["model_name"] == "gpt-5.4"
    assert len(traj["steps"]) == 2
    assert traj["steps"][0]["source"] == "user"
    assert traj["steps"][1]["source"] == "agent"
    assert traj["steps"][1]["tool_calls"][0]["function_name"] == "write_file"
    assert traj["steps"][1]["observation"]["results"][0]["source_call_id"] == "call-1"
    assert traj["extra"]["handoff"]["cwd"] == "/tmp/app"
    assert validate_atif(traj) == []


def test_roundtrip_preserves_content():
    original = _sample()
    restored = from_atif(to_atif(original))
    assert restored.metadata.session_id == "sess-1"
    assert restored.metadata.cwd == "/tmp/app"
    assert restored.metadata.source_agent == "codex"
    texts = [(m.author, m.type, m.content[:20]) for m in restored.transcript]
    assert ("user", "message", "Create hello.txt") in texts
    assert any(
        m.type == "tool_call" and m.metadata.get("tool_name") == "write_file"
        for m in restored.transcript
    )
    assert any(m.type == "tool_result" and "File created" in m.content for m in restored.transcript)
    assert restored.artifacts.files_modified == ["hello.txt"]
    assert restored.artifacts.task_state is not None
    assert restored.artifacts.task_state.items[0].content == "Write hello.txt"


def test_validate_atif_catches_bad_step_ids():
    traj = to_atif(_sample())
    traj["steps"][1]["step_id"] = 99
    errors = validate_atif(traj)
    assert any("step_id" in e for e in errors)


def test_from_atif_harbor_minimal():
    data = {
        "schema_version": "ATIF-v1.8",
        "session_id": "session-123",
        "agent": {"name": "my-agent", "version": "1.0.0", "model_name": "gpt-5"},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Create hello.txt."},
            {
                "step_id": 2,
                "source": "agent",
                "message": "I'll create it.",
                "tool_calls": [
                    {
                        "tool_call_id": "call-1",
                        "function_name": "write_file",
                        "arguments": {"path": "hello.txt", "content": "Hello"},
                    }
                ],
                "observation": {
                    "results": [{"source_call_id": "call-1", "content": "File created"}]
                },
            },
        ],
    }
    t = from_atif(data, default_cwd="/work")
    assert t.metadata.source_agent == "my-agent"
    assert t.metadata.model == "gpt-5"
    assert t.transcript[0].content == "Create hello.txt."
    assert any(m.type == "tool_call" for m in t.transcript)


def test_empty_transcript_still_emits_valid_atif():
    t = CanonicalTranscript(
        metadata=Metadata(
            session_id="empty",
            source_agent="claude",
            source_session_path="",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=0,
            cwd="/tmp",
        )
    )
    traj = to_atif(t)
    assert validate_atif(traj) == []
    assert len(traj["steps"]) == 1
    # Ensure JSON-serializable
    json.dumps(traj)
