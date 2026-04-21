from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from handoff.cli import main


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
