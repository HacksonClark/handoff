from handoff.canonical import (
    CanonicalTranscript,
    Message,
    Metadata,
    is_infra_message,
    now_iso,
    strip_infra,
)


def _msg(author, typ, content):
    return Message(id="m", timestamp=now_iso(), author=author, type=typ, content=content)


def _t(messages):
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


def test_is_infra_recognises_permissions_block():
    m = _msg("user", "message", "<permissions instructions>\nhi\n</permissions instructions>")
    assert is_infra_message(m)


def test_is_infra_recognises_agents_md():
    m = _msg("user", "message", "# AGENTS.md instructions for /Users/x\n...")
    assert is_infra_message(m)


def test_is_infra_treats_developer_messages_as_infra():
    m = _msg("developer", "message", "anything goes here")
    assert is_infra_message(m)


def test_is_infra_allows_normal_user_messages():
    m = _msg("user", "message", "Please fix the auth bug")
    assert not is_infra_message(m)


def test_is_infra_keeps_tool_calls_even_if_agent_is_system():
    m = _msg("system", "tool_result", "exit 0")
    # tool_result is not author-typed as developer, content is plain; kept.
    assert not is_infra_message(m)


def test_strip_infra_filters_and_updates_count():
    messages = [
        _msg("user", "message", "<permissions instructions>..."),
        _msg("user", "message", "Real ask"),
        _msg("agent", "message", "Real reply"),
        _msg("developer", "message", "boilerplate"),
    ]
    t = _t(messages)
    strip_infra(t)
    assert [m.content for m in t.transcript] == ["Real ask", "Real reply"]
    assert t.metadata.message_count == 2
