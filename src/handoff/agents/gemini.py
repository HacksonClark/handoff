"""Gemini CLI adapter — reads/writes ``~/.gemini/tmp/<hash>/chats/``.

Gemini CLI (v0.39+) stores sessions as JSON or JSONL under a per-project
temp directory. Project identity is a hash of the workspace path; we match
by reading chat metadata ``cwd`` / ``projectRoot`` fields when present, and
fall back to listing all chats when filtering by cwd finds nothing with
matching metadata.
"""

from __future__ import annotations

import json
import os
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


def _iter_json_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield dict records from a ``.json`` (array/object) or ``.jsonl`` file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return
    if not text:
        return
    if path.suffix == ".jsonl" or "\n" in text and not text.startswith(("{", "[")):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Maybe JSONL misnamed as .json
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data, dict):
        # Whole-session document with embedded messages.
        if isinstance(data.get("messages"), list):
            yield data
            return
        yield data


def _iso_from_any(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        # ms or seconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, ValueError, OverflowError):
            return ""
    if isinstance(value, str):
        return value
    return ""


def _mtime_iso(path: Path) -> str:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return now_iso()


def _session_cwd(data: dict[str, Any]) -> str | None:
    for key in ("cwd", "projectRoot", "project_root", "workingDirectory"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    meta = data.get("metadata")
    if isinstance(meta, dict):
        for key in ("cwd", "projectRoot", "project_root"):
            val = meta.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _flatten_messages(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize various Gemini session shapes into a list of role/content dicts."""
    if isinstance(data.get("messages"), list):
        return [m for m in data["messages"] if isinstance(m, dict)]
    # Single-turn record
    if "role" in data or "type" in data:
        return [data]
    return []


class GeminiExtractor(Extractor):
    agent_name = "gemini"

    def _chats_dirs(self) -> list[Path]:
        tmp = self.home / "tmp"
        if not tmp.is_dir():
            return []
        found: list[Path] = []
        for child in tmp.iterdir():
            chats = child / "chats"
            if chats.is_dir():
                found.append(chats)
        return found

    def _chat_files(self) -> list[Path]:
        files: list[Path] = []
        for chats in self._chats_dirs():
            files.extend(chats.glob("*.json"))
            files.extend(chats.glob("*.jsonl"))
        return files

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        want = str(Path(cwd).resolve()) if cwd else None
        refs: list[SessionRef] = []
        for path in self._chat_files():
            ref = self._ref_from_path(path)
            if ref is None:
                continue
            if want is not None and ref.cwd:
                try:
                    if Path(ref.cwd).resolve() != Path(want).resolve():
                        continue
                except OSError:
                    if ref.cwd != want:
                        continue
            elif want is not None and not ref.cwd:
                # No metadata — include only when --all (cwd is None).
                continue
            refs.append(ref)
        # If cwd filter yielded nothing, don't silently widen (caller can use --all).
        refs.sort(key=lambda r: r.last_activity or "", reverse=True)
        return refs

    def _ref_from_path(self, path: Path) -> SessionRef | None:
        records = list(_iter_json_records(path))
        if not records:
            return None
        session_cwd: str | None = None
        created = ""
        last = _mtime_iso(path)
        count = 0
        title: str | None = None
        session_id = path.stem
        for rec in records:
            session_cwd = session_cwd or _session_cwd(rec)
            if rec.get("sessionId") or rec.get("id"):
                session_id = str(rec.get("sessionId") or rec.get("id"))
            for msg in _flatten_messages(rec):
                count += 1
                ts = _iso_from_any(msg.get("timestamp") or msg.get("time") or msg.get("createdAt"))
                if ts:
                    if not created:
                        created = ts
                    last = ts
                if title is None:
                    role = (msg.get("role") or msg.get("type") or "").lower()
                    if role in ("user", "human"):
                        content = _message_content(msg)
                        if content:
                            title = content[:80]
            # Whole-doc timestamps
            for key in ("startTime", "createdAt", "timestamp", "updatedAt"):
                ts = _iso_from_any(rec.get(key))
                if ts:
                    if not created:
                        created = ts
                    if key in ("updatedAt", "timestamp"):
                        last = ts
        return SessionRef(
            session_id=session_id,
            path=path,
            cwd=session_cwd,
            created_at=created or last,
            last_activity=last,
            message_count=count,
            title=title,
        )

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        messages: list[Message] = []
        files_modified: set[str] = set()
        created_at = ref.created_at or now_iso()
        last_activity = ref.last_activity or created_at
        cwd = ref.cwd or ""
        msg_i = 0

        for rec in _iter_json_records(ref.path):
            cwd = cwd or (_session_cwd(rec) or "")
            for msg in _flatten_messages(rec):
                role_raw = (msg.get("role") or msg.get("type") or "").lower()
                ts = (
                    _iso_from_any(msg.get("timestamp") or msg.get("time") or msg.get("createdAt"))
                    or last_activity
                )
                if role_raw in ("user", "human"):
                    author = "user"
                    mtype = "message"
                elif role_raw in ("model", "assistant", "gemini"):
                    author = "agent"
                    mtype = "message"
                elif role_raw in ("system",):
                    author = "system"
                    mtype = "message"
                elif role_raw in ("tool", "function"):
                    author = "agent"
                    mtype = "tool_result"
                else:
                    author = "agent"
                    mtype = "message"

                # Function calls embedded in model turns
                parts = msg.get("parts") if isinstance(msg.get("parts"), list) else None
                if parts:
                    text_bits: list[str] = []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if "text" in part:
                            text_bits.append(str(part["text"]))
                        fc = part.get("functionCall") or part.get("function_call")
                        if isinstance(fc, dict):
                            if text_bits:
                                msg_i += 1
                                messages.append(
                                    Message(
                                        id=f"gem-{msg_i}",
                                        timestamp=ts,
                                        author=author,
                                        type="message",
                                        content="\n".join(text_bits),
                                    )
                                )
                                text_bits = []
                            name = str(fc.get("name") or "tool")
                            args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                            msg_i += 1
                            call_id = str(fc.get("id") or f"gem-call-{msg_i}")
                            messages.append(
                                Message(
                                    id=call_id,
                                    timestamp=ts,
                                    author="agent",
                                    type="tool_call",
                                    content=json.dumps(args, ensure_ascii=False),
                                    metadata={
                                        "tool_name": name,
                                        "tool_call_id": call_id,
                                        "arguments": args,
                                    },
                                )
                            )
                        fr = part.get("functionResponse") or part.get("function_response")
                        if isinstance(fr, dict):
                            msg_i += 1
                            content = fr.get("response", fr)
                            if not isinstance(content, str):
                                content = json.dumps(content, ensure_ascii=False)
                            messages.append(
                                Message(
                                    id=f"gem-obs-{msg_i}",
                                    timestamp=ts,
                                    author="agent",
                                    type="tool_result",
                                    content=str(content),
                                    metadata={"tool_name": fr.get("name")},
                                )
                            )
                    if text_bits:
                        msg_i += 1
                        messages.append(
                            Message(
                                id=f"gem-{msg_i}",
                                timestamp=ts,
                                author=author,
                                type=mtype if mtype == "message" else "message",
                                content="\n".join(text_bits),
                            )
                        )
                    last_activity = ts
                    continue

                content = _message_content(msg)
                if not content and mtype != "tool_result":
                    continue
                msg_i += 1
                messages.append(
                    Message(
                        id=f"gem-{msg_i}",
                        timestamp=ts,
                        author=author,
                        type=mtype,
                        content=content,
                    )
                )
                last_activity = ts

        meta = Metadata(
            session_id=ref.session_id,
            source_agent="gemini",
            source_session_path=str(ref.path),
            created_at=created_at,
            last_activity=last_activity,
            message_count=len(messages),
            cwd=cwd,
        )
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(files_modified=sorted(files_modified)),
        )


def _message_content(msg: dict[str, Any]) -> str:
    for key in ("content", "text", "message"):
        val = msg.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
    return ""


class GeminiInjector(Injector):
    agent_name = "gemini"

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        from handoff.formatters import to_markdown

        cwd = transcript.metadata.cwd or str(Path.cwd())
        # Stable-ish bucket so re-handoffs land in the same chats dir.
        import hashlib

        digest = hashlib.sha256(cwd.encode()).hexdigest()[:16]
        chats = self.home / "tmp" / digest / "chats"
        chats.mkdir(parents=True, exist_ok=True)
        session_id = str(uuid.uuid4())
        path = chats / f"{session_id}.json"

        pruned = strip_infra(deepcopy(transcript))
        catch_up = (
            f"[handoff] Context transferred from {transcript.metadata.source_agent}.\n\n"
            + to_markdown(pruned)
        )
        now = now_iso()
        doc = {
            "sessionId": session_id,
            "cwd": cwd,
            "startTime": now,
            "updatedAt": now,
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": catch_up,
                    "timestamp": now,
                }
            ],
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return path


def register() -> None:
    register_extractor("gemini", lambda home: GeminiExtractor(home))
    register_injector("gemini", lambda home: GeminiInjector(home))


register()
