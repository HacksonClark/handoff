"""Claude Code adapter — reads/writes ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``."""

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
    TaskItem,
    TaskState,
    now_iso,
    strip_infra,
)

CLAUDE_VERSION = "2.1.116"


def encode_project_dir(cwd: Path | str) -> str:
    """Claude Code encodes a cwd as the absolute path with '/' replaced by '-'.

    ``/Users/jackson/Desktop/handoff`` → ``-Users-jackson-Desktop-handoff``

    Rejects null bytes and path-separator attempts so the encoded name can't
    escape the ``projects/`` directory.
    """
    p = str(cwd)
    if "\x00" in p:
        raise ValueError("null byte in cwd")
    if not p.startswith("/"):
        p = str(Path(p).resolve())
    encoded = p.replace("/", "-")
    # Defence in depth: strip any residual path separators.
    if "/" in encoded or "\\" in encoded or encoded in ("", ".", ".."):
        raise ValueError(f"invalid project dir encoding: {encoded!r}")
    return encoded


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


class ClaudeExtractor(Extractor):
    agent_name = "claude"

    def _projects_root(self) -> Path:
        return self.home / "projects"

    def _session_files_for_cwd(self, cwd: Path) -> list[Path]:
        encoded = encode_project_dir(Path(cwd).resolve())
        project_dir = self._projects_root() / encoded
        if not project_dir.exists():
            return []
        return [p for p in project_dir.glob("*.jsonl") if not p.name.endswith(".wakatime")]

    def _all_session_files(self) -> list[Path]:
        root = self._projects_root()
        if not root.exists():
            return []
        files: list[Path] = []
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            files.extend(p for p in project_dir.glob("*.jsonl") if not p.name.endswith(".wakatime"))
        return files

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        files = self._session_files_for_cwd(cwd) if cwd else self._all_session_files()
        refs: list[SessionRef] = []
        for path in files:
            first_ts = ""
            last_ts = ""
            session_id = path.stem
            session_cwd: str | None = None
            count = 0
            for rec in _iter_jsonl(path):
                ts = rec.get("timestamp")
                if ts:
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts
                rcwd = rec.get("cwd")
                if rcwd and not session_cwd:
                    session_cwd = rcwd
                rsid = rec.get("sessionId")
                if rsid:
                    session_id = rsid
                if rec.get("type") in ("user", "assistant"):
                    count += 1
            refs.append(
                SessionRef(
                    session_id=session_id,
                    path=path,
                    cwd=session_cwd,
                    created_at=first_ts,
                    last_activity=last_ts,
                    message_count=count,
                )
            )
        refs.sort(key=lambda r: r.last_activity or "", reverse=True)
        return refs

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        messages: list[Message] = []
        session_id = ref.session_id
        created_at = ref.created_at or now_iso()
        last_activity = ref.last_activity or now_iso()
        cwd = ref.cwd or ""
        git_branch: str | None = None
        model: str | None = None
        files_modified: set[str] = set()
        task_state: TaskState | None = None
        away_summary: str | None = None

        for idx, rec in enumerate(_iter_jsonl(ref.path)):
            rtype = rec.get("type")
            ts = rec.get("timestamp") or now_iso()
            if rec.get("sessionId"):
                session_id = rec["sessionId"]
            if rec.get("cwd"):
                cwd = rec["cwd"]
            if rec.get("gitBranch"):
                git_branch = rec["gitBranch"]

            if rtype == "system" and rec.get("subtype") == "away_summary":
                content = rec.get("content")
                if isinstance(content, str) and content.strip():
                    away_summary = content.strip()
                continue

            if rtype == "attachment":
                attachment = rec.get("attachment") or {}
                if (
                    isinstance(attachment, dict)
                    and attachment.get("type") == "task_reminder"
                    and task_state is None
                ):
                    task_state = _task_state_from_task_reminder(attachment)
                continue

            if rtype == "user":
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    if content.strip():
                        messages.append(
                            Message(
                                id=rec.get("uuid") or f"claude-{idx}",
                                timestamp=ts,
                                author="user",
                                type="message",
                                content=content,
                            )
                        )
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            text = _tool_result_text(block.get("content"))
                            messages.append(
                                Message(
                                    id=rec.get("uuid") or f"claude-{idx}",
                                    timestamp=ts,
                                    author="system",
                                    type="tool_result",
                                    content=text,
                                    metadata={
                                        "call_id": block.get("tool_use_id", ""),
                                        "is_error": block.get("is_error", False),
                                    },
                                )
                            )

            elif rtype == "assistant":
                msg = rec.get("message") or {}
                model = msg.get("model") or model
                content = msg.get("content") or []
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text") or ""
                        if text.strip():
                            messages.append(
                                Message(
                                    id=rec.get("uuid") or f"claude-{idx}",
                                    timestamp=ts,
                                    author="agent",
                                    type="message",
                                    content=text,
                                    metadata={"model": model} if model else {},
                                )
                            )
                    elif btype == "thinking":
                        text = block.get("thinking") or ""
                        if text.strip():
                            messages.append(
                                Message(
                                    id=rec.get("uuid") or f"claude-{idx}",
                                    timestamp=ts,
                                    author="agent",
                                    type="reasoning",
                                    content=text,
                                )
                            )
                    elif btype == "tool_use":
                        name = block.get("name") or "tool"
                        input_obj = block.get("input") or {}
                        args_text = json.dumps(input_obj, ensure_ascii=False)
                        messages.append(
                            Message(
                                id=rec.get("uuid") or f"claude-{idx}",
                                timestamp=ts,
                                author="agent",
                                type="tool_call",
                                content=args_text,
                                metadata={
                                    "tool_name": name,
                                    "call_id": block.get("id", ""),
                                },
                            )
                        )
                        if name in ("Edit", "Write", "NotebookEdit"):
                            fp = input_obj.get("file_path")
                            if isinstance(fp, str):
                                files_modified.add(fp)
                        elif name == "TodoWrite" and isinstance(input_obj, dict):
                            task_state = _task_state_from_todo_write(input_obj)

            # attachment / file-history-snapshot / permission-mode / etc. → skip

            if not created_at:
                created_at = ts

        meta = Metadata(
            session_id=session_id,
            source_agent="claude",
            source_session_path=str(ref.path),
            created_at=created_at,
            last_activity=last_activity,
            message_count=len(messages),
            cwd=cwd,
            git_branch=git_branch,
            model=model,
        )
        if task_state is not None and away_summary:
            if task_state.explanation:
                task_state.explanation = f"{away_summary} | {task_state.explanation}"
            else:
                task_state.explanation = away_summary
        return CanonicalTranscript(
            metadata=meta,
            transcript=messages,
            artifacts=Artifacts(
                files_modified=sorted(files_modified),
                task_state=task_state,
            ),
        )


