from __future__ import annotations

from handoff.agents.base import (
    get_extractor,
    get_injector,
    known_agents,
    load_plugins,
)


def test_builtins_are_registered():
    agents = known_agents()
    assert "claude" in agents
    assert "codex" in agents
    assert "opencode" in agents


def test_load_plugins_is_idempotent():
    first = load_plugins()
    second = load_plugins()
    assert second == []
    assert isinstance(first, list)


def test_get_extractor_returns_instance(tmp_path):
    ex = get_extractor("codex", tmp_path)
    assert ex is not None
    assert ex.home == tmp_path


def test_get_extractor_unknown_raises(tmp_path):
    try:
        get_extractor("bogus-agent", tmp_path)
    except ValueError as exc:
        assert "bogus-agent" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_get_injector_unknown_raises(tmp_path):
    try:
        get_injector("bogus-agent", tmp_path)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
