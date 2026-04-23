from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from handoff.cli import _resume_hint, main


def test_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "handoff" in result.output


def test_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0


def test_transfer_no_sessions_errors(tmp_path: Path, monkeypatch) -> None:
    # Point HOME at an empty temp dir so there are no sessions to find.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["codex", "claude", "--cwd", str(tmp_path / "app")])
    assert result.exit_code != 0
    assert (
        "no codex sessions" in result.output.lower()
        or "no codex sessions" in (result.stderr_bytes or b"").decode()
    )


def test_transfer_session_id_is_scoped_to_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    from handoff.agents.codex import CodexInjector
    from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso

    source_project = tmp_path / "source-project"
    other_project = tmp_path / "other-project"
    source_project.mkdir()
    other_project.mkdir()

    transcript = CanonicalTranscript(
        metadata=Metadata(
            session_id="src",
            source_agent="claude",
            source_session_path="/tmp/src.jsonl",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=1,
            cwd=str(other_project),
            model="gpt-5.4",
        ),
        transcript=[
            Message(
                id="1",
                timestamp=now_iso(),
                author="user",
                type="message",
                content="Continue from the other project",
            )
        ],
    )
    session_path = CodexInjector(tmp_path / ".codex").inject(transcript)

    first_line = session_path.read_text(encoding="utf-8").splitlines()[0]
    session_uuid = json.loads(first_line)["payload"]["id"]

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["codex", "claude", "--cwd", str(source_project), "--session-id", session_uuid],
    )

    assert result.exit_code != 0
    output = result.output.lower()
    assert "no codex session with id starting with" in output
    assert str(source_project).lower() in output


def test_unknown_agent_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["transfer", "bogus", "claude"])
    assert result.exit_code != 0


def test_resume_hint_uses_codex_session_uuid(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-04-21T20-37-58-abc.jsonl"
    rollout.write_text(
        '{"timestamp":"2026-04-21T20:37:58Z","type":"session_meta","payload":{"id":"392bdabb-6109-4343-81e5-6b7ca056b09d"}}\n',
        encoding="utf-8",
    )

    hint = _resume_hint("codex", rollout)
    assert hint.command == "codex resume 392bdabb-6109-4343-81e5-6b7ca056b09d"
    assert "codex resume 392bdabb-6109-4343-81e5-6b7ca056b09d" in hint.text
    assert "most recent rollout is picked up" not in hint.text


def test_resume_hint_claude_has_copyable_command(tmp_path: Path) -> None:
    session = tmp_path / "4c9b7967-90d7-4fab-8e1d-6a95f1b3c8e2.jsonl"
    session.write_text("", encoding="utf-8")
    hint = _resume_hint("claude", session)
    assert hint.command == "claude --resume 4c9b7967-90d7-4fab-8e1d-6a95f1b3c8e2"


def test_resume_hint_opencode_has_no_copyable_command(tmp_path: Path) -> None:
    session = tmp_path / "abc.json"
    session.write_text("", encoding="utf-8")
    hint = _resume_hint("opencode", session)
    assert hint.command is None
