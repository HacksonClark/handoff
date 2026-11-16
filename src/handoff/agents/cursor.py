"""Cursor adapter — reads/writes ``~/.cursor/projects/<slug>/agent-transcripts``.

Cursor keeps conversation history in several places:

* **Agent transcripts** (Agents Window / cursor-agent): JSONL under
  ``~/.cursor/projects/<project-slug>/agent-transcripts/<id>/<id>.jsonl``
* **IDE sidebar index**: SQLite ``state.vscdb`` (composerData / bubbleId) —
  not written by this adapter (undocumented, brittle)
* **CLI chats**: ``~/.cursor/chats/<hash>/<uuid>/store.db`` — optional future

This adapter focuses on the on-disk JSONL transcripts, which are the durable
source the Agents Window already reads. Injection writes a new catch-up
transcript so the receiving Cursor session can pick up context; it does not
attempt to patch ``state.vscdb``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Timestamps embedded in user_info blocks, e.g.
# <timestamp>Sunday, Jul 26, 2026, 1:06 PM (UTC+3)</timestamp>
_TS_BLOCK = re.compile(
    r"<timestamp>\s*(.*?)\s*</timestamp>",
    re.IGNORECASE | re.DOTALL,
)
_USER_QUERY = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>",
    re.IGNORECASE | re.DOTALL,
)


def encode_project_slug(cwd: Path | str) -> str:
    """Encode a workspace path the way Cursor typically names project dirs.

    Absolute paths become dash-separated slugs::

        /Users/me/app  →  Users-me-app
        /workspace     →  workspace

    Leading separators are stripped (unlike Claude Code's leading ``-``).
    """
    p = str(Path(cwd).expanduser().resolve())
    if "\x00" in p:
        raise ValueError("null byte in cwd")
    # Drop drive letter noise on Windows-ish paths if any.
    slug = p.replace("\\", "/").lstrip("/").replace("/", "-")
    if not slug or slug in (".", "..") or "/" in slug or "\\" in slug:
        raise ValueError(f"invalid project slug encoding: {slug!r}")
    return slug


def _slug_candidates(cwd: Path) -> list[str]:
    """Possible Cursor project-dir names for ``cwd``."""
    resolved = Path(cwd).resolve()
    candidates = [
        encode_project_slug(resolved),
        # Some builds keep the leading dash (Claude-style).
        "-" + encode_project_slug(resolved).lstrip("-"),
        # Folder basename only.
        resolved.name,
        # Cursor cloud / remote often uses the folder name "workspace".
        "workspace",
    ]
    # Dedupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _mtime_iso(path: Path) -> str:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return now_iso()


def _content_blocks(rec: dict[str, Any]) -> list[dict[str, Any]]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _extract_title(text: str) -> str | None:
    m = _USER_QUERY.search(text)
    if not m:
        return None
    title = " ".join(m.group(1).strip().split())
    return title[:80] if title else None


def _parse_embedded_timestamp(text: str) -> str | None:
    """Best-effort parse of Cursor's ``<timestamp>`` user_info block → ISO."""
    m = _TS_BLOCK.search(text)
    if not m:
        return None
    raw = " ".join(m.group(1).strip().split())
    # Drop parenthetical zone if present; fromisoformat can't handle named zones.
    # Keep an explicit offset when present, else assume UTC.
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", raw)
    for fmt in (
        "%A, %b %d, %Y, %I:%M %p",
        "%A, %B %d, %Y, %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


class CursorExtractor(Extractor):
    agent_name = "cursor"

    def _projects_root(self) -> Path:
        return self.home / "projects"

    def _transcript_files(self, project_dir: Path) -> list[Path]:
        root = project_dir / "agent-transcripts"
        if not root.is_dir():
            return []
        files: list[Path] = []
        # Layout A: agent-transcripts/<id>/<id>.jsonl
        for child in root.iterdir():
            if child.is_dir():
                candidate = child / f"{child.name}.jsonl"
                if candidate.is_file():
                    files.append(candidate)
                    continue
                files.extend(child.glob("*.jsonl"))
            elif child.suffix == ".jsonl" and child.is_file():
                # Layout B: flat agent-transcripts/<id>.jsonl
                files.append(child)
        # Skip subagent side-chains by default (…/subagents/*.jsonl).
        return [p for p in files if "subagents" not in p.parts]

    def _project_dirs_for_cwd(self, cwd: Path) -> list[Path]:
        root = self._projects_root()
        if not root.is_dir():
            return []
        matched: list[Path] = []
        for slug in _slug_candidates(cwd):
            d = root / slug
            if d.is_dir():
                matched.append(d)
        if matched:
            return matched
        # Fuzzy: slug contains distinctive path segments from cwd.
        resolved = str(Path(cwd).resolve())
        needle = encode_project_slug(resolved)
        for d in root.iterdir():
            if not d.is_dir():
                continue
            name = d.name.lstrip("-")
            if name == needle or needle.endswith(name) or name.endswith(needle):
                matched.append(d)
        return matched

    def _all_project_dirs(self) -> list[Path]:
        root = self._projects_root()
        if not root.is_dir():
            return []
        return [d for d in root.iterdir() if d.is_dir()]

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        dirs = self._project_dirs_for_cwd(cwd) if cwd else self._all_project_dirs()
        refs: list[SessionRef] = []
        for project_dir in dirs:
            project_cwd = _guess_cwd_from_slug(project_dir.name, cwd)
            for path in self._transcript_files(project_dir):
                ref = self._ref_from_path(path, project_cwd)
                if ref:
                    refs.append(ref)
        refs.sort(key=lambda r: r.last_activity or "", reverse=True)
        return refs

    def _ref_from_path(self, path: Path, project_cwd: str | None) -> SessionRef | None:
        session_id = path.stem
        count = 0
        title: str | None = None
        first_ts = ""
        last_ts = _mtime_iso(path)
        for rec in _iter_jsonl(path):
            if rec.get("type") == "turn_ended":
                continue
            role = rec.get("role")
            if role not in ("user", "assistant"):
                continue
            count += 1
            for block in _content_blocks(rec):
                if block.get("type") != "text":
                    continue
                text = str(block.get("text") or "")
                if not first_ts:
                    embedded = _parse_embedded_timestamp(text)
                    if embedded:
                        first_ts = embedded
                if title is None and role == "user":
                    title = _extract_title(text)
        if count == 0:
            # Empty / unparseable — still list by mtime so the file is visible.
            count = 0
        created = first_ts or last_ts
        return SessionRef(
            session_id=session_id,
            path=path,
            cwd=project_cwd,
            created_at=created,
            last_activity=last_ts,
            message_count=count,
            title=title,
        )

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        messages: list[Message] = []
        files_modified: set[str] = set()
        created_at = ref.created_at or now_iso()
        last_activity = ref.last_activity or created_at
        msg_i = 0
        current_ts = created_at

        for rec in _iter_jsonl(ref.path):
            if rec.get("type") == "turn_ended":
                continue
            role = rec.get("role")
            if role not in ("user", "assistant"):
                continue
            blocks = _content_blocks(rec)
            text_parts: list[str] = []
            for block in blocks:
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "")
                    embedded = _parse_embedded_timestamp(text)
                    if embedded:
                        current_ts = embedded
                        if not created_at or created_at == ref.last_activity:
                            created_at = embedded
                    text_parts.append(text)
                elif btype == "tool_use":
                    # Flush preceding text as its own message first.
                    if text_parts:
                        msg_i += 1
                        messages.append(
                            Message(
                                id=f"msg-{msg_i}",
                                timestamp=current_ts,
                                author="user" if role == "user" else "agent",
                                type="message",
                                content="\n".join(text_parts).strip(),
                            )
                        )
                        text_parts = []
                    name = str(block.get("name") or "tool")
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    call_id = str(block.get("id") or f"call-{msg_i}-{name}")
                    msg_i += 1
                    messages.append(
                        Message(
                            id=call_id,
                            timestamp=current_ts,
                            author="agent",
                            type="tool_call",
                            content=json.dumps(inp, ensure_ascii=False),
                            metadata={
                                "tool_name": name,
                                "tool_call_id": call_id,
                                "arguments": inp,
                            },
                        )
                    )
                    for key in ("path", "file_path", "target_file"):
                        val = inp.get(key)
                        if isinstance(val, str) and val:
                            files_modified.add(val)
                            break

            if text_parts:
                content = "\n".join(text_parts).strip()
                if content:
                    msg_i += 1
                    messages.append(
                        Message(
                            id=f"msg-{msg_i}",
                            timestamp=current_ts,
                            author="user" if role == "user" else "agent",
                            type="message",
                            content=content,
                        )
                    )
            last_activity = current_ts

        meta = Metadata(
            session_id=ref.session_id,
            source_agent="cursor",
            source_session_path=str(ref.path),
            created_at=created_at,
            last_activity=last_activity,
            message_count=len(messages),
            cwd=ref.cwd or "",
        )
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(files_modified=sorted(files_modified)),
        )


