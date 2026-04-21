"""Codex adapter — reads and writes ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``."""

from __future__ import annotations

import json
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


def _content_to_text(content: list[dict[str, Any]] | str | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if "text" in item:
            parts.append(str(item["text"]))
    return "\n".join(parts)


class CodexExtractor(Extractor):
    agent_name = "codex"

    def _sessions_root(self) -> Path:
        return self.home / "sessions"

    def _rollout_files(self) -> Iterator[Path]:
        root = self._sessions_root()
        if not root.exists():
            return
        yield from sorted(root.glob("*/*/*/rollout-*.jsonl"), reverse=True)

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        """Scan rollouts in a single pass.

        Optimisation: we only need the first record (``session_meta``) to
        filter by ``cwd``. For files that don't match we stop after that
        record. For matching files we keep iterating to count messages.
        """
        refs: list[SessionRef] = []
        cwd_str = str(Path(cwd).resolve()) if cwd else None
        for path in self._rollout_files():
            session_cwd: str | None = None
            created = ""
            session_id = path.stem.split("-", 1)[-1]
            count = 0
            matched = cwd_str is None

            for i, record in enumerate(_iter_jsonl(path)):
                if i == 0:
                    if record.get("type") != "session_meta":
                        break  # malformed rollout; skip entirely
                    payload = record.get("payload") or {}
                    session_cwd = payload.get("cwd")
                    created = payload.get("timestamp") or record.get("timestamp") or ""
                    session_id = payload.get("id") or session_id
                    if cwd_str and session_cwd != cwd_str:
                        break  # wrong project; no need to count messages
                    matched = True
                    continue
                count += 1

            if not matched:
                continue

            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                last = mtime.isoformat().replace("+00:00", "Z")
            except OSError:
                last = created

            refs.append(
                SessionRef(
                    session_id=session_id,
                    path=path,
                    cwd=session_cwd,
                    created_at=created,
                    last_activity=last,
                    message_count=count,
                )
            )
        return refs

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        messages: list[Message] = []
        session_id = ref.session_id
        created_at = ref.created_at
        last_activity = ref.last_activity
        cwd = ref.cwd or ""
        git_branch: str | None = None
        model: str | None = None
        files_modified: set[str] = set()

        for idx, record in enumerate(_iter_jsonl(ref.path)):
            rtype = record.get("type")
            ts = record.get("timestamp") or now_iso()
            payload = record.get("payload", {}) or {}

            if rtype == "session_meta":
                session_id = payload.get("id") or session_id
                created_at = payload.get("timestamp") or created_at
                cwd = payload.get("cwd") or cwd
                git = payload.get("git") or {}
                if isinstance(git, dict):
                    git_branch = git.get("branch") or git_branch
                continue

            if rtype == "turn_context":
                model = payload.get("model") or model
                continue

            if rtype != "response_item":
                continue

            ptype = payload.get("type")
            mid = payload.get("id") or f"codex-{idx}"

            if ptype == "message":
                role = payload.get("role") or "user"
                if role == "assistant":
                    author = "agent"
                elif role == "user":
                    author = "user"
                elif role == "developer":
                    author = "developer"
                else:
                    author = "system"
                text = _content_to_text(payload.get("content"))
                if not text.strip():
                    continue
                messages.append(
                    Message(
                        id=mid,
                        timestamp=ts,
                        author=author,  # type: ignore[arg-type]
                        type="message",
                        content=text,
                    )
                )

            elif ptype == "reasoning":
                text = _content_to_text(payload.get("content") or payload.get("summary"))
                if not text.strip():
                    continue
                messages.append(
                    Message(
                        id=mid,
                        timestamp=ts,
                        author="agent",
                        type="reasoning",
                        content=text,
                    )
                )

            elif ptype in ("function_call", "custom_tool_call"):
                name = payload.get("name") or "tool"
                args = payload.get("arguments") or payload.get("input") or ""
                if isinstance(args, (dict, list)):
                    args_text = json.dumps(args, ensure_ascii=False)
                else:
                    args_text = str(args)
                call_id = payload.get("call_id") or mid
                messages.append(
                    Message(
                        id=mid,
                        timestamp=ts,
                        author="agent",
                        type="tool_call",
                        content=args_text,
                        metadata={"tool_name": name, "call_id": call_id},
                    )
                )
                if name == "apply_patch":
                    for line in args_text.splitlines():
                        line = line.strip()
                        for marker in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
                            if line.startswith(marker):
                                files_modified.add(line[len(marker) :].strip())

            elif ptype in ("function_call_output", "custom_tool_call_output"):
                output = payload.get("output")
                if isinstance(output, dict):
                    output_text = output.get("content") or json.dumps(output, ensure_ascii=False)
                else:
                    output_text = str(output or "")
                call_id = payload.get("call_id") or mid
                messages.append(
                    Message(
                        id=mid,
                        timestamp=ts,
                        author="system",
                        type="tool_result",
                        content=str(output_text),
                        metadata={"call_id": call_id},
                    )
                )

        meta = Metadata(
            session_id=session_id,
            source_agent="codex",
            source_session_path=str(ref.path),
            created_at=created_at or now_iso(),
            last_activity=last_activity or now_iso(),
            message_count=len(messages),
            cwd=cwd,
            git_branch=git_branch,
            model=model,
        )
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(files_modified=sorted(files_modified)),
        )


