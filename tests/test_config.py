from __future__ import annotations

from pathlib import Path

from handoff.config import Config, ensure_config, load_config


def test_load_config_returns_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.redact_secrets is True
    assert cfg.claude_home == Path.home() / ".claude"
    assert cfg.codex_home == Path.home() / ".codex"


def test_ensure_config_creates_file(tmp_path):
    path = tmp_path / "config.toml"
    ensure_config(path)
    assert path.exists()
    text = path.read_text()
    assert "claude_home" in text


def test_load_config_parses_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[agents]
claude_home = "/opt/claude"
codex_home = "/opt/codex"

[defaults]
redact_secrets = false
format = "json"

[redaction]
enabled = false
patterns = ["MY_SECRET=.*"]
"""
    )
    cfg = load_config(path)
    assert cfg.claude_home == Path("/opt/claude")
    assert cfg.codex_home == Path("/opt/codex")
    assert cfg.redact_secrets is False
    assert cfg.default_format == "json"
    assert cfg.redaction_enabled is False
    assert cfg.redaction_patterns == ["MY_SECRET=.*"]


def test_expanduser_in_paths(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[agents]\nclaude_home = "~/claude"\n')
    cfg = load_config(path)
    assert "~" not in str(cfg.claude_home)
    assert str(cfg.claude_home).endswith("/claude")


def test_home_for_unknown_agent():
    cfg = Config()
    try:
        cfg.home_for("nonexistent")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
