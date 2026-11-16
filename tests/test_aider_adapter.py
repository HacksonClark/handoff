from __future__ import annotations

from pathlib import Path

from handoff.agents.aider import AiderExtractor, AiderInjector
from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso

HISTORY = """# aider chat started at 2026-07-17 09:00:00

#### Show me the failing query

The query misses the tenant predicate.

> Applied edit to query.go

#### /add helpers.py

#### Fix the join

Done.
"""


def test_aider_extract_sessions(tmp_path: Path):
    project = tmp_path / "repo"
    project.mkdir()
    hist = project / ".aider.chat.history.md"
    hist.write_text(HISTORY, encoding="utf-8")

    ex = AiderExtractor(tmp_path)
    sessions = ex.list_sessions(project)
    assert len(sessions) == 1
    t = ex.extract(sessions[0])
    assert t.metadata.source_agent == "aider"
    user_msgs = [m.content for m in t.transcript if m.author == "user"]
    assert "Show me the failing query" in user_msgs
    assert "Fix the join" in user_msgs
    # /add command skipped
    assert not any(m.content.startswith("/add") for m in t.transcript if m.author == "user")
    assert "query.go" in t.artifacts.files_modified


def test_aider_inject_appends(tmp_path: Path):
    project = tmp_path / "repo"
    project.mkdir()
    hist = project / ".aider.chat.history.md"
    hist.write_text(HISTORY, encoding="utf-8")

    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="s",
            source_agent="claude",
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
                content="Resume please",
            )
        ],
    )
    path = AiderInjector(tmp_path).inject(transcript)
    text = path.read_text(encoding="utf-8")
    assert text.count("# aider chat started at") == 2
    assert "[handoff]" in text