class CodexInjector(Injector):
    agent_name = "codex"

    def _new_session_path(self, session_id: str, ts: datetime) -> Path:
        y = f"{ts.year:04d}"
        m = f"{ts.month:02d}"
        d = f"{ts.day:02d}"
        stamp = ts.strftime("%Y-%m-%dT%H-%M-%S")
        name = f"rollout-{stamp}-{session_id}.jsonl"
        return self.home / "sessions" / y / m / d / name

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        transcript = strip_infra(deepcopy(transcript))

        ts = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())
        path = self._new_session_path(session_id, ts)
        path.parent.mkdir(parents=True, exist_ok=True)

        ts_iso = ts.isoformat().replace("+00:00", "Z")
        lines: list[dict[str, Any]] = []

        # session_meta
        lines.append(
            {
                "timestamp": ts_iso,
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": ts_iso,
                    "cwd": transcript.metadata.cwd,
                    "originator": "handoff_cli",
                    "cli_version": "0.1.0",
                    "source": "handoff",
                    "instructions": (
                        "This session was transferred from "
                        f"{transcript.metadata.source_agent} via `handoff`. "
                        "The conversation up to this point has been reconstructed below."
                    ),
                },
            }
        )

        # Prepend a developer note summarizing the handoff
        summary = self._summary(transcript)
        lines.append(
            {
                "timestamp": ts_iso,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": summary}],
                },
            }
        )

        # Replay canonical transcript
        for msg in transcript.transcript:
            lines.extend(self._message_to_records(msg, ts_iso))

        import os

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in lines:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    @staticmethod
    def _summary(t: CanonicalTranscript) -> str:
        return (
            f"[handoff] Context transferred from {t.metadata.source_agent}.\n"
            f"Original session: {t.metadata.session_id}\n"
            f"Messages: {t.metadata.message_count}\n"
            f"Project: {t.metadata.cwd}\n"
            "Resume where the previous agent left off."
        )

    @staticmethod
    def _message_to_records(msg: Message, ts: str) -> list[dict[str, Any]]:
        if msg.type == "message":
            role_map = {
                "user": "user",
                "agent": "assistant",
                "developer": "developer",
                "system": "developer",
            }
            role = role_map.get(msg.author, "user")
            key = "output_text" if role == "assistant" else "input_text"
            return [
                {
                    "timestamp": msg.timestamp or ts,
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"type": key, "text": msg.content}],
                    },
                }
            ]
        if msg.type == "reasoning":
            return [
                {
                    "timestamp": msg.timestamp or ts,
                    "type": "response_item",
                    "payload": {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": msg.content}],
                    },
                }
            ]
        if msg.type == "tool_call":
            name = msg.metadata.get("tool_name", "tool")
            call_id = msg.metadata.get("call_id", msg.id)
            return [
                {
                    "timestamp": msg.timestamp or ts,
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": name,
                        "arguments": msg.content,
                        "call_id": call_id,
                    },
                }
            ]
        if msg.type == "tool_result":
            call_id = msg.metadata.get("call_id", msg.id)
            return [
                {
                    "timestamp": msg.timestamp or ts,
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": msg.content,
                    },
                }
            ]
        return []


def register() -> None:
    """Entry-point hook. Safe to call multiple times."""
    register_extractor("codex", lambda home: CodexExtractor(home))
    register_injector("codex", lambda home: CodexInjector(home))


register()
