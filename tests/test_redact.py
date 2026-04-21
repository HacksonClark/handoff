from handoff.canonical import CanonicalTranscript, Message, Metadata, now_iso
from handoff.redact import REDACTED, Redactor


def _msg(text: str) -> Message:
    return Message(id="m1", timestamp=now_iso(), author="user", type="message", content=text)


def _transcript(messages: list[Message]) -> CanonicalTranscript:
    return CanonicalTranscript(
        metadata=Metadata(
            session_id="s",
            source_agent="codex",
            source_session_path="/tmp/x",
            created_at=now_iso(),
            last_activity=now_iso(),
            message_count=len(messages),
            cwd="/tmp",
        ),
        transcript=messages,
    )


def test_redacts_openai_key():
    r = Redactor()
    out = r.redact_text("export OPENAI_API_KEY=sk-abcdef1234567890abcdef")
    assert "sk-abcdef" not in out
    assert REDACTED in out


def test_redacts_anthropic_key():
    r = Redactor()
    out = r.redact_text("ANTHROPIC_API_KEY=sk-ant-abcdef1234567890abcdef")
    assert "sk-ant-" not in out
    assert REDACTED in out


def test_redacts_bearer_token():
    r = Redactor()
    out = r.redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef")
    assert "eyJhbGci" not in out
    assert REDACTED in out


def test_redacts_github_token():
    r = Redactor()
    out = r.redact_text("ghp_abcdef1234567890abcdefghij")
    assert "ghp_abcdef" not in out


def test_disabled_redactor_is_pass_through():
    r = Redactor(enabled=False)
    raw = "OPENAI_API_KEY=sk-abcdef1234567890abcdef"
    assert r.redact_text(raw) == raw


def test_redact_transcript_walks_messages():
    t = _transcript(
        [
            _msg("hello"),
            _msg("my key is sk-abcdef1234567890abcdef"),
        ]
    )
    Redactor().redact_transcript(t)
    assert t.transcript[0].content == "hello"
    assert "sk-abcdef" not in t.transcript[1].content
