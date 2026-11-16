"""ATIF (Agent Trajectory Interchange Format) bridge.

ATIF is Harbor/Terminal-Bench's standard JSON format for agent trajectories
(messages, reasoning, tool calls, observations). See:

    https://docs.harborframework.com/agents/atif
    https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md

Handoff keeps :class:`~handoff.canonical.CanonicalTranscript` as its internal
working model (flat message list, handoff-specific metadata). This module
converts both ways so transcripts can be:

* exported with ``--format atif``
* stored/loaded via the ``atif`` agent adapter
* exchanged with Harbor trials and other ATIF producers/consumers

We intentionally implement a *compatible subset* of ATIF-v1.5+ (steps with
tool_calls + observations, agent metadata, extra) without depending on the
full ``harbor`` package.
"""

from __future__ import annotations

import json
from typing import Any

from handoff.canonical import (
    Artifacts,
    Author,
    CanonicalTranscript,
    Message,
    MessageType,
    Metadata,
    now_iso,
)

ATIF_SCHEMA_VERSION = "ATIF-v1.5"
HANDOFF_VERSION = "0.2.0"

# Map handoff authors → ATIF step sources (and back).
_AUTHOR_TO_SOURCE: dict[str, str] = {
    "user": "user",
    "agent": "agent",
    "system": "system",
    "developer": "system",
}
_SOURCE_TO_AUTHOR: dict[str, Author] = {
    "user": "user",
    "agent": "agent",
    "system": "system",
}


def to_atif(transcript: CanonicalTranscript) -> dict[str, Any]:
    """Convert a canonical transcript into an ATIF trajectory dict."""
    meta = transcript.metadata
    steps = _messages_to_steps(transcript.transcript)

    extra: dict[str, Any] = {
        "handoff": {
            "source_agent": meta.source_agent,
            "source_session_path": meta.source_session_path,
            "cwd": meta.cwd,
            "created_at": meta.created_at,
            "last_activity": meta.last_activity,
            "message_count": meta.message_count,
        }
    }
    if meta.git_branch:
        extra["handoff"]["git_branch"] = meta.git_branch
    if transcript.artifacts.files_modified:
        extra["handoff"]["files_modified"] = list(transcript.artifacts.files_modified)
    if transcript.artifacts.task_state is not None:
        ts = transcript.artifacts.task_state
        extra["handoff"]["task_state"] = {
            "source": ts.source,
            "explanation": ts.explanation,
            "items": [{"content": i.content, "status": i.status} for i in ts.items],
        }

    agent: dict[str, Any] = {
        "name": meta.source_agent or "handoff",
        "version": HANDOFF_VERSION,
    }
    if meta.model:
        agent["model_name"] = meta.model

    traj: dict[str, Any] = {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": meta.session_id,
        "agent": agent,
        "steps": steps,
        "extra": extra,
    }
    if not steps:
        # ATIF requires ≥1 step; emit a placeholder so the document validates.
        traj["steps"] = [
            {
                "step_id": 1,
                "timestamp": meta.last_activity or now_iso(),
                "source": "system",
                "message": "(empty handoff transcript)",
            }
        ]
    return traj


def from_atif(
    data: dict[str, Any],
    *,
    source_path: str = "",
    default_cwd: str = "",
) -> CanonicalTranscript:
    """Parse an ATIF trajectory dict into a :class:`CanonicalTranscript`."""
    if not isinstance(data, dict):
        raise TypeError("ATIF trajectory must be a JSON object")

    schema = data.get("schema_version", "")
    if schema and not str(schema).startswith("ATIF-"):
        raise ValueError(f"unsupported schema_version: {schema!r}")

    agent = data.get("agent") or {}
    if not isinstance(agent, dict):
        agent = {}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    handoff_extra = extra.get("handoff") if isinstance(extra.get("handoff"), dict) else {}

    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError("ATIF 'steps' must be an array")

    messages = _steps_to_messages(steps)
    created_at = ""
    last_activity = ""
    for msg in messages:
        if msg.timestamp:
            if not created_at:
                created_at = msg.timestamp
            last_activity = msg.timestamp
    if not created_at:
        created_at = now_iso()
    if not last_activity:
        last_activity = created_at

    cwd = (
        handoff_extra.get("cwd")
        or (extra.get("cwd") if isinstance(extra.get("cwd"), str) else None)
        or default_cwd
        or ""
    )
    session_id = data.get("session_id") or data.get("trajectory_id") or "atif-unknown"
    source_agent = handoff_extra.get("source_agent") or agent.get("name") or "atif"

    from handoff.canonical import TaskItem, TaskState

    artifacts = Artifacts()
    files = handoff_extra.get("files_modified")
    if isinstance(files, list):
        artifacts.files_modified = [str(f) for f in files]
    raw_ts = handoff_extra.get("task_state")
    if isinstance(raw_ts, dict):
        items: list[TaskItem] = []
        for item in raw_ts.get("items") or []:
            if isinstance(item, dict) and item.get("content") and item.get("status"):
                items.append(TaskItem(content=str(item["content"]), status=str(item["status"])))
        if items:
            artifacts.task_state = TaskState(
                items=items,
                source=raw_ts.get("source"),
                explanation=raw_ts.get("explanation"),
            )

    meta = Metadata(
        session_id=str(session_id),
        source_agent=str(source_agent),
        source_session_path=source_path or handoff_extra.get("source_session_path", ""),
        created_at=str(handoff_extra.get("created_at") or created_at),
        last_activity=str(handoff_extra.get("last_activity") or last_activity),
        message_count=len(messages),
        cwd=str(cwd),
        git_branch=handoff_extra.get("git_branch"),
        model=agent.get("model_name"),
    )
    return CanonicalTranscript(metadata=meta, transcript=messages, artifacts=artifacts)


