"""Tests for tui_gateway.event_replay — per-session event seq + replay ring."""

import threading

import pytest

from tui_gateway import event_replay
from tui_gateway.event_replay import (
    latest_seq,
    reset_replay_state,
    events_since,
    replay_stats,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_replay_state()
    yield
    reset_replay_state()


def _frame(sid, etype="message.delta"):
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": etype, "session_id": sid, "payload": {}},
    }


def test_stamp_adds_monotonic_seq_per_session():
    f1 = _frame("s1")
    f2 = _frame("s1")
    other = _frame("s2")

    event_replay._stamp_event(f1)
    event_replay._stamp_event(other)
    event_replay._stamp_event(f2)

    assert f1["params"]["seq"] == 1
    assert f2["params"]["seq"] == 2  # per-session counter, unaffected by s2
    assert other["params"]["seq"] == 1


def test_stamp_ignores_non_event_and_sessionless_frames():
    rpc = {"jsonrpc": "2.0", "id": 1, "result": {}}
    no_sid = {"jsonrpc": "2.0", "method": "event", "params": {"type": "skin.changed"}}

    event_replay._stamp_event(rpc)
    event_replay._stamp_event(no_sid)

    assert "seq" not in rpc
    assert "seq" not in no_sid["params"]
    assert replay_stats()["events"] == 0


def test_events_since_returns_only_newer_frames_in_order():
    frames = [_frame("s1") for _ in range(5)]
    for f in frames:
        event_replay._stamp_event(f)

    got = events_since("s1", 3)
    assert [e["seq"] for e in got] == [4, 5]
    assert events_since("s1", 0) == [f["params"] for f in frames]
    assert events_since("s1", 99) == []
    assert latest_seq("s1") == 5


def test_events_since_returns_client_dispatchable_event_objects():
    """Cross-language contract: the client's replay loop dispatches an element
    only when it has a TOP-LEVEL ``type`` (json-rpc-gateway.ts fetchReplay:
    ``if (!event?.type) continue``). Returning full JSON-RPC envelopes here
    makes every replayed event silently droppable — the original #94219 bug.
    """
    event_replay._stamp_event(_frame("s1"))
    (event,) = events_since("s1", 0)

    # Bare event object, not an envelope.
    assert event["type"] == "message.delta"
    assert event["session_id"] == "s1"
    assert event["seq"] == 1
    assert "jsonrpc" not in event
    assert "method" not in event
    assert "params" not in event


def test_unknown_session_returns_empty():
    assert events_since("nope", 0) == []
    assert latest_seq("nope") == 0


def test_ring_buffer_is_bounded():
    for i in range(event_replay._REPLAY_BUFFER_MAX + 50):
        event_replay._stamp_event(_frame("s1"))

    stats = replay_stats()
    assert stats["events"] == event_replay._REPLAY_BUFFER_MAX
    # Oldest evicted: last_seen=0 must report truncation via the RPC contract.
    buf = event_replay._replay_buffers["s1"]
    assert buf[0][0] > 1


def test_session_count_bounded_with_fifo_eviction():
    for i in range(event_replay._REPLAY_SESSIONS_MAX + 10):
        event_replay._stamp_event(_frame(f"s{i}"))

    stats = replay_stats()
    assert stats["sessions"] == event_replay._REPLAY_SESSIONS_MAX
    assert events_since("s0", 0) == []  # oldest session fully evicted
    assert latest_seq(f"s{event_replay._REPLAY_SESSIONS_MAX + 9}") == 1


def test_concurrent_stamping_never_drops_or_duplicates_seq():
    errors = []

    def worker(sid):
        try:
            seen = set()
            for _ in range(200):
                f = _frame(sid)
                event_replay._stamp_event(f)
                seq = f["params"]["seq"]
                assert seq not in seen
                seen.add(seq)
        except AssertionError as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert replay_stats()["events"] == 8 * 200


def test_byte_budget_evicts_payloads_and_preserves_gap_semantics(monkeypatch):
    monkeypatch.setattr(event_replay, "_REPLAY_BUFFER_BYTES_MAX", 700)
    monkeypatch.setattr(event_replay, "_REPLAY_PROCESS_BYTES_MAX", 5_000)

    first = _frame("s1", "tool.complete")
    first["params"]["payload"] = {"result": "x" * 400}
    second = _frame("s1", "tool.complete")
    second["params"]["payload"] = {"result": "y" * 400}
    event_replay._stamp_event(first)
    event_replay._stamp_event(second)

    stats = replay_stats()
    assert stats["bytes"] <= stats["max_bytes_per_session"]
    assert [event["seq"] for event in events_since("s1", 0)] == [2]
    assert event_replay.is_truncated("s1", 0)

    reset_replay_state()
    monkeypatch.setattr(event_replay, "_REPLAY_BUFFER_BYTES_MAX", 1_000)
    monkeypatch.setattr(event_replay, "_REPLAY_PROCESS_BYTES_MAX", 700)
    first = _frame("s1", "tool.complete")
    first["params"]["payload"] = {"result": "x" * 400}
    event_replay._stamp_event(first)
    other = _frame("s2", "tool.complete")
    other["params"]["payload"] = {"result": "y" * 400}
    event_replay._stamp_event(other)

    stats = replay_stats()
    assert stats["bytes"] <= stats["max_bytes_process"]
    assert events_since("s1", 0) == []
    assert event_replay.is_truncated("s1", 0)
    assert [event["seq"] for event in events_since("s2", 0)] == [1]


