"""Config loading for ~/.handoff/config.toml."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


CONFIG_DIR = Path.home() / ".handoff"
CONFIG_PATH = CONFIG_DIR / "config.toml"


def _default_opencode_home() -> Path:
    """Respect XDG, fall back to the conventional location."""
    import os

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "opencode"
    return Path.home() / ".local" / "share" / "opencode"


DEFAULT_CONFIG_TEXT = """# handoff configuration

[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
# opencode follows XDG — override below if yours lives somewhere else.
# opencode_home = "~/.local/share/opencode"

[defaults]
redact_secrets = true
auto_inject = true
format = "native"

[redaction]
enabled = true
patterns = [
    "OPENAI_API_KEY\\\\s*=\\\\s*\\\\S+",
    "ANTHROPIC_API_KEY\\\\s*=\\\\s*\\\\S+",
    "Bearer\\\\s+[A-Za-z0-9_\\\\-\\\\.]{20,}",
    "sk-[A-Za-z0-9]{20,}",
]
"""


@dataclass
class Config:
    claude_home: Path = field(default_factory=lambda: Path.home() / ".claude")
    codex_home: Path = field(default_factory=lambda: Path.home() / ".codex")
    opencode_home: Path = field(default_factory=_default_opencode_home)

    redact_secrets: bool = True
    auto_inject: bool = True
    default_format: str = "native"

    redaction_enabled: bool = True
    redaction_patterns: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        agents = data.get("agents", {})
        defaults = data.get("defaults", {})
        redaction = data.get("redaction", {})

        def _expand(p: str | None, fallback: Path) -> Path:
            return Path(p).expanduser() if p else fallback

        cfg = cls()
        cfg.claude_home = _expand(agents.get("claude_home"), cfg.claude_home)
        cfg.codex_home = _expand(agents.get("codex_home"), cfg.codex_home)
        cfg.opencode_home = _expand(agents.get("opencode_home"), cfg.opencode_home)

        cfg.redact_secrets = bool(defaults.get("redact_secrets", cfg.redact_secrets))
        cfg.auto_inject = bool(defaults.get("auto_inject", cfg.auto_inject))
        cfg.default_format = str(defaults.get("format", cfg.default_format))

        cfg.redaction_enabled = bool(redaction.get("enabled", cfg.redaction_enabled))
        patterns = redaction.get("patterns")
        if isinstance(patterns, list):
            cfg.redaction_patterns = [str(p) for p in patterns]
        return cfg

    def home_for(self, agent: str) -> Path:
        agent = agent.lower()
        mapping = {
            "claude": self.claude_home,
            "codex": self.codex_home,
            "opencode": self.opencode_home,
        }
        if agent not in mapping:
            raise KeyError(f"unknown agent: {agent!r}")
        return mapping[agent]


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config.from_dict(data)


def ensure_config(path: Path | None = None) -> Path:
    """Create a default config file if one doesn't exist. Returns the path."""
    path = path or CONFIG_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TEXT)
    return path