def to_atif_json(transcript: CanonicalTranscript, *, indent: int = 2) -> str:
    """Serialize a transcript as pretty-printed ATIF JSON."""
    return json.dumps(to_atif(transcript), indent=indent, ensure_ascii=False) + "\n"


def load_atif_file(path: Any) -> CanonicalTranscript:
    """Load an ATIF trajectory from a ``.json`` file path."""
    from pathlib import Path

    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return from_atif(data, source_path=str(p))


def validate_atif(data: dict[str, Any]) -> list[str]:
    """Return a list of validation problems (empty = ok).

    Checks the subset handoff cares about: schema prefix, sequential step_ids,
    and observation ``source_call_id`` references.
    """
    errors: list[str] = []
    schema = data.get("schema_version")
    if not isinstance(schema, str) or not schema.startswith("ATIF-"):
        errors.append(f"schema_version must start with 'ATIF-', got {schema!r}")

    agent = data.get("agent")
    if not isinstance(agent, dict) or not agent.get("name") or not agent.get("version"):
        errors.append("agent.name and agent.version are required")

    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty array")
        return errors

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps[{i}] must be an object")
            continue
        expected = i + 1
        if step.get("step_id") != expected:
            errors.append(f"steps[{i}].step_id: expected {expected}, got {step.get('step_id')!r}")
        source = step.get("source")
        if source not in ("system", "user", "agent"):
            errors.append(f"steps[{i}].source: invalid {source!r}")
        if "message" not in step:
            errors.append(f"steps[{i}].message is required")

        tool_ids: set[str] = set()
        for tc in step.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("tool_call_id"):
                tool_ids.add(str(tc["tool_call_id"]))
        obs = step.get("observation")
        if isinstance(obs, dict):
            for j, result in enumerate(obs.get("results") or []):
                if not isinstance(result, dict):
                    continue
                sid = result.get("source_call_id")
                if sid is not None and str(sid) not in tool_ids:
                    errors.append(
                        f"steps[{i}].observation.results[{j}].source_call_id "
                        f"{sid!r} not in tool_calls"
                    )
    return errors


# ---------------------------------------------------------------------------
# Internal: flatten / regroup messages ↔ steps
# ---------------------------------------------------------------------------


