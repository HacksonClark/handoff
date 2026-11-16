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


def _default_atif_home() -> Path:
    return Path.home() / ".handoff" / "trajectories"


def _default_cursor_home() -> Path:
    return Path.home() / ".cursor"


def _default_gemini_home() -> Path:
    return Path.home() / ".gemini"


def _default_aider_home() -> Path:
    # Aider history is project-local; home is only a fallback search root.
    return Path.home()


DEFAULT_CONFIG_TEXT = """# handoff configuration

[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
cursor_home = "~/.cursor"
gemini_home = "~/.gemini"
# opencode follows XDG — override below if yours lives somewhere else.
# opencode_home = "~/.local/share/opencode"
# Neutral ATIF trajectory store (Harbor-compatible JSON).
# atif_home = "~/.handoff/trajectories"
# aider_home is only a fallback; aider history lives in the project dir.
# aider_home = "~"

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

# Built-in default homes. Plugins/unknown agents resolve via ``{name}_home``
# in the config file, else ``~/.{name}``.
_BUILTIN_HOME_FACTORIES: dict[str, Any] = {
    "claude": lambda: Path.home() / ".claude",
    "codex": lambda: Path.home() / ".codex",
    "opencode": _default_opencode_home,
    "cursor": _default_cursor_home,
    "gemini": _default_gemini_home,
    "aider": _default_aider_home,
    "atif": _default_atif_home,
}


@dataclass
class Config:
    claude_home: Path = field(default_factory=lambda: Path.home() / ".claude")
    codex_home: Path = field(default_factory=lambda: Path.home() / ".codex")
    opencode_home: Path = field(default_factory=_default_opencode_home)
    cursor_home: Path = field(default_factory=_default_cursor_home)
    gemini_home: Path = field(default_factory=_default_gemini_home)
    aider_home: Path = field(default_factory=_default_aider_home)
    atif_home: Path = field(default_factory=_default_atif_home)

    # Extra ``{agent}_home`` entries from config / plugins.
    agent_homes: dict[str, Path] = field(default_factory=dict)

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
        cfg.cursor_home = _expand(agents.get("cursor_home"), cfg.cursor_home)
        cfg.gemini_home = _expand(agents.get("gemini_home"), cfg.gemini_home)
        cfg.aider_home = _expand(agents.get("aider_home"), cfg.aider_home)
        cfg.atif_home = _expand(agents.get("atif_home"), cfg.atif_home)

        # Capture any other ``*_home`` keys so plugins don't need Config edits.
        known_keys = {
            "claude_home",
            "codex_home",
            "opencode_home",
            "cursor_home",
            "gemini_home",
            "aider_home",
            "atif_home",
        }
        for key, val in agents.items():
            if key in known_keys or not key.endswith("_home"):
                continue
            if isinstance(val, str):
                agent_name = key[: -len("_home")]
                cfg.agent_homes[agent_name] = Path(val).expanduser()

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
            "cursor": self.cursor_home,
            "gemini": self.gemini_home,
            "aider": self.aider_home,
            "atif": self.atif_home,
        }
        if agent in mapping:
            return mapping[agent]
        if agent in self.agent_homes:
            return self.agent_homes[agent]
        # Open default for plugins: ~/.{agent}
        factory = _BUILTIN_HOME_FACTORIES.get(agent)
        if factory is not None:
            return factory()
        return Path.home() / f".{agent}"


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
