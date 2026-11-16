from __future__ import annotations

import json
from pathlib import Path

from handoff.agents.gemini import GeminiExtractor, GeminiInjector
from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso


def test_gemini_list_and_extract(tmp_path: Path):
    home = tmp_path / ".gemini"
    project = tmp_path / "app"
    project.mkdir()
    chats = home / "tmp" / "abc123" / "chats"
    chats.mkdir(parents=True)
    doc = {
        "sessionId": "gem-sess-1",
        "cwd": str(project),
        "startTime": "2026-03-01T12:00:00Z",
        "messages": [
            {"role": "user", "content": "List files", "timestamp": "2026-03-01T12:00:00Z"},
            {
                "role": "model",
                "timestamp": "2026-03-01T12:00:01Z",
                "parts": [
                    {"text": "Listing."},
                    {"functionCall": {"name": "list_directory", "args": {"path": "."}}},
                ],
            },
        ],
    }
    path = chats / "gem-sess-1.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    ex = GeminiExtractor(home)
    sessions = ex.list_sessions(project)
    assert len(sessions) == 1
    assert sessions[0].session_id == "gem-sess-1"

    t = ex.extract(sessions[0])
    assert t.metadata.source_agent == "gemini"
    assert any(m.content == "List files" for m in t.transcript)
    assert any(
        m.type == "tool_call" and m.metadata.get("tool_name") == "list_directory"
        for m in t.transcript
    )


def test_gemini_inject(tmp_path: Path):
    home = tmp_path / ".gemini"
    project = tmp_path / "app"
    project.mkdir()
    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="s",
            source_agent="cursor",
            source_session_path="",
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
                content="Continue",
            )
        ],
    )
    path = GeminiInjector(home).inject(transcript)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cwd"] == str(project)
    assert "handoff" in data["messages"][0]["content"].lower()
