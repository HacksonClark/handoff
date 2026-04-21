from __future__ import annotations

import json
from pathlib import Path

from handoff.agents.opencode import OpenCodeExtractor, OpenCodeInjector


def _make_opencode_session(
    home: Path, cwd: str, project_id: str, session_id: str, messages: list[dict]
) -> Path:
    storage = home / "storage"
    (storage / "project").mkdir(parents=True, exist_ok=True)
    (storage / "session" / project_id).mkdir(parents=True, exist_ok=True)
    (storage / "message" / session_id).mkdir(parents=True, exist_ok=True)

    (storage / "project" / f"{project_id}.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "worktree": cwd,
                "vcs": "git",
                "time": {"created": 1000, "updated": 2000},
            }
        )
    )
    session_path = storage / "session" / project_id / f"{session_id}.json"
    session_path.write_text(
        json.dumps(
            {
                "id": session_id,
                "slug": "test",
                "projectID": project_id,
                "directory": cwd,
                "title": "Test session",
                "time": {"created": 1500, "updated": 2500},
            }
        )
    )

    for i, m in enumerate(messages):
        msg_id = m["id"]
        (storage / "message" / session_id / f"{msg_id}.json").write_text(
            json.dumps(
                {
                    "id": msg_id,
                    "sessionID": session_id,
                    "role": m["role"],
                    "time": {"created": 1500 + i, "completed": 1500 + i},
                    "modelID": m.get("model"),
                }
            )
        )
        part_dir = storage / "part" / msg_id
        part_dir.mkdir(parents=True, exist_ok=True)
        for j, part in enumerate(m["parts"]):
            (part_dir / f"prt_{msg_id}_{j}.json").write_text(
                json.dumps(
                    {
                        "id": f"prt_{msg_id}_{j}",
                        "sessionID": session_id,
                        "messageID": msg_id,
                        **part,
                    }
                )
            )

    return session_path


def test_list_sessions_finds_by_cwd(tmp_path):
    home = tmp_path / "opencode"
    _make_opencode_session(
        home,
        cwd="/Users/me/a",
        project_id="proj_a",
        session_id="ses_1",
        messages=[{"id": "msg_1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
    )
    _make_opencode_session(
        home,
        cwd="/Users/me/b",
        project_id="proj_b",
        session_id="ses_2",
        messages=[{"id": "msg_2", "role": "user", "parts": [{"type": "text", "text": "bye"}]}],
    )

    ex = OpenCodeExtractor(home)
    refs = ex.list_sessions(Path("/Users/me/a"))
    assert len(refs) == 1
    assert refs[0].session_id == "ses_1"
    assert refs[0].cwd == "/Users/me/a"


def test_extract_reads_text_parts_in_order(tmp_path):
    home = tmp_path / "opencode"
    _make_opencode_session(
        home,
        cwd="/Users/me/a",
        project_id="proj_a",
        session_id="ses_1",
        messages=[
            {"id": "msg_u", "role": "user", "parts": [{"type": "text", "text": "Fix bug"}]},
            {
                "id": "msg_a",
                "role": "assistant",
                "model": "gpt-5.2",
                "parts": [
                    {"type": "text", "text": "Looking at it"},
                    {"type": "reasoning", "text": "internal thought"},
                    {"type": "patch", "files": ["/tmp/x.py"]},
                ],
            },
        ],
    )
    ex = OpenCodeExtractor(home)
    ref = ex.find_latest(Path("/Users/me/a"))
    assert ref is not None
    t = ex.extract(ref)

    types = [(m.author, m.type) for m in t.transcript]
    assert ("user", "message") in types
    assert ("agent", "message") in types
    assert ("agent", "reasoning") in types
    assert t.metadata.model == "gpt-5.2"
    assert "/tmp/x.py" in t.artifacts.files_modified


def test_inject_creates_full_storage_tree(tmp_path):
    from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso

    home = tmp_path / "opencode"
    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="src",
            source_agent="claude",
            source_session_path="/tmp/src.jsonl",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=1,
            cwd="/Users/me/a",
        ),
        transcript=[
            Message(id="1", timestamp=now_iso(), author="user", type="message", content="hi"),
        ],
    )

    inj = OpenCodeInjector(home)
    session_path = inj.inject(transcript)
    assert session_path.exists()
    data = json.loads(session_path.read_text())
    assert data["projectID"]
    assert data["directory"] == "/Users/me/a"

    # Part file was written
    storage = home / "storage"
    parts = list((storage / "part").rglob("*.json"))
    assert parts, "expected part file"
    part = json.loads(parts[0].read_text())
    assert "[handoff]" in part["text"]


def test_injected_files_are_mode_0600(tmp_path):
    from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso

    home = tmp_path / "opencode"
    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="src",
            source_agent="claude",
            source_session_path="/tmp/src.jsonl",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=1,
            cwd="/Users/me/a",
        ),
        transcript=[
            Message(id="1", timestamp=now_iso(), author="user", type="message", content="hi"),
        ],
    )
    inj = OpenCodeInjector(home)
    session_path = inj.inject(transcript)

    import stat

    mode = stat.S_IMODE(session_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
