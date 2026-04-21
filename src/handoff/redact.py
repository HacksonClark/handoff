"""Secret redaction. Applied to text content in canonical transcripts.

Redaction is **best-effort**. The default patterns cover common API-key shapes
(OpenAI, Anthropic, GitHub, AWS, Bearer tokens, PEM blocks) but cannot catch
every custom secret format or tokens that are contextually sensitive. Users
should review redacted output before sharing it externally.
"""

from __future__ import annotations

import re
from dataclasses import replace

from handoff.canonical import CanonicalTranscript, Message

DEFAULT_PATTERNS: tuple[str, ...] = (
    r"OPENAI_API_KEY\s*=\s*\S+",
    r"ANTHROPIC_API_KEY\s*=\s*\S+",
    r"GITHUB_TOKEN\s*=\s*\S+",
    r"AWS_SECRET_ACCESS_KEY\s*=\s*\S+",
    r"AWS_ACCESS_KEY_ID\s*=\s*\S+",
    r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"ghs_[A-Za-z0-9]{20,}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----",
)

REDACTED = "[REDACTED]"


class Redactor:
    def __init__(self, patterns: list[str] | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        pats = patterns if patterns is not None else list(DEFAULT_PATTERNS)
        self._regexes = [re.compile(p) for p in pats]

    def redact_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        for rx in self._regexes:
            text = rx.sub(REDACTED, text)
        return text

    def redact_message(self, msg: Message) -> Message:
        if not self.enabled:
            return msg
        return replace(msg, content=self.redact_text(msg.content))

    def redact_transcript(self, t: CanonicalTranscript) -> CanonicalTranscript:
        if not self.enabled:
            return t
        t.transcript = [self.redact_message(m) for m in t.transcript]
        return t
