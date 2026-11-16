from __future__ import annotations

import json
from pathlib import Path

from handoff.agents.cursor import CursorExtractor, CursorInjector, encode_project_slug
from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso


def _write_transcript(home: Path, cwd: Path, session_id: str, records: list[dict]) -> Path:
    slug = encode_project_slug(cwd)
    path = home / "projects" / slug / "agent-transcripts" / session_id / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_encode_project_slug():
    assert encode_project_slug("/Users/me/app") == "Users-me-app"
    assert encode_project_slug("/workspace") == "workspace"


def test_list_and_extract_cursor_transcript(tmp_path: Path):
    home = tmp_path / ".cursor"
    project = tmp_path / "my-app"
    project.mkdir()
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _write_transcript(
        home,
        project,
        sid,
        [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<timestamp>Sunday, Jul 26, 2026, 1:06 PM (UTC)</timestamp>\n"
                                "<user_query>\nFix the auth bug\n</user_query>"
                            ),
                        }
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Looking at the auth module."},
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "id": "call-1",
                            "input": {"path": "auth.ts"},
                        },
                    ]
                },
            },
        ],
    )

    ex = CursorExtractor(home)
    sessions = ex.list_sessions(project)
    assert len(sessions) == 1
    assert sessions[0].session_id == sid
    assert sessions[0].title and "Fix the auth bug" in sessions[0].title

    t = ex.extract(sessions[0])
    assert t.metadata.source_agent == "cursor"
    assert any("Fix the auth bug" in m.content for m in t.transcript if m.author == "user")
    assert any(
        m.type == "tool_call" and m.metadata.get("tool_name") == "Read" for m in t.transcript
    )
    assert "auth.ts" in t.artifacts.files_modified


def test_inject_writes_catchup_transcript(tmp_path: Path):
    home = tmp_path / ".cursor"
    project = tmp_path / "app"
    project.mkdir()
    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="src",
            source_agent="codex",
            source_session_path="/tmp/x",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=1,
            cwd=str(project),
        ),
        transcript=[
            Message(
                id="1",
                timestamp=now_iso(),
                author="user",
                type="message",
                content="Keep going",
            )
        ],
    )
    path = CursorInjector(home).inject(transcript)
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["role"] == "user"
    text = rec["message"]["content"][0]["text"]
    assert "handoff" in text.lower()
    assert "Keep going" in text or "codex" in text.lower()
