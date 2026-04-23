from __future__ import annotations

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
