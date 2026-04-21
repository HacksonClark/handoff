"""Canonical transcript model used internally by handoff.

All extractors produce a ``CanonicalTranscript``. All injectors consume one.
The shape stays intentionally simple — anything richer lives in per-message
``metadata`` so we don't grow the schema every time an agent adds a feature.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Author = Literal["user", "agent", "system", "developer"]
MessageType = Literal["message", "tool_call", "tool_result", "reasoning", "approval"]


@dataclass
class Message:
    id: str
    timestamp: str  # ISO 8601
    author: Author
    type: MessageType
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileDiff:
    file: str
    before: str = ""
    after: str = ""


@dataclass
class Artifacts:
    files_modified: list[str] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)


@dataclass
class Metadata:
    session_id: str
    source_agent: str
    source_session_path: str
    created_at: str
    last_activity: str
    message_count: int
    cwd: str
    git_branch: str | None = None
    model: str | None = None


@dataclass
class CanonicalTranscript:
    metadata: Metadata
    transcript: list[Message] = field(default_factory=list)
    artifacts: Artifacts = field(default_factory=Artifacts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def duration_seconds(self) -> float:
        try:
            start = datetime.fromisoformat(self.metadata.created_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.metadata.last_activity.replace("Z", "+00:00"))
            return max(0.0, (end - start).total_seconds())
        except (ValueError, AttributeError):
            return 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Substrings that indicate a message is agent-internal infra (env context,
# tool-permission blobs, collaboration-mode directives) rather than real
# conversation. Used to prune injected context so the receiving agent isn't
# fed boilerplate from a different harness.
_INFRA_MARKERS: tuple[str, ...] = (
    "<environment_context>",
    "<permissions instructions>",
    "<collaboration_mode>",
    "# AGENTS.md instructions for",
    "<system-reminder>",
    "# claudeMd",
)


def is_infra_message(msg: Message) -> bool:
    """True if this message looks like harness-injected boilerplate."""
    if msg.author in ("developer", "system") and msg.type == "message":
        return True
    text = msg.content or ""
    return any(marker in text for marker in _INFRA_MARKERS)


def strip_infra(t: CanonicalTranscript) -> CanonicalTranscript:
    """Return the transcript with infra messages removed, in place."""
    t.transcript = [m for m in t.transcript if not is_infra_message(m)]
    t.metadata.message_count = len(t.transcript)
    return t
