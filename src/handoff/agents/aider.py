"""Aider adapter — reads/writes ``.aider.chat.history.md`` in the project.

Aider appends every launch to a single Markdown file at the repo root
(default ``.aider.chat.history.md``). Sessions begin with::

    # aider chat started at 2026-07-17 09:00:00

User turns are ``#### …`` headings; plain Markdown is assistant output;
``> ``-prefixed lines are tool/status output.

Injection appends a new session block with a catch-up summary. Resume with
``aider --restore-chat-history`` (loads the whole file).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from handoff.agents.base import (
    Extractor,
    Injector,
    SessionRef,
    register_extractor,
    register_injector,
)
from handoff.canonical import (
    Artifacts,
    CanonicalTranscript,
    Message,
    Metadata,
    now_iso,
    strip_infra,
)

_SESSION_START = re.compile(
    r"^#\s+aider chat started at\s+(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_USER_HEADING = re.compile(r"^####\s+(.*)$")
_AIDER_CMD = re.compile(
    r"^/(add|drop|undo|clear|reset|quit|exit|help|ask|code|context|tokens|"
    r"model|models|chat|copy|paste|run|git|test|lint|map|read|commit|"
    r"architect|editor)\b",
    re.IGNORECASE,
)


def _history_path(cwd: Path, home: Path) -> Path:
    """Resolve the aider history file for a project.

    Prefer the project-local file; fall back to ``home`` if it points at a
    file, otherwise ``home / .aider.chat.history.md``.
    """
    local = Path(cwd).resolve() / ".aider.chat.history.md"
    if local.is_file():
        return local
    home = Path(home).expanduser()
    if home.is_file():
        return home
    candidate = home / ".aider.chat.history.md"
    if candidate.is_file():
        return candidate
    return local  # default write target even if missing


def _parse_session_ts(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return now_iso()


def _split_sessions(text: str) -> list[tuple[str, str]]:
    """Return list of (started_at_iso, body) for each session in the file."""
    matches = list(_SESSION_START.finditer(text))
    if not matches:
        if text.strip():
            return [(now_iso(), text)]
        return []
    sessions: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sessions.append((_parse_session_ts(m.group(1)), text[start:end]))
    return sessions


def _parse_body(body: str, session_ts: str) -> tuple[list[Message], list[str], str | None]:
    messages: list[Message] = []
    files: list[str] = []
    title: str | None = None
    lines = body.splitlines()
    i = 0
    msg_i = 0
    while i < len(lines):
        line = lines[i]
        um = _USER_HEADING.match(line)
        if um:
            content = um.group(1).strip()
            # Collect continuation lines until next role marker.
            i += 1
            cont: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if _USER_HEADING.match(nxt) or nxt.startswith("# aider chat"):
                    break
                if nxt.startswith(">"):
                    break
                # Plain text after a #### user line before assistant prose:
                # aider puts the user question only on the #### line usually.
                # If we see non-empty plain lines, they belong to the assistant
                # — stop collecting for the user turn.
                if nxt.strip() and not nxt.startswith("####"):
                    break
                cont.append(nxt)
                i += 1
            if cont:
                extra = "\n".join(cont).strip()
                if extra:
                    content = f"{content}\n{extra}".strip()
            if _AIDER_CMD.match(content):
                continue
            msg_i += 1
            if title is None and content:
                title = content[:80]
            messages.append(
                Message(
                    id=f"aider-u-{msg_i}",
                    timestamp=session_ts,
                    author="user",
                    type="message",
                    content=content,
                )
            )
            continue

        if line.startswith(">"):
            # Tool / status block
            block = [line[1:].lstrip()]
            i += 1
            while i < len(lines) and (
                lines[i].startswith(">")
                or (
                    block
                    and lines[i].strip()
                    and not _USER_HEADING.match(lines[i])
                    and not lines[i].startswith("#")
                )
            ):
                if lines[i].startswith(">"):
                    block.append(lines[i][1:].lstrip())
                elif _USER_HEADING.match(lines[i]) or lines[i].startswith("# aider"):
                    break
                else:
                    # Unprefixed continuation of tool output
                    block.append(lines[i])
                i += 1
            text = "\n".join(block).strip()
            for m in re.finditer(r"(?:Applied edit to|Added|Added .* to)\s+(\S+)", text):
                files.append(m.group(1).rstrip(""))
            if text:
                msg_i += 1
                messages.append(
                    Message(
                        id=f"aider-tool-{msg_i}",
                        timestamp=session_ts,
                        author="agent",
                        type="tool_result",
                        content=text,
                        metadata={"tool_name": "aider"},
                    )
                )
            continue

        if line.startswith("# aider chat"):
            i += 1
            continue

        # Assistant prose
        if line.strip():
            block = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if (
                    _USER_HEADING.match(nxt)
                    or nxt.startswith(">")
                    or nxt.startswith("# aider chat")
                ):
                    break
                block.append(nxt)
                i += 1
            content = "\n".join(block).strip()
            if content:
                msg_i += 1
                messages.append(
                    Message(
                        id=f"aider-a-{msg_i}",
                        timestamp=session_ts,
                        author="agent",
                        type="message",
                        content=content,
                    )
                )
            continue
        i += 1

    # Dedupe files preserving order
    seen: set[str] = set()
    uniq_files: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq_files.append(f)
    return messages, uniq_files, title


class AiderExtractor(Extractor):
    agent_name = "aider"

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        if cwd is None:
            # Without a project we can only see a home-level history file.
            path = Path(self.home).expanduser()
            if path.is_dir():
                path = path / ".aider.chat.history.md"
            if not path.is_file():
                return []
            return self._refs_from_file(path, cwd=str(path.parent))
        path = _history_path(cwd, self.home)
        if not path.is_file():
            return []
        return self._refs_from_file(path, cwd=str(Path(cwd).resolve()))

    def _refs_from_file(self, path: Path, cwd: str) -> list[SessionRef]:
        text = path.read_text(encoding="utf-8", errors="replace")
        sessions = _split_sessions(text)
        refs: list[SessionRef] = []
        for idx, (ts, body) in enumerate(sessions):
            messages, _files, title = _parse_body(body, ts)
            session_id = f"{path.stem}-{idx + 1}"
            refs.append(
                SessionRef(
                    session_id=session_id,
                    path=path,
                    cwd=cwd,
                    created_at=ts,
                    last_activity=ts,
                    message_count=len(messages),
                    title=title,
                )
            )
        refs.reverse()  # newest first
        return refs

    def find_by_id(self, session_id: str, cwd: Path | None = None) -> SessionRef | None:
        for s in self.list_sessions(cwd):
            if s.session_id == session_id or s.session_id.startswith(session_id):
                return s
            # Allow "3" to mean session index 3 in the file.
            if session_id.isdigit() and s.session_id.endswith(f"-{session_id}"):
                return s
        return None

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        text = ref.path.read_text(encoding="utf-8", errors="replace")
        sessions = _split_sessions(text)
        # session id ends with -<1-based index>
        try:
            idx = int(ref.session_id.rsplit("-", 1)[-1]) - 1
        except ValueError:
            idx = len(sessions) - 1
        if idx < 0 or idx >= len(sessions):
            idx = len(sessions) - 1
        ts, body = sessions[idx]
        messages, files, _title = _parse_body(body, ts)
        meta = Metadata(
            session_id=ref.session_id,
            source_agent="aider",
            source_session_path=str(ref.path),
            created_at=ts,
            last_activity=ts,
            message_count=len(messages),
            cwd=ref.cwd or "",
        )
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(files_modified=files),
        )


class AiderInjector(Injector):
    agent_name = "aider"

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        from handoff.formatters import to_markdown

        cwd = Path(transcript.metadata.cwd or Path.cwd()).resolve()
        path = cwd / ".aider.chat.history.md"
        pruned = strip_infra(deepcopy(transcript))
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"\n# aider chat started at {stamp}\n\n"
            f"#### [handoff] Continue from {transcript.metadata.source_agent}\n\n"
            f"{to_markdown(pruned)}\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(block.lstrip("\n"))
        return path


def register() -> None:
    register_extractor("aider", lambda home: AiderExtractor(home))
    register_injector("aider", lambda home: AiderInjector(home))


register()
