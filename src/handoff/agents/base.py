"""Agent adapter registry. Each agent plugs in a pair (extractor, injector).

Adapters can be registered three ways:

1. **Built-ins** — imported at the bottom of this module so they register on
   package import.
2. **Entry points** — any installed distribution declaring the
   ``handoff.agents`` entry-point group is discovered via
   :func:`load_plugins`. Each entry point should resolve to a callable that
   takes no arguments and calls :func:`register_extractor` /
   :func:`register_injector`.
3. **Programmatic** — library users can call :func:`register_extractor` /
   :func:`register_injector` directly.

Entry-point example (in a third-party ``pyproject.toml``)::

    [project.entry-points."handoff.agents"]
    aider = "my_package.aider:register"

where ``my_package.aider`` defines ``def register(): ...``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

from handoff.canonical import CanonicalTranscript

log = logging.getLogger(__name__)


@dataclass
class SessionRef:
    """Lightweight handle to a session on disk."""

    session_id: str
    path: Path
    cwd: str | None
    created_at: str
    last_activity: str
    message_count: int
    title: str | None = None


class Extractor(ABC):
    """Read an agent's session storage."""

    agent_name: str = ""

    def __init__(self, home: Path) -> None:
        self.home = Path(home).expanduser()

    @abstractmethod
    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        """Return sessions, newest first. If cwd given, only sessions for that dir."""

    @abstractmethod
    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        """Parse a session file into a canonical transcript."""

    def find_latest(self, cwd: Path) -> SessionRef | None:
        sessions = self.list_sessions(cwd)
        return sessions[0] if sessions else None

    def find_by_id(self, session_id: str, cwd: Path | None = None) -> SessionRef | None:
        for s in self.list_sessions(cwd):
            if s.session_id == session_id or s.session_id.startswith(session_id):
                return s
        return None


class Injector(ABC):
    """Write a canonical transcript into an agent's session storage."""

    agent_name: str = ""

    def __init__(self, home: Path) -> None:
        self.home = Path(home).expanduser()

    @abstractmethod
    def inject(self, transcript: CanonicalTranscript) -> Path:
        """Create a new session file. Return its path."""


_EXTRACTORS: dict[str, Callable[[Path], Extractor]] = {}
_INJECTORS: dict[str, Callable[[Path], Injector]] = {}


def register_extractor(name: str, factory: Callable[[Path], Extractor]) -> None:
    _EXTRACTORS[name.lower()] = factory


def register_injector(name: str, factory: Callable[[Path], Injector]) -> None:
    _INJECTORS[name.lower()] = factory


def get_extractor(name: str, home: Path) -> Extractor:
    load_plugins()
    name = name.lower()
    if name not in _EXTRACTORS:
        raise ValueError(
            f"No extractor registered for agent {name!r}. Known: {sorted(_EXTRACTORS)}"
        )
    return _EXTRACTORS[name](home)


def get_injector(name: str, home: Path) -> Injector:
    load_plugins()
    name = name.lower()
    if name not in _INJECTORS:
        raise ValueError(f"No injector registered for agent {name!r}. Known: {sorted(_INJECTORS)}")
    return _INJECTORS[name](home)


def known_agents() -> list[str]:
    load_plugins()
    return sorted(set(_EXTRACTORS) | set(_INJECTORS))


_PLUGINS_LOADED = False


def load_plugins(group: str = "handoff.agents") -> list[str]:
    """Discover and register adapters declared under an entry-point group.

    Idempotent: repeated calls are no-ops. Returns the list of entry-point
    names that were successfully registered.
    """
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return []
    _PLUGINS_LOADED = True

    registered: list[str] = []
    try:
        eps = entry_points(group=group)
    except TypeError:  # pragma: no cover — Python < 3.10 selector API
        eps = entry_points().get(group, [])  # type: ignore[call-overload]
    for ep in eps:
        try:
            fn = ep.load()
        except Exception as exc:  # pragma: no cover — plugin bugs
            log.warning("handoff: failed to load plugin %s: %s", ep.name, exc)
            continue
        try:
            if callable(fn):
                fn()
            registered.append(ep.name)
        except Exception as exc:  # pragma: no cover
            log.warning("handoff: plugin %s raised during register(): %s", ep.name, exc)
    return registered


# Eagerly register built-in adapters. Imports live at the bottom to avoid
# circular-import cycles during package init.
from handoff.agents import claude as _claude  # noqa: E402,F401
from handoff.agents import codex as _codex  # noqa: E402,F401
from handoff.agents import opencode as _opencode  # noqa: E402,F401
