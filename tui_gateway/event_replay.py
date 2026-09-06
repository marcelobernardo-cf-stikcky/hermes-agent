"""Per-session event sequencing + bounded replay for WS reconnects.

Every event frame through :func:`server.write_json` (hence ``_emit``) gets a per-session monotonic
``seq`` and lands in a small ring per session; a reconnecting client calls ``session.events.since``
with its last seen seq and gets everything newer. Invariants: stdio TUI unaffected (``seq`` only on
event frames; Ink ignores unknown keys); one lock guards counters + buffers, and write_json already
serializes per-transport writes so stamping cannot reorder frames; memory bound =
_REPLAY_BUFFER_MAX events x _REPLAY_SESSIONS_MAX sessions, oldest session evicted FIFO.
"""

from __future__ import annotations

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

_replay_lock = threading.Lock()
# sid -> deque of (seq, params dict) — the bare event (type/session_id/seq/payload),
# the exact shape the client's dispatch path consumes.
_replay_buffers: "OrderedDict[str, deque]" = OrderedDict()
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
    with _replay_lock:
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
            buf = _replay_buffers[sid] = deque(maxlen=_REPLAY_BUFFER_MAX)
            while len(_replay_buffers) > _REPLAY_SESSIONS_MAX:
                _oldest_sid, _oldest_buf = _replay_buffers.popitem(last=False)
                _replay_next_seq.pop(_oldest_sid, None)
                _turn_ids.pop(_oldest_sid, None)
        buf.append((seq, params))


def events_since(sid: str, last_seen: int) -> list[dict]:
    """Recorded EVENT OBJECTS (each frame's ``params`` dict) with seq > last_seen for *sid*.

    Returning the full JSON-RPC envelope would make every replayed event fail the
    client's ``event.type`` gate and be silently dropped.
    """
    with _replay_lock:
        buf = _replay_buffers.get(sid or "")
        return [event for seq, event in buf if seq > last_seen] if buf else []


def is_truncated(sid: str, last_seen: int) -> bool:
    """True when events between *last_seen* and the ring's oldest retained seq were
    evicted — the client must refetch history instead of trusting the replay."""
    with _replay_lock:
        buf = _replay_buffers.get(sid or "")
        return bool(buf) and last_seen + 1 < buf[0][0]


def latest_seq(sid: str) -> int:
    """Current highest stamped seq for *sid* (0 when unknown)."""
    with _replay_lock:
        return _replay_next_seq.get(sid or "", 0)


def reset_replay_state() -> None:
    """Test hook."""
    with _replay_lock:
        _replay_buffers.clear()
        _replay_next_seq.clear()
        _turn_ids.clear()


def replay_stats() -> dict:
    """Telemetry: buffer occupancy for the ops/debug surface."""
    with _replay_lock:
        return {
            "sessions": len(_replay_buffers),
            "events": sum(len(b) for b in _replay_buffers.values()),
            "max_per_session": _REPLAY_BUFFER_MAX}
