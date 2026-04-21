from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / ".codex"
    (home / "sessions").mkdir(parents=True)
    return home


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    (home / "projects").mkdir(parents=True)
    return home


@pytest.fixture
def project_cwd(tmp_path: Path) -> Path:
    p = tmp_path / "my-app"
    p.mkdir()
    return p


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def codex_session_factory(codex_home: Path):
    """Create a fake codex rollout-*.jsonl and return its path."""

    def _factory(
        cwd: str,
        messages: list[dict] | None = None,
        session_id: str = "019abcde-1111-2222-3333-444455556666",
        when: datetime | None = None,
    ) -> Path:
        when = when or datetime.now(timezone.utc)
        y = f"{when.year:04d}"
        m = f"{when.month:02d}"
        d = f"{when.day:02d}"
        stamp = when.strftime("%Y-%m-%dT%H-%M-%S")
        path = codex_home / "sessions" / y / m / d / f"rollout-{stamp}-{session_id}.jsonl"
        ts = when.isoformat().replace("+00:00", "Z")
        records: list[dict] = [
            {
                "timestamp": ts,
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": ts,
                    "cwd": cwd,
                    "originator": "codex_cli_rs",
                    "cli_version": "0.114.0",
                    "git": {"branch": "main"},
                },
            },
            {
                "timestamp": ts,
                "type": "turn_context",
                "payload": {"model": "gpt-5.4"},
            },
        ]
        records.extend(messages or [])
        _write_jsonl(path, records)
        return path

    return _factory


@pytest.fixture
def claude_session_factory(claude_home: Path):
    def _factory(cwd: str, records: list[dict]) -> Path:
        from handoff.agents.claude import encode_project_dir

        encoded = encode_project_dir(cwd)
        session_id = "00000000-1111-2222-3333-444455556666"
        path = claude_home / "projects" / encoded / f"{session_id}.jsonl"
        _write_jsonl(path, records)
        return path

    return _factory