def test_oversized_event_marks_gap_even_with_empty_buffer(monkeypatch):
    monkeypatch.setattr(event_replay, "_REPLAY_BUFFER_BYTES_MAX", 300)
    monkeypatch.setattr(event_replay, "_REPLAY_PROCESS_BYTES_MAX", 300)

    oversized = _frame("s1", "tool.complete")
    oversized["params"]["payload"] = {"result": "x" * 1000}
    event_replay._stamp_event(oversized)

    assert events_since("s1", 0) == []
    assert event_replay.is_truncated("s1", 0)
    assert not event_replay.is_truncated("s1", 1)

    event_replay._stamp_event(_frame("s1"))
    assert [event["seq"] for event in events_since("s1", 0)] == [2]
    assert event_replay.is_truncated("s1", 0)
    assert not event_replay.is_truncated("s1", 1)
    assert not event_replay.is_truncated("s1", 2)

    # The gap watermark never moves backwards: a small frame after an oversized one must
    # not hide the hole the oversized frame left.
    reset_replay_state()
    monkeypatch.setattr(event_replay, "_REPLAY_BUFFER_MAX", 1)
    monkeypatch.setattr(event_replay, "_REPLAY_BUFFER_BYTES_MAX", 1000)
    monkeypatch.setattr(event_replay, "_REPLAY_PROCESS_BYTES_MAX", 1000)
    event_replay._stamp_event(_frame("s"))
    large = _frame("s")
    large["params"]["payload"] = {"data": "x" * 2000}
    event_replay._stamp_event(large)
    event_replay._stamp_event(_frame("s"))
    assert event_replay.is_truncated("s", 1)


def test_truncation_detection_semantics():
    """The RPC handler's truncated flag: gap between last_seen and buffer start."""
    # Overflow the ring so the oldest events are genuinely evicted.
    for _ in range(event_replay._REPLAY_BUFFER_MAX + 10):
        event_replay._stamp_event(_frame("s1"))

    with event_replay._replay_lock:
        oldest = event_replay._replay_buffers["s1"][0][0]

    assert oldest > 1  # eviction happened

    # Client saw everything up to just before the buffer → NOT truncated.
    assert not event_replay.is_truncated("s1", oldest - 1)
    # Client saw seq 5, buffer starts later → truncated.
    assert event_replay.is_truncated("s1", 5)
    # Unknown session: nothing evicted, nothing truncated.
    assert not event_replay.is_truncated("nope", 0)


# --- turn identity (turn_id) -------------------------------------------------
# ``seq`` orders frames within a session but spans turns, so payload from an
# older turn delivered after the next turn opened is indistinguishable from
# live payload at the client edge. These cover the identity stamped alongside
# seq in the same choke point.


def test_turn_id_is_stable_across_a_turn_and_changes_on_next_start():
    start1 = _frame("s1", "message.start")
    delta1 = _frame("s1", "message.delta")
    complete1 = _frame("s1", "message.complete")
    start2 = _frame("s1", "message.start")
    delta2 = _frame("s1", "message.delta")

    for f in (start1, delta1, complete1, start2, delta2):
        event_replay._stamp_event(f)

    t1 = start1["params"]["payload"]["turn_id"]
    assert delta1["params"]["payload"]["turn_id"] == t1
    assert complete1["params"]["payload"]["turn_id"] == t1

    t2 = start2["params"]["payload"]["turn_id"]
    assert t2 != t1, "a new message.start must open a new turn identity"
    assert delta2["params"]["payload"]["turn_id"] == t2


def test_late_payload_keeps_the_closed_turn_id_not_the_new_one():
    """The bug this identity exists for.

    A delta emitted by turn 1 after turn 2 already started must still carry
    turn 1's id, so the renderer can reject it instead of applying it to the
    new bubble.
    """
    start1 = _frame("s1", "message.start")
    event_replay._stamp_event(start1)
    t1 = start1["params"]["payload"]["turn_id"]

    complete1 = _frame("s1", "message.complete")
    event_replay._stamp_event(complete1)

    start2 = _frame("s1", "message.start")
    event_replay._stamp_event(start2)
    t2 = start2["params"]["payload"]["turn_id"]

    # Turn 1's straggler, emitted with turn 1's identity already attached.
    straggler = _frame("s1", "message.delta")
    straggler["params"]["payload"]["turn_id"] = t1
    event_replay._stamp_event(straggler)

    assert straggler["params"]["payload"]["turn_id"] == t1
    assert straggler["params"]["payload"]["turn_id"] != t2


def test_replay_preserves_the_original_turn_id():
    start = _frame("s1", "message.start")
    delta = _frame("s1", "message.delta")
    event_replay._stamp_event(start)
    event_replay._stamp_event(delta)
    original = delta["params"]["payload"]["turn_id"]

    # A newer turn opens after the client dropped.
    event_replay._stamp_event(_frame("s1", "message.start"))

    replayed = events_since("s1", 0)
    replayed_deltas = [
        e for e in replayed if e["type"] == "message.delta"
    ]
    assert replayed_deltas, "replay must return the buffered delta"
    assert replayed_deltas[0]["payload"]["turn_id"] == original, (
        "replay must reuse the original turn id, not relabel it as current"
    )


def test_turn_id_not_stamped_on_session_control_events():
    event_replay._stamp_event(_frame("s1", "message.start"))
    info = _frame("s1", "session.info")
    event_replay._stamp_event(info)

    assert "turn_id" not in info["params"]["payload"]


def test_tool_events_inherit_the_live_turn_id():
    start = _frame("s1", "message.start")
    tool = _frame("s1", "tool.start")
    event_replay._stamp_event(start)
    event_replay._stamp_event(tool)

    assert tool["params"]["payload"]["turn_id"] == start["params"]["payload"]["turn_id"]


def test_payload_absent_gets_turn_id_without_crashing():
    start = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "message.start", "session_id": "s1"},
    }
    event_replay._stamp_event(start)

    assert start["params"]["payload"]["turn_id"]
