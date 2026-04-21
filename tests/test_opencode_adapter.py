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


def test_project_id_uses_git_first_commit_when_in_git_repo(tmp_path):
    """OpenCode keys projects by the hash of the first commit in the worktree."""
    import subprocess

    from handoff.agents.opencode import _project_id_for

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "first", "--no-gpg-sign"],
        cwd=repo,
        check=True,
    )

    expected = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert _project_id_for(str(repo)) == expected


def test_project_id_falls_back_to_sha1_for_non_git_dir(tmp_path):
    import hashlib

    from handoff.agents.opencode import _project_id_for

    plain = tmp_path / "plain"
    plain.mkdir()
    expected = hashlib.sha1(str(plain.resolve()).encode()).hexdigest()
    assert _project_id_for(str(plain)) == expected


def test_inject_writes_to_sqlite_when_db_exists(tmp_path):
    import sqlite3

    from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso

    home = tmp_path / "opencode"
    home.mkdir()

    # Create a minimal DB matching OpenCode's schema.
    db_path = home / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE project (
            id TEXT PRIMARY KEY, worktree TEXT, vcs TEXT, name TEXT,
            icon_url TEXT, icon_color TEXT,
            time_created INTEGER, time_updated INTEGER, time_initialized INTEGER,
            sandboxes TEXT, commands TEXT
        );
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
            directory TEXT, title TEXT, version TEXT, share_url TEXT,
            summary_additions INTEGER, summary_deletions INTEGER,
            summary_files INTEGER, summary_diffs TEXT, revert TEXT,
            permission TEXT, time_created INTEGER, time_updated INTEGER,
            time_compacting INTEGER, time_archived INTEGER, workspace_id TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    t = CanonicalTranscript(
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
    OpenCodeInjector(home).inject(t)

    conn = sqlite3.connect(db_path)
    sessions = list(conn.execute("SELECT id, project_id, title FROM session"))
    assert len(sessions) == 1
    assert sessions[0][2] == "Handoff from claude"
    msgs = list(conn.execute("SELECT id, session_id, data FROM message"))
    assert len(msgs) == 1
    parts = list(conn.execute("SELECT id, data FROM part"))
    assert len(parts) == 1
    import json as _json

    assert "[handoff]" in _json.loads(parts[0][1])["text"]
    conn.close()