def _guess_cwd_from_slug(slug: str, hint: Path | None) -> str | None:
    if hint is not None:
        return str(Path(hint).resolve())
    # Best-effort decode: Users-me-app → /Users/me/app
    name = slug.lstrip("-")
    if not name:
        return None
    return "/" + name.replace("-", "/")


class CursorInjector(Injector):
    agent_name = "cursor"

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        from handoff.formatters import to_markdown

        cwd = transcript.metadata.cwd or str(Path.cwd())
        slug = encode_project_slug(cwd)
        session_id = str(uuid.uuid4())
        session_dir = self.home / "projects" / slug / "agent-transcripts" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{session_id}.jsonl"

        pruned = strip_infra(deepcopy(transcript))
        catch_up = (
            f"[handoff] Context transferred from {transcript.metadata.source_agent}. "
            "The transcript from the previous session follows. "
            "Read it to understand what was done, then continue from where it left off.\n\n"
            + to_markdown(pruned)
        )
        record = {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"<timestamp>{now_iso()}</timestamp>\n"
                            f"<user_query>\n{catch_up}\n</user_query>"
                        ),
                    }
                ]
            },
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


def register() -> None:
    register_extractor("cursor", lambda home: CursorExtractor(home))
    register_injector("cursor", lambda home: CursorInjector(home))


register()