def _messages_to_steps(messages: list[Message]) -> list[dict[str, Any]]:
    """Group a flat handoff message list into ATIF steps.

    Strategy:
    - user/system/developer ``message`` → own step
    - agent ``message`` / ``reasoning`` opens an agent step; subsequent
      ``tool_call`` / ``tool_result`` attach to that step until the next
      non-tool message.
    """
    steps: list[dict[str, Any]] = []
    open_agent: dict[str, Any] | None = None

    def _flush_agent() -> None:
        nonlocal open_agent
        if open_agent is not None:
            steps.append(open_agent)
            open_agent = None

    for msg in messages:
        if msg.type == "message" and msg.author in ("user", "system", "developer"):
            _flush_agent()
            steps.append(
                {
                    "step_id": 0,  # renumbered below
                    "timestamp": msg.timestamp or None,
                    "source": _AUTHOR_TO_SOURCE.get(msg.author, "user"),
                    "message": msg.content or "",
                    **({"extra": {"handoff_id": msg.id}} if msg.id else {}),
                }
            )
            continue

        if msg.type in ("message", "reasoning") and msg.author == "agent":
            _flush_agent()
            open_agent = {
                "step_id": 0,
                "timestamp": msg.timestamp or None,
                "source": "agent",
                "message": msg.content if msg.type == "message" else "",
                **(
                    {"reasoning_content": msg.content}
                    if msg.type == "reasoning" and msg.content
                    else {}
                ),
                **({"extra": {"handoff_id": msg.id}} if msg.id else {}),
            }
            continue

        if msg.type == "tool_call":
            if open_agent is None:
                open_agent = {
                    "step_id": 0,
                    "timestamp": msg.timestamp or None,
                    "source": "agent",
                    "message": "",
                }
            call_id = str(msg.metadata.get("tool_call_id") or msg.id or f"call-{len(steps)}")
            name = str(msg.metadata.get("tool_name") or "tool")
            args = msg.metadata.get("arguments")
            if not isinstance(args, dict):
                # Content is often a JSON dump of args; try to parse.
                try:
                    parsed = json.loads(msg.content) if msg.content else {}
                    args = parsed if isinstance(parsed, dict) else {"_raw": msg.content}
                except (json.JSONDecodeError, TypeError):
                    args = {"_raw": msg.content or ""}
            open_agent.setdefault("tool_calls", []).append(
                {
                    "tool_call_id": call_id,
                    "function_name": name,
                    "arguments": args,
                }
            )
            continue

        if msg.type == "tool_result":
            if open_agent is None:
                open_agent = {
                    "step_id": 0,
                    "timestamp": msg.timestamp or None,
                    "source": "agent",
                    "message": "",
                }
            call_id = msg.metadata.get("tool_call_id") or msg.metadata.get("source_call_id")
            # Prefer correlating with the most recent unmatched tool_call.
            if call_id is None:
                existing = {
                    r.get("source_call_id")
                    for r in (open_agent.get("observation") or {}).get("results", [])
                }
                for tc in open_agent.get("tool_calls") or []:
                    tid = tc.get("tool_call_id")
                    if tid and tid not in existing:
                        call_id = tid
                        break
            result: dict[str, Any] = {"content": msg.content or ""}
            if call_id is not None:
                result["source_call_id"] = str(call_id)
            obs = open_agent.setdefault("observation", {"results": []})
            obs["results"].append(result)
            continue

        # approval / unknown → treat as system note
        _flush_agent()
        steps.append(
            {
                "step_id": 0,
                "timestamp": msg.timestamp or None,
                "source": "system",
                "message": msg.content or "",
                "extra": {"handoff_type": msg.type, "handoff_author": msg.author},
            }
        )

    _flush_agent()

    # Renumber + drop None timestamps / empty extras for cleaner JSON.
    cleaned: list[dict[str, Any]] = []
    for i, step in enumerate(steps, start=1):
        step["step_id"] = i
        if step.get("timestamp") is None:
            step.pop("timestamp", None)
        extra = step.get("extra")
        if extra == {}:
            step.pop("extra", None)
        cleaned.append(step)
    return cleaned


def _steps_to_messages(steps: list[Any]) -> list[Message]:
    messages: list[Message] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        source = str(step.get("source") or "agent")
        author: Author = _SOURCE_TO_AUTHOR.get(source, "agent")
        ts = str(step.get("timestamp") or now_iso())
        step_id = step.get("step_id", i + 1)
        base_id = f"step-{step_id}"

        message_field = step.get("message", "")
        text = _message_to_text(message_field)

        reasoning = step.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            messages.append(
                Message(
                    id=f"{base_id}-reasoning",
                    timestamp=ts,
                    author="agent",
                    type="reasoning",
                    content=reasoning,
                )
            )

        if text or not step.get("tool_calls"):
            # Emit a message step even when empty if there are no tool calls,
            # so user/system turns are preserved; for agent+tools an empty
            # message is skipped to avoid noise.
            if text or source in ("user", "system"):
                msg_type: MessageType = "message"
                messages.append(
                    Message(
                        id=base_id,
                        timestamp=ts,
                        author=author if source != "agent" else "agent",
                        type=msg_type,
                        content=text,
                    )
                )

        for j, tc in enumerate(step.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            call_id = str(tc.get("tool_call_id") or f"{base_id}-call-{j}")
            name = str(tc.get("function_name") or "tool")
            args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else {}
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

        obs = step.get("observation")
        if isinstance(obs, dict):
            for j, result in enumerate(obs.get("results") or []):
                if not isinstance(result, dict):
                    continue
                content = _message_to_text(result.get("content", ""))
                call_id = result.get("source_call_id")
                messages.append(
                    Message(
                        id=f"{base_id}-obs-{j}",
                        timestamp=ts,
                        author="agent",
                        type="tool_result",
                        content=content,
                        metadata={
                            "tool_call_id": call_id,
                            "source_call_id": call_id,
                        },
                    )
                )
    return messages


def _message_to_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for part in message:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
                elif "text" in part:
                    parts.append(str(part["text"]))
        return "\n".join(parts)
    return str(message)
