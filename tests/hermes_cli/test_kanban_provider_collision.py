"""Kanban provider reservations must constrain worker fallback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.error_classifier import FailoverReason
from hermes_cli import kanban_db as kb
from run_agent import AIAgent


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _make_agent(fallback_model):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://api.anthropic.com",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
    agent.model = "claude-3-7-sonnet"
    agent.provider = "anthropic"
    return agent


def _claim_overridden_task(monkeypatch: pytest.MonkeyPatch) -> tuple[str, int]:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(
            conn,
            title="fallback collision",
            assignee="rigger",
            model_override="claude-3-7-sonnet",
            provider_override="anthropic",
        )
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (task_id,))
        assert kb.claim_task(conn, task_id, claimer="rigger") is not None
        row = conn.execute(
            "SELECT current_run_id, model_override, provider_override "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        assert row is not None
        assert row["model_override"] == "claude-3-7-sonnet"
        assert row["provider_override"] == "anthropic"
        run_id = int(row["current_run_id"])
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return task_id, run_id


def test_dispatcher_reserves_orchestrator_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_KANBAN_RESERVED_PROVIDERS", raising=False)
    with (
        patch(
            "hermes_cli.config.load_config",
            return_value={"kanban": {"orchestrator_profile": "orchestrator"}},
        ),
        patch("hermes_cli.profiles.get_active_profile_name", return_value="default"),
        # Patch no modulo CANONICO: a funcao vive em kanban_db_dispatch e chega a
        # kanban_db so pelo re-export lazy de compat. Patchar o shim funciona apenas
        # enquanto kanban_db_dispatch nao foi importado por outro teste — o que torna
        # este caso dependente de ordem (passa isolado, falha na suite).
        patch(
            "hermes_cli.kanban_db_dispatch._profile_provider_for_dispatch_guard",
            return_value="openai-codex",
        ),
    ):
        assert kb._resolve_kanban_reserved_providers(None) == ["openai-codex"]


def test_reserved_openai_codex_is_skipped_before_client_resolution(
    kanban_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id, _ = _claim_overridden_task(monkeypatch)
    monkeypatch.setenv("HERMES_KANBAN_RESERVED_PROVIDERS", "openai-codex")
    agent = _make_agent(
        [
            {"provider": "openai-codex", "model": "gpt-5.6-codex"},
            {"provider": "zai", "model": "glm-5.2"},
        ]
    )

    with patch(
        "agent.auxiliary_client.resolve_provider_client",
        return_value=(
            SimpleNamespace(
                base_url="https://api.z.ai/v1",
                api_key="zai-test-key",
            ),
            "glm-5.2",
        ),
    ) as resolve_client:
        assert agent._try_activate_fallback(reason=FailoverReason.rate_limit) is True

    assert resolve_client.call_count == 1
    assert resolve_client.call_args.args[0] == "zai"
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "running"
        events = [event for event in kb.list_events(conn, task_id)
                  if event.kind == "provider_collision"]
        assert len(events) == 1
        payload = events[0].payload or {}
        assert payload["candidate_provider"] == "openai-codex"


def test_all_reserved_fallbacks_block_the_worker_task(
    kanban_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id, _ = _claim_overridden_task(monkeypatch)
    monkeypatch.setenv(
        "HERMES_KANBAN_RESERVED_PROVIDERS", "openai-codex,openrouter"
    )
    agent = _make_agent(
        [
            {"provider": "openai-codex", "model": "gpt-5.6-codex"},
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        ]
    )

    with patch("agent.auxiliary_client.resolve_provider_client") as resolve_client:
        assert agent._try_activate_fallback(reason=FailoverReason.rate_limit) is False

    resolve_client.assert_not_called()
    with kb.connect_closing() as conn:
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "blocked"
        collisions = [event for event in kb.list_events(conn, task_id)
                      if event.kind == "provider_collision"]
        assert {event.payload["candidate_provider"] for event in collisions} == {
            "openai-codex",
            "openrouter",
        }
        blocked = [event for event in kb.list_events(conn, task_id)
                   if event.kind == "blocked"]
        assert blocked
        assert blocked[-1].payload["kind"] == "capability"
