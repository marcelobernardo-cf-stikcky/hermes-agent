"""Tests for the push/wake capability split (this card).

``async_delivery_supported()`` used to conflate two distinct channel
capabilities and let the first negate the second:

  1. **push** — send text to the user AFTER the turn ends (an already-open
     channel's ``send()``). The API server is push=False: ``send()`` is a
     no-op stub (see ``gateway/platforms/api_server.py``).
  2. **wake** — resume the REAL session with a fresh turn later. The API
     server IS wake-capable: it self-posts ``/v1/chat/completions`` with the
     raw ``X-Hermes-Session-Id`` header (``gateway/wake.py::deliver_wake``).

``tools/terminal_tool.py`` used to consult only the aggregated (push)
capability and refuse ``notify_on_complete``/``watch_patterns`` on ANY
push=False session — including api_server, where the downstream consumer
(``gateway/run.py::_inject_watch_notification``) already implements the wake
delivery for that exact surface. The fix: refuse the promise only when
NEITHER capability is available.

These are behavior/invariant tests — they assert the RELATIONSHIP between the
two capability bits and the tool's resulting behavior, not a frozen snapshot
of current output text.
"""

import json

import pytest

from gateway.session_context import (
    async_delivery_supported,
    clear_session_vars,
    set_session_vars,
    wake_delivery_supported,
)


# ---------------------------------------------------------------------------
# Capability helpers — session_context.py
# ---------------------------------------------------------------------------

class TestWakeDeliverySupportedHelper:
    def test_default_unbound_is_supported(self):
        """Unaware paths (CLI, unbound contextvar) default wake-capable,
        mirroring async_delivery_supported()'s default."""
        assert wake_delivery_supported() is True

    def test_mirrors_async_delivery_when_not_passed_explicitly(self):
        """A caller that only passes async_delivery=False (the pre-split
        call shape, e.g. cron / declare_stateless_channel-equivalent usage)
        must NOT silently become wake-capable — wake_delivery mirrors
        async_delivery when the caller never opted into the split."""
        tokens = set_session_vars(
            platform="", chat_id="", session_key="", async_delivery=False,
        )
        try:
            assert async_delivery_supported() is False
            assert wake_delivery_supported() is False
        finally:
            clear_session_vars(tokens)

    def test_explicit_split_push_false_wake_true(self):
        """A caller that explicitly declares the split (api_server's shape)
        gets independent bits."""
        tokens = set_session_vars(
            platform="api_server", chat_id="s1", session_key="s1",
            async_delivery=False, wake_delivery=True,
        )
        try:
            assert async_delivery_supported() is False
            assert wake_delivery_supported() is True
        finally:
            clear_session_vars(tokens)

    def test_kanban_worker_env_forces_both_false_even_if_wake_true_passed(
        self, monkeypatch
    ):
        """HERMES_KANBAN_TASK is a hard override on BOTH helpers: a one-shot
        dispatcher-spawned worker has no gateway drain loop behind it at all,
        so neither capability may report True no matter what the session
        vars claim."""
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_probe")
        tokens = set_session_vars(
            platform="api_server", chat_id="s1", session_key="s1",
            async_delivery=False, wake_delivery=True,
        )
        try:
            assert async_delivery_supported() is False
            assert wake_delivery_supported() is False
        finally:
            clear_session_vars(tokens)


# ---------------------------------------------------------------------------
# Adapter capability flags
# ---------------------------------------------------------------------------

