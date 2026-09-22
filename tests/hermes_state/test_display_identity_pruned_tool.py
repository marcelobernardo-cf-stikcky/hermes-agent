"""Compaction prunes tool output/args; the display identity must survive it (else "Result unavailable")."""
import json

from hermes_state_messages import SessionMessagesMixin as M


def _row(role, content, tool_call_id=None, tool_calls=None):
    return {"role": role, "content": content, "timestamp": 1790105404.3, "tool_call_id": tool_call_id,
            "tool_calls": tool_calls, "tool_name": None, "display_kind": None, "display_metadata": None}


def test_pruned_tool_result_keeps_identity():
    full, pruned = _row("tool", "long output", "call_1"), _row("tool", "[pruned]", "call_1")
    assert M._display_dedupe_key(None, full) == M._display_dedupe_key(None, pruned)
    assert M._display_dedupe_key(None, full) != M._display_dedupe_key(None, _row("tool", "x", "call_2"))


def test_pruned_tool_call_args_keep_identity():
    a = json.dumps([{"id": "call_1", "function": {"name": "terminal", "arguments": "{\"command\": \"ls -la\"}"}}])
    b = json.dumps([{"id": "call_1", "function": {"name": "terminal", "arguments": "{\"command\": \"[pruned]\"}"}}])
    assert M._display_dedupe_key(None, _row("assistant", "", tool_calls=a)) == \
        M._display_dedupe_key(None, _row("assistant", "", tool_calls=b))
