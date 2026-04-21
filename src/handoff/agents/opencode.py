"""OpenCode adapter — reads/writes ``~/.local/share/opencode/storage``.

Storage layout (as of OpenCode 1.1.x)::

    storage/
      project/<project_id>.json          # { id, worktree, vcs, time: {...} }
      session/<project_id>/<session_id>.json
                                         # { id, slug, directory, title, time, ... }
      message/<session_id>/<msg_id>.json # { id, role, time, parentID, modelID, ... }
      part/<msg_id>/<part_id>.json       # { type: text|reasoning|patch|step-*, ... }

Project IDs are hash-like; we resolve them by reading ``project/*.json`` and
matching the ``worktree`` field against the requested cwd.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import time
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


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_private(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as indented JSON with 0600 permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _ms_to_iso(ms: int | float | None) -> str:
    if not ms:
        return ""
    from datetime import datetime, timezone

    try:
        return (
            datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, OSError, OverflowError):
        return ""


class OpenCodeExtractor(Extractor):
    agent_name = "opencode"

    def _storage_root(self) -> Path:
        # Callers may pass either the opencode home (~/.local/share/opencode)
        # or its ``storage`` subdirectory. Normalise.
        if (self.home / "storage").is_dir():
            return self.home / "storage"
        return self.home

    def _projects(self) -> list[dict[str, Any]]:
        project_dir = self._storage_root() / "project"
        if not project_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in project_dir.glob("*.json"):
            data = _load_json(p)
            if data and isinstance(data, dict) and "id" in data:
                out.append(data)
        return out

    def _project_for_cwd(self, cwd: Path) -> dict[str, Any] | None:
        target = str(Path(cwd).resolve())
        for proj in self._projects():
            if str(proj.get("worktree", "")) == target:
                return proj
        return None

    def _session_files(self, project_id: str | None) -> list[Path]:
        root = self._storage_root() / "session"
        if not root.is_dir():
            return []
        if project_id:
            subdir = root / project_id
            return sorted(subdir.glob("*.json")) if subdir.is_dir() else []
        files: list[Path] = []
        for sub in root.iterdir():
            if sub.is_dir():
                files.extend(sub.glob("*.json"))
        return files

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        project_id: str | None = None
        if cwd is not None:
            proj = self._project_for_cwd(cwd)
            if proj is None:
                return []
            project_id = proj["id"]

        refs: list[SessionRef] = []
        for path in self._session_files(project_id):
            data = _load_json(path)
            if not data:
                continue
            sid = data.get("id") or path.stem
            directory = data.get("directory") or ""
            tinfo = data.get("time") or {}
            created = _ms_to_iso(tinfo.get("created"))
            updated = _ms_to_iso(tinfo.get("updated")) or created
            # Count messages on disk
            msg_dir = self._storage_root() / "message" / sid
            count = len(list(msg_dir.glob("*.json"))) if msg_dir.is_dir() else 0
            refs.append(
                SessionRef(
                    session_id=sid,
                    path=path,
                    cwd=directory or None,
                    created_at=created,
                    last_activity=updated,
                    message_count=count,
                    title=data.get("title"),
                )
            )
        refs.sort(key=lambda r: r.last_activity or "", reverse=True)
        return refs

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        data = _load_json(ref.path) or {}
        cwd = data.get("directory") or ref.cwd or ""
        session_id = data.get("id") or ref.session_id
        created = _ms_to_iso((data.get("time") or {}).get("created")) or ref.created_at
        updated = _ms_to_iso((data.get("time") or {}).get("updated")) or ref.last_activity

        msg_dir = self._storage_root() / "message" / session_id
        msg_files = sorted(msg_dir.glob("*.json")) if msg_dir.is_dir() else []
        raw_messages: list[dict[str, Any]] = []
        for f in msg_files:
            m = _load_json(f)
            if m:
                raw_messages.append(m)
        # Order by created time
        raw_messages.sort(key=lambda m: (m.get("time") or {}).get("created") or 0)

        messages: list[Message] = []
        model: str | None = None
        files_modified: set[str] = set()

        for m in raw_messages:
            role = m.get("role") or "user"
            model = m.get("modelID") or model
            ts = _ms_to_iso((m.get("time") or {}).get("created")) or now_iso()
            msg_id = m.get("id") or f"opencode-{len(messages)}"

            part_dir = self._storage_root() / "part" / msg_id
            parts = sorted(part_dir.glob("*.json")) if part_dir.is_dir() else []
            for pf in parts:
                p = _load_json(pf)
                if not p:
                    continue
                ptype = p.get("type")
                if ptype == "text":
                    text = p.get("text") or ""
                    if not text.strip():
                        continue
                    messages.append(
                        Message(
                            id=p.get("id") or msg_id,
                            timestamp=ts,
                            author="agent" if role == "assistant" else "user",
                            type="message",
                            content=text,
                            metadata={"model": model} if model else {},
                        )
                    )
                elif ptype == "reasoning":
                    text = p.get("text") or ""
                    if not text.strip():
                        continue
                    messages.append(
                        Message(
                            id=p.get("id") or msg_id,
                            timestamp=ts,
                            author="agent",
                            type="reasoning",
                            content=text,
                        )
                    )
                elif ptype == "patch":
                    for fpath in p.get("files") or []:
                        if isinstance(fpath, str):
                            files_modified.add(fpath)
                # step-start / step-finish / other → skip

        meta = Metadata(
            session_id=session_id,
            source_agent="opencode",
            source_session_path=str(ref.path),
            created_at=created or now_iso(),
            last_activity=updated or now_iso(),
            message_count=len(messages),
            cwd=cwd,
            model=model,
        )
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(files_modified=sorted(files_modified)),
        )


# --- injection helpers ------------------------------------------------------

_ID_ALPHABET = string.ascii_letters + string.digits


def _rand_id(prefix: str, length: int = 25) -> str:
    rand = "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))
    return f"{prefix}{rand}"


def _project_id_for(cwd: str) -> str:
    """OpenCode derives project ids from the worktree path via a hash. We
    mimic that with SHA-1 of the absolute path so we get a stable id."""
    import hashlib

    return hashlib.sha1(str(Path(cwd).resolve()).encode()).hexdigest()


class OpenCodeInjector(Injector):
    agent_name = "opencode"

    def _storage_root(self) -> Path:
        if (self.home / "storage").is_dir() or not (self.home / "storage").exists():
            return self.home / "storage"
        return self.home

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        from handoff.formatters import to_markdown

        transcript_pruned = strip_infra(deepcopy(transcript))

        cwd = transcript.metadata.cwd or str(Path.cwd())
        project_id = _project_id_for(cwd)
        session_id = _rand_id("ses_", 28)
        now_ms = int(time.time() * 1000)

        root = self._storage_root()
        (root / "project").mkdir(parents=True, exist_ok=True)
        (root / "session" / project_id).mkdir(parents=True, exist_ok=True)
        (root / "message" / session_id).mkdir(parents=True, exist_ok=True)

        # project file (create if missing)
        project_file = root / "project" / f"{project_id}.json"
        if not project_file.exists():
            _write_json_private(
                project_file,
                {
                    "id": project_id,
                    "worktree": str(Path(cwd).resolve()),
                    "vcs": "git",
                    "sandboxes": [],
                    "time": {"created": now_ms, "updated": now_ms},
                },
            )

        # session file
        session_path = root / "session" / project_id / f"{session_id}.json"
        _write_json_private(
            session_path,
            {
                "id": session_id,
                "slug": f"handoff-{session_id[4:12]}",
                "version": "handoff-0.1.0",
                "projectID": project_id,
                "directory": str(Path(cwd).resolve()),
                "title": (
                    f"Handoff from {transcript.metadata.source_agent}"
                    if transcript.metadata.source_agent
                    else "Handoff"
                ),
                "time": {"created": now_ms, "updated": now_ms},
                "summary": {"additions": 0, "deletions": 0, "files": 0},
            },
        )

        # single "catch-up" user message with markdown transcript
        msg_id = _rand_id("msg_", 25)
        msg_file = root / "message" / session_id / f"{msg_id}.json"
        _write_json_private(
            msg_file,
            {
                "id": msg_id,
                "sessionID": session_id,
                "role": "user",
                "time": {"created": now_ms, "completed": now_ms},
                "path": {
                    "cwd": str(Path(cwd).resolve()),
                    "root": str(Path(cwd).resolve()),
                },
            },
        )

        part_id = _rand_id("prt_", 25)
        part_dir = root / "part" / msg_id
        part_dir.mkdir(parents=True, exist_ok=True)
        catch_up = (
            f"[handoff] Context transferred from {transcript.metadata.source_agent}. "
            "Read the transcript below to understand prior work, then continue.\n\n"
            + to_markdown(transcript_pruned)
        )
        _write_json_private(
            part_dir / f"{part_id}.json",
            {
                "id": part_id,
                "sessionID": session_id,
                "messageID": msg_id,
                "type": "text",
                "text": catch_up,
            },
        )

        return session_path


def register() -> None:
    """Entry-point hook. Safe to call multiple times."""
    register_extractor("opencode", lambda home: OpenCodeExtractor(home))
    register_injector("opencode", lambda home: OpenCodeInjector(home))


register()
