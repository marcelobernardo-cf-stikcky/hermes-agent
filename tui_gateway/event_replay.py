"""Per-session event sequencing + bounded replay for WS reconnects.

Every event frame through :func:`server.write_json` (hence ``_emit``) gets a per-session monotonic
``seq`` and lands in a small ring per session; a reconnecting client calls ``session.events.since``
with its last seen seq and gets everything newer. Invariants: stdio TUI unaffected (``seq`` only on
event frames; Ink ignores unknown keys); one lock guards counters + buffers, and write_json already
serializes per-transport writes so stamping cannot reorder frames; memory bound =
_REPLAY_BUFFER_MAX events AND _REPLAY_BUFFER_BYTES_MAX serialized bytes per session,
_REPLAY_PROCESS_BYTES_MAX bytes across at most _REPLAY_SESSIONS_MAX sessions, oldest evicted
FIFO. Evicted or never-retained (oversized) frames leave a truncation watermark so a
reconnecting client refetches instead of trusting a replay with holes.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict, deque

# Seq counters live in-process, so a restart resets them to 1 while clients hold high
# watermarks — events_since(sid, 97) would return [] with truncated=False forever. The
# epoch lets clients detect the restart and reset their watermarks.
_REPLAY_EPOCH = uuid.uuid4().hex

# A long turn emits ~hundreds of token events; 512 covers minutes of streaming plus
# all control events. Desktop users rarely exceed a dozen live chats.
_REPLAY_BUFFER_MAX = 512
_REPLAY_SESSIONS_MAX = 64
# A ring may legitimately hold many bounded 64 KiB tool results (512 of them ≈ 32 MiB per
# session, ×64 sessions before any cap); bound the serialized bytes so replay memory cannot
# scale with payload size without limit.
_REPLAY_BUFFER_BYTES_MAX = 4 * 1024 * 1024
_REPLAY_PROCESS_BYTES_MAX = 64 * 1024 * 1024

_replay_lock = threading.Lock()
# sid -> deque of (seq, params dict, serialized bytes).
_replay_buffers: "OrderedDict[str, deque]" = OrderedDict()
_replay_buffer_bytes: dict[str, int] = {}
_replay_evicted_through: dict[str, int] = {}
_replay_total_bytes = 0
_replay_next_seq: dict[str, int] = {}

# Turn identity per session. ``seq`` orders frames within a session but spans
# turns, so a payload emitted by an older turn after the next turn opened is
# indistinguishable from live payload at the client edge (reconnect replay and
# run supersession both produce that ordering). Stamping identity here — the
# same choke point that already stamps ``seq`` — keeps the ~18 scattered
# ``_emit("message.*")`` call sites untouched and guarantees replay reuses the
# ORIGINAL id, because the ring buffers the already-stamped ``params`` dict.
#
# Scope is deliberately the streaming family (message.* + tool.*): those are
# the events the renderer applies into a turn's bubble. Session-level control
# events carry no turn-bound payload and are left alone.
_TURN_SCOPED_EVENT_PREFIXES = ("message.", "tool.")
# ``message.start`` opens a turn; the terminal event closes it. Payload that
# arrives after the close keeps the closed turn's id, so the client can still
# tell WHICH turn it belonged to instead of seeing it unlabeled.
_TURN_START_EVENTS = frozenset({"message.start"})

_turn_ids: dict[str, str] = {}


def _turn_scoped(event_type: str) -> bool:
    return event_type.startswith(_TURN_SCOPED_EVENT_PREFIXES)


def current_turn_id(sid: str) -> str | None:
    """Turn id currently stamped for *sid* (None before the first turn)."""
    with _replay_lock:
        return _turn_ids.get(sid or "")


def replay_epoch() -> str:
    """Opaque token identifying this server process's seq numbering."""
    return _REPLAY_EPOCH


