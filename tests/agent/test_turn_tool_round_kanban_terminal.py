"""A kanban worker's tool round ends the turn once a terminal board tool returned ok."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import turn_tool_round


def _round(monkeypatch, result_content):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc")
    monkeypatch.delenv("HERMES_KANBAN_STOP_NUDGE", raising=False)
    tc = SimpleNamespace(id="7", type="function",
                         function=SimpleNamespace(name="kanban_complete", arguments="{}"))
    assistant_message = SimpleNamespace(tool_calls=[tc], content="")
    messages = [{"role": "user", "content": "go"}]
    agent = MagicMock(quiet_mode=True, verbose_logging=False, valid_tool_names={"kanban_complete"},
                      _tool_guardrail_halt_decision=None, _incremental_persistence_failed=False,
                      stream_delta_callback=None)
    agent._deduplicate_tool_calls.side_effect = lambda calls: calls
    agent._cap_delegate_task_calls.side_effect = lambda calls: calls
    agent._flush_messages_to_session_db.return_value = True

    def execute(msg, msgs, *_):
        msgs.append({"role": "tool", "tool_call_id": "7", "content": result_content})
    agent._execute_tool_calls.side_effect = execute

    compressed = SimpleNamespace(messages=messages, active_system_prompt="s", conversation_history=[],
                                 compression_attempts=0, final_response=None, turn_exit_reason="unknown",
                                 current_turn_user_idx=0, end_turn=False)
    with patch.object(turn_tool_round, "validate_tool_calls",
                      return_value=SimpleNamespace(action="proceed", mixed_invalid_batch=False)), \
         patch.object(turn_tool_round, "stage_tool_call_message",
                      return_value=({"role": "assistant", "content": "", "tool_calls": []}, False)), \
         patch.object(turn_tool_round, "compress_after_tool_results", return_value=compressed):
        return turn_tool_round.run_tool_round(
            agent, assistant_message=assistant_message, finish_reason="tool_calls", messages=messages,
            conversation_history=[], api_call_count=1, effective_task_id="t", user_message="go",
            system_message="s", active_system_prompt="s", compression_attempts=0,
            max_compression_attempts=3, final_response=None, failed=False, _turn_exit_reason="unknown",
            truncated_tool_call_retries=0, current_turn_user_idx=0)


def test_ok_terminal_tool_breaks_the_turn(monkeypatch):
    verdict = _round(monkeypatch, '{"ok": true, "task_id": "t_abc"}')
    assert verdict.action == "break"
    assert verdict._turn_exit_reason == "kanban_terminal"
    assert verdict.messages[-1]["content"] == "Task handed to the board via kanban_complete."


@pytest.mark.parametrize("content", ['{"error": "summary is required"}'])
def test_failed_terminal_tool_keeps_going(monkeypatch, content):
    assert _round(monkeypatch, content).action == "continue"