class TestAdapterCapabilityFlags:
    def test_api_server_is_push_false_wake_true(self):
        from gateway.platforms.api_server import APIServerAdapter

        assert APIServerAdapter.supports_async_delivery is False
        assert APIServerAdapter.supports_wake_delivery is True

    def test_base_adapter_defaults_both_true(self):
        """Push-capable adapters (Telegram, Discord, ...) that never
        override either flag stay capable of both — every pre-split adapter
        keeps its existing behavior."""
        from gateway.platforms.base import BasePlatformAdapter

        assert BasePlatformAdapter.supports_async_delivery is True
        assert BasePlatformAdapter.supports_wake_delivery is True

    def test_api_server_bind_chokepoint_keeps_wake_true(self):
        """The api_server's single session-bind chokepoint
        (_bind_api_server_session) hardwires push=False (#10760) but must NOT
        also silently zero out wake — the adapter is wake-capable via
        gateway/wake.py's self-post."""
        from gateway.platforms.api_server import APIServerAdapter

        tokens = APIServerAdapter._bind_api_server_session(
            chat_id="c1", session_key="sk1", session_id="sid1"
        )
        try:
            assert async_delivery_supported() is False
            assert wake_delivery_supported() is True
        finally:
            clear_session_vars(tokens)


# ---------------------------------------------------------------------------
# terminal_tool: the actual notify_on_complete/watch_patterns gate
# ---------------------------------------------------------------------------

class TestTerminalNotifyPushWakeMatrix:
    @pytest.fixture(autouse=True)
    def _clean_watchers(self):
        from tools.process_registry import process_registry

        process_registry.pending_watchers = []
        yield
        process_registry.pending_watchers = []

    def _run_bg(self, command="sleep 30 && echo DONE"):
        from tools.terminal_tool import terminal_tool

        return json.loads(
            terminal_tool(command=command, background=True, notify_on_complete=True)
        )

    def test_push_false_wake_true_keeps_notify_active(self):
        """The api_server shape: push=False, wake=True. notify_on_complete
        must stay True and notify_unsupported must NOT be emitted — this is
        the exact bug this card fixes (the completion routes via
        gateway/wake.py's self-post, so the promise is real)."""
        tokens = set_session_vars(
            platform="api_server", chat_id="s-wake", session_key="s-wake",
            async_delivery=False, wake_delivery=True,
        )
        try:
            d = self._run_bg()
        finally:
            clear_session_vars(tokens)

        assert d.get("notify_on_complete") is True
        assert "notify_unsupported" not in d

    def test_push_false_wake_false_still_refuses(self):
        """Neither capability available — the #10760 refusal must still
        hold (e.g. declare_stateless_channel's shape: cron, hermes -z)."""
        tokens = set_session_vars(
            platform="", chat_id="", session_key="",
            async_delivery=False, wake_delivery=False,
        )
        try:
            d = self._run_bg()
        finally:
            clear_session_vars(tokens)

        assert d.get("notify_on_complete") is False
        assert d.get("notify_unsupported"), "must explain the limitation"
        assert "poll" in d["notify_unsupported"].lower()

    def test_kanban_worker_still_refuses_even_with_wake_true_declared(
        self, monkeypatch
    ):
        """A dispatcher-spawned Kanban worker (HERMES_KANBAN_TASK) is a
        one-shot subprocess with no gateway behind it — it must keep
        refusing the promise even if a session var claims wake=True."""
        monkeypatch.setenv("HERMES_KANBAN_TASK", "t_probe")
        tokens = set_session_vars(
            platform="api_server", chat_id="s-kanban", session_key="s-kanban",
            async_delivery=False, wake_delivery=True,
        )
        try:
            d = self._run_bg()
        finally:
            clear_session_vars(tokens)

        assert d.get("notify_on_complete") is False
        assert d.get("notify_unsupported"), "must explain the limitation"

    def test_push_true_wake_false_keeps_notify_active(self):
        """A push-capable channel that happens to declare wake=False (no
        such real adapter today, but the OR-gate must not require BOTH) —
        push alone is sufficient."""
        tokens = set_session_vars(
            platform="telegram", chat_id="s-push", session_key="s-push",
            async_delivery=True, wake_delivery=False,
        )
        try:
            d = self._run_bg()
        finally:
            clear_session_vars(tokens)

        assert d.get("notify_on_complete") is True
        assert "notify_unsupported" not in d