def _stamp_event(obj: dict) -> None:
    """Stamp one outgoing event frame (mutates obj in place) and record it."""
    if obj.get("method") != "event":
        return
    params = obj.get("params")
    if not isinstance(params, dict):
        return
    sid = params.get("session_id") or ""
    if not sid:
        # Session-less global events (skin.changed etc.) are re-fetchable via their own RPCs.
        return
    # Sizing stays OUTSIDE the lock (same rule as transport.write) so one large payload cannot
    # stall other threads' frames; ``seq`` is not stamped yet, a few bytes off a MiB budget.
    size = len(json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8", errors="surrogatepass"))
    with _replay_lock:
        global _replay_total_bytes
        seq = _replay_next_seq.get(sid, 0) + 1
        _replay_next_seq[sid] = seq
        params["seq"] = seq
        event_type = str(params.get("type") or "")
        if _turn_scoped(event_type):
            if event_type in _TURN_START_EVENTS:
                # New turn opens: mint identity here, at the same point seq is
                # assigned, so every later frame of this turn inherits it.
                _turn_ids[sid] = uuid.uuid4().hex
            turn_id = _turn_ids.get(sid)
            if turn_id is not None:
                # setdefault, not assignment: a replayed frame arrives with its
                # original id already present and must keep it. Overwriting
                # would relabel old payload as current — the exact bug this
                # identity is meant to catch.
                payload = params.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                    params["payload"] = payload
                payload.setdefault("turn_id", turn_id)
        buf = _replay_buffers.get(sid)
        if buf is None:
            buf = _replay_buffers[sid] = deque()
            _replay_buffer_bytes[sid] = 0
            while len(_replay_buffers) > _REPLAY_SESSIONS_MAX:
                oldest_sid, oldest_buf = _replay_buffers.popitem(last=False)
                _replay_total_bytes -= _replay_buffer_bytes.pop(oldest_sid, 0)
                _replay_next_seq.pop(oldest_sid, None)
                _turn_ids.pop(oldest_sid, None)
                _replay_evicted_through.pop(oldest_sid, None)
        if size > _REPLAY_BUFFER_BYTES_MAX or size > _REPLAY_PROCESS_BYTES_MAX:
            _replay_evicted_through[sid] = seq
            return
        buf.append((seq, params, size))
        _replay_buffer_bytes[sid] += size
        _replay_total_bytes += size
        while len(buf) > _REPLAY_BUFFER_MAX or _replay_buffer_bytes[sid] > _REPLAY_BUFFER_BYTES_MAX:
            evicted_seq, _event, evicted_size = buf.popleft()
            _replay_buffer_bytes[sid] -= evicted_size
            _replay_total_bytes -= evicted_size
            _replay_evicted_through[sid] = max(_replay_evicted_through.get(sid, 0), evicted_seq)
        while _replay_total_bytes > _REPLAY_PROCESS_BYTES_MAX:
            for evict_sid, evict_buf in _replay_buffers.items():
                if evict_buf:
                    evicted_seq, _event, evicted_size = evict_buf.popleft()
                    _replay_buffer_bytes[evict_sid] -= evicted_size
                    _replay_total_bytes -= evicted_size
                    _replay_evicted_through[evict_sid] = max(_replay_evicted_through.get(evict_sid, 0), evicted_seq)
                    break


def events_since(sid: str, last_seen: int) -> list[dict]:
    """Recorded EVENT OBJECTS (each frame's ``params`` dict) with seq > last_seen for *sid*.

    Returning the full JSON-RPC envelope would make every replayed event fail the
    client's ``event.type`` gate and be silently dropped.
    """
    with _replay_lock:
        buf = _replay_buffers.get(sid or "")
        return [event for seq, event, _size in buf if seq > last_seen] if buf else []


def is_truncated(sid: str, last_seen: int) -> bool:
    """True when events between *last_seen* and the ring's oldest retained seq were
    evicted — the client must refetch history instead of trusting the replay."""
    with _replay_lock:
        return last_seen < _replay_evicted_through.get(sid or "", 0)


def latest_seq(sid: str) -> int:
    """Current highest stamped seq for *sid* (0 when unknown)."""
    with _replay_lock:
        return _replay_next_seq.get(sid or "", 0)


def reset_replay_state() -> None:
    """Test hook."""
    with _replay_lock:
        global _replay_total_bytes
        _replay_buffers.clear()
        _replay_buffer_bytes.clear()
        _replay_evicted_through.clear()
        _replay_next_seq.clear()
        _replay_total_bytes = 0
        _turn_ids.clear()


def replay_stats() -> dict:
    """Telemetry: buffer occupancy for the ops/debug surface."""
    with _replay_lock:
        return {
            "sessions": len(_replay_buffers),
            "events": sum(len(buffer) for buffer in _replay_buffers.values()),
            "bytes": _replay_total_bytes,
            "max_per_session": _REPLAY_BUFFER_MAX,
            "max_bytes_per_session": _REPLAY_BUFFER_BYTES_MAX,
            "max_bytes_process": _REPLAY_PROCESS_BYTES_MAX}
