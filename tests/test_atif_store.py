from __future__ import annotations

import json
from pathlib import Path

from handoff.agents.atif_store import AtifExtractor, AtifInjector
from handoff.atif import validate_atif
from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso


def test_atif_inject_and_extract_roundtrip(tmp_path: Path):
    home = tmp_path / "trajectories"
    project = tmp_path / "app"
    project.mkdir()
    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="orig-sess",
            source_agent="cursor",
            source_session_path="/tmp/x.jsonl",
            created_at="2026-01-01T00:00:00Z",
            last_activity="2026-01-01T00:01:00Z",
            message_count=2,
            cwd=str(project),
            model="composer",
        ),
        transcript=[
            Message(
                id="1",
                timestamp="2026-01-01T00:00:00Z",
                author="user",
                type="message",
                content="Ship the feature",
            ),
            Message(
                id="2",
                timestamp="2026-01-01T00:01:00Z",
                author="agent",
                type="message",
                content="Done.",
            ),
        ],
    )
    path = AtifInjector(home).inject(transcript)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert validate_atif(data) == []
    assert data["agent"]["name"] == "handoff"
    assert data["extra"]["handoff"]["source_agent"] == "cursor"

    ex = AtifExtractor(home)
    sessions = ex.list_sessions(project)
    assert len(sessions) == 1
    restored = ex.extract(sessions[0])
    assert restored.metadata.source_agent == "cursor"
    assert restored.transcript[0].content == "Ship the feature"
    assert restored.transcript[1].content == "Done."


def test_atif_reads_harbor_trial_layout(tmp_path: Path):
    trial = tmp_path / "trial"
    agent_dir = trial / "agent"
    agent_dir.mkdir(parents=True)
    traj = {
        "schema_version": "ATIF-v1.8",
        "session_id": "harbor-1",
        "agent": {"name": "claude-code", "version": "1.0"},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Solve the task"},
            {"step_id": 2, "source": "agent", "message": "Working on it."},
        ],
    }
    (agent_dir / "trajectory.json").write_text(json.dumps(traj), encoding="utf-8")

    ex = AtifExtractor(tmp_path / "unused")
    sessions = ex.list_sessions(trial)
    assert any(s.session_id == "harbor-1" for s in sessions)
    t = ex.extract(next(s for s in sessions if s.session_id == "harbor-1"))
    assert t.transcript[0].content == "Solve the task"


def test_inject_permissions(tmp_path: Path):
    home = tmp_path / "trajectories"
    project = tmp_path / "p"
    project.mkdir()
    path = AtifInjector(home).inject(
        CanonicalTranscript(
            metadata=Metadata(
                session_id="s",
                source_agent="codex",
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
                    content="hi",
                )
            ],
        )
    )
    assert path.stat().st_mode & 0o777 == 0o600