def _tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _task_state_from_todo_write(input_obj: dict[str, Any]) -> TaskState | None:
    raw_items = input_obj.get("todos") or input_obj.get("items") or input_obj.get("plan")
    if not isinstance(raw_items, list):
        return None

    items: list[TaskItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("step") or item.get("title")
        status = item.get("status")
        if not isinstance(content, str) or not isinstance(status, str):
            continue
        content = content.strip()
        status = status.strip()
        if not content or not status:
            continue
        items.append(TaskItem(content=content, status=status))

    if not items:
        return None

    return TaskState(items=items, source="TodoWrite")


def _task_state_from_task_reminder(attachment: dict[str, Any]) -> TaskState | None:
    raw_items = attachment.get("content")
    if not isinstance(raw_items, list):
        return None

    items: list[TaskItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        content = item.get("subject") or item.get("activeForm") or item.get("description")
        status = item.get("status")
        if not isinstance(content, str) or not isinstance(status, str):
            continue
        content = content.strip()
        status = status.strip()
        if not content or not status:
            continue
        items.append(TaskItem(content=content, status=status))

    if not items:
        return None

    explanation = None
    item_count = attachment.get("itemCount")
    if isinstance(item_count, int):
        explanation = f"{item_count} tasks captured from Claude task reminder"

    return TaskState(items=items, source="task_reminder", explanation=explanation)


class ClaudeInjector(Injector):
    agent_name = "claude"

    def inject(self, transcript: CanonicalTranscript) -> Path:
        from copy import deepcopy

        from handoff.formatters import to_markdown

        cwd = transcript.metadata.cwd or str(Path.cwd())
        encoded = encode_project_dir(cwd)
        project_dir = self.home / "projects" / encoded
        project_dir.mkdir(parents=True, exist_ok=True)

        session_id = str(uuid.uuid4())
        path = project_dir / f"{session_id}.jsonl"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        pruned = strip_infra(deepcopy(transcript))
        catch_up = (
            f"[handoff] Context transferred from {transcript.metadata.source_agent}. "
            "The transcript from the previous session follows. "
            "Read it to understand what was done, then continue from where it left off.\n\n"
            + to_markdown(pruned)
        )

        first_uuid = str(uuid.uuid4())
        records: list[dict[str, Any]] = [
            {
                "type": "permission-mode",
                "permissionMode": "auto",
                "sessionId": session_id,
            },
            {
                "parentUuid": None,
                "isSidechain": False,
                "promptId": str(uuid.uuid4()),
                "type": "user",
                "message": {"role": "user", "content": catch_up},
                "uuid": first_uuid,
                "timestamp": now,
                "permissionMode": "auto",
                "userType": "external",
                "entrypoint": "cli",
                "cwd": cwd,
                "sessionId": session_id,
                "version": CLAUDE_VERSION,
                "gitBranch": transcript.metadata.git_branch or "",
            },
        ]

        import os

        # Create with 0600 so other users on the box can't read transferred
        # transcripts even if the parent dir is loose.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path


def register() -> None:
    """Entry-point hook. Safe to call multiple times."""
    register_extractor("claude", lambda home: ClaudeExtractor(home))
    register_injector("claude", lambda home: ClaudeInjector(home))


register()
