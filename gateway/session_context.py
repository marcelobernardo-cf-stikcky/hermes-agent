"""Session-scoped context variables for the Hermes gateway.

Replaces the old ``os.environ``-based ``HERMES_SESSION_*`` state with task-local ``ContextVar``s
(inherited by ``run_in_executor`` threads), so concurrently handled messages no longer clobber each
other's routing ids.  ``get_session_env`` is a drop-in for ``os.getenv``.
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

# "Never set here" (falls back to os.environ for CLI/cron) vs "" = explicitly cleared (no fallback).
_UNSET: Any = object()

# Process-level latch: has set_session_vars() ever bound a session?  When engaged, the subprocess
# env bridge treats ContextVars as authoritative and an _UNSET var as "no session in THIS task".
_session_context_engaged: bool = False


def session_context_engaged() -> bool:
    """True if any session has been bound via set_session_vars in this process."""
    return _session_context_engaged


# --- Per-task session variables: bound by set_session_vars / cleared to "" by clear_session_vars;
# tuple ORDER is the positional order of ``values`` in set_session_vars (zipped).
# * SCOPE_ID: platform-neutral scope (guild / workspace / Matrix server) so async producers can
#   persist a completion's full routing origin (relay egress guards need it).
# * UI_SESSION_ID: in-process UI tab id, separate from the durable SESSION_ID, so a stale/rotated
#   durable key is not consumed by the wrong poller.
# * MESSAGE_ID: reply anchor keeping notifications inside the originating Telegram topic.
# * CRON_SESSION: tri-state — _UNSET = legacy env fallback; "1" = cron; "" = non-cron, masks env.
_SESSION_VARS = (
    _SESSION_PLATFORM, _SESSION_SOURCE, _SESSION_CHAT_ID, _SESSION_CHAT_TYPE,
    _SESSION_CHAT_NAME, _SESSION_THREAD_ID, _SESSION_USER_ID, _SESSION_USER_ID_ALT,
    _SESSION_USER_NAME, _SESSION_SCOPE_ID, _SESSION_KEY, _SESSION_ID,
    _SESSION_UI_SESSION_ID, _SESSION_MESSAGE_ID, _SESSION_PROFILE,
    _BROWSER_CONTROL_PRINCIPAL, _BROWSER_CONTROL_TRANSPORT_FAMILY, _CRON_SESSION,
) = tuple(ContextVar(name, default=_UNSET) for name in (
    "HERMES_SESSION_PLATFORM", "HERMES_SESSION_SOURCE", "HERMES_SESSION_CHAT_ID",
    "HERMES_SESSION_CHAT_TYPE", "HERMES_SESSION_CHAT_NAME", "HERMES_SESSION_THREAD_ID",
    "HERMES_SESSION_USER_ID", "HERMES_SESSION_USER_ID_ALT", "HERMES_SESSION_USER_NAME",
    "HERMES_SESSION_SCOPE_ID", "HERMES_SESSION_KEY", "HERMES_SESSION_ID",
    "HERMES_UI_SESSION_ID", "HERMES_SESSION_MESSAGE_ID", "HERMES_SESSION_PROFILE",
    "HERMES_BROWSER_CONTROL_PRINCIPAL", "HERMES_BROWSER_CONTROL_TRANSPORT_FAMILY",
    "HERMES_CRON_SESSION",
))

# Whether this channel can route an ASYNC completion back AFTER the turn ends (see
# ``async_delivery_supported()``).  _UNSET => supported (CLI, contextvar-unaware paths); stateless
# adapters (API server, Kanban workers) opt OUT via ``supports_async_delivery = False`` at bind.
_SESSION_ASYNC_DELIVERY = ContextVar("HERMES_SESSION_ASYNC_DELIVERY", default=_UNSET)

# Cron auto-delivery vars, set per-job in run_job() so concurrent jobs don't clobber.
_CRON_AUTO_DELIVER_PLATFORM = ContextVar("HERMES_CRON_AUTO_DELIVER_PLATFORM", default=_UNSET)
_CRON_AUTO_DELIVER_CHAT_ID = ContextVar("HERMES_CRON_AUTO_DELIVER_CHAT_ID", default=_UNSET)
_CRON_AUTO_DELIVER_THREAD_ID = ContextVar("HERMES_CRON_AUTO_DELIVER_THREAD_ID", default=_UNSET)

# Legacy env-var name -> ContextVar for get_session_env (_SESSION_ASYNC_DELIVERY deliberately
# absent: it is a bool capability, read via async_delivery_supported).
_VAR_MAP = {var.name: var for var in (
    *_SESSION_VARS, _CRON_AUTO_DELIVER_PLATFORM, _CRON_AUTO_DELIVER_CHAT_ID,
    _CRON_AUTO_DELIVER_THREAD_ID,
)}

# Whether the current session's channel can WAKE the real session with a
# fresh turn after the current one ends — distinct from
# ``_SESSION_ASYNC_DELIVERY`` (push a message into an already-open channel).
#
# A stateless request/response adapter (the API server) has no open channel
# to push into (``_SESSION_ASYNC_DELIVERY`` is False there), but it CAN
# resume its session by self-posting a new request through its own entry
# point (see ``gateway/wake.py::deliver_wake``) — so it is push=False but
# wake=True. Genuinely finite runtimes with no gateway drain loop at all
# (one-shot Kanban workers, ``hermes -z``, cron) are both push=False and
# wake=False; see ``declare_stateless_channel`` and
# ``wake_delivery_supported``.
#
# Default _UNSET => treated as supported, mirroring
# ``_SESSION_ASYNC_DELIVERY``'s default so CLI / contextvar-unaware paths
# keep working.
_SESSION_WAKE_DELIVERY: ContextVar = ContextVar("HERMES_SESSION_WAKE_DELIVERY", default=_UNSET)

# Cron auto-delivery vars — set per-job in run_job() so concurrent jobs
# don't clobber each other's delivery targets.
_CRON_AUTO_DELIVER_PLATFORM: ContextVar = ContextVar("HERMES_CRON_AUTO_DELIVER_PLATFORM", default=_UNSET)
_CRON_AUTO_DELIVER_CHAT_ID: ContextVar = ContextVar("HERMES_CRON_AUTO_DELIVER_CHAT_ID", default=_UNSET)
_CRON_AUTO_DELIVER_THREAD_ID: ContextVar = ContextVar("HERMES_CRON_AUTO_DELIVER_THREAD_ID", default=_UNSET)

def _runtime_cwd(func: str, *args: Any) -> None:
    """Best-effort call of ``agent.runtime_cwd.<func>``; import/runtime failures are ignored."""
    try:
        from agent import runtime_cwd
        getattr(runtime_cwd, func)(*args)
    except Exception:
        pass


def set_current_session_id(session_id: str) -> None:
    """Synchronize ``HERMES_SESSION_ID`` across ContextVar and ``os.environ`` (tools read it
    with an os.environ fallback).  Delegated subagent children (built in the parent process)
    get ONLY the task-local write, or they would clobber the parent's id."""
    _SESSION_ID.set(session_id)
    try:
        from agent.delegation_context import is_delegated_child_context
        if is_delegated_child_context():
            return
    except Exception:
        pass
    os.environ["HERMES_SESSION_ID"] = session_id


@contextmanager
def scoped_current_session_id(session_id: str | None = None) -> Iterator[None]:
    """Bind a task-local session id and restore the prior value on exit; never touches
    ``os.environ``.  ``session_id=None`` is a pure save/restore boundary."""
    previous = _SESSION_ID.get()
    if session_id is not None:
        _SESSION_ID.set(session_id)
    try:
        yield
    finally:
        _SESSION_ID.set(previous)


def set_session_vars(
    platform: str = "", source: str = "", chat_id: str = "", chat_type: str = "",
    chat_name: str = "", thread_id: str = "", user_id: str = "", user_id_alt: str = "",
    user_name: str = "", scope_id: str = "", session_key: str = "", session_id: str = "",
    message_id: str = "", profile: str = "", browser_control_principal: str = "",
    browser_control_transport_family: str = "", cwd: str = "", async_delivery: bool = True,
    wake_delivery: Any = _UNSET, ui_session_id: str = "", cron_session: Any = _UNSET,
) -> list:
    """Set all session context variables and return reset tokens.  Call
    ``clear_session_vars(tokens)`` in a ``finally``; not nestable, clearing resets every var
    to ``""`` rather than restoring prior values (tokens are accepted only for API compat)."""
    global _session_context_engaged
    _session_context_engaged = True
    values = (
        platform, source, chat_id, chat_type, chat_name, thread_id, user_id, user_id_alt,
        user_name, scope_id, session_key, session_id, ui_session_id, message_id, profile,
        browser_control_principal, browser_control_transport_family, cron_session,
    )
    tokens = [var.set(value) for var, value in zip(_SESSION_VARS, values)]
    tokens.append(_SESSION_ASYNC_DELIVERY.set(bool(async_delivery)))
    # Fail closed: a pre-split caller that only says async_delivery=False must NOT
    # silently become wake-capable, so wake MIRRORS async unless the caller opts
    # into the split explicitly. Only then are the bits independent (api_server is
    # push=False / wake=True: it cannot push, but gateway/wake.py can resume it).
    tokens.append(_SESSION_WAKE_DELIVERY.set(
        bool(async_delivery) if wake_delivery is _UNSET else bool(wake_delivery)))
    _runtime_cwd("set_session_cwd", cwd)
    return tokens


def clear_session_vars(tokens: list) -> None:
    """Mark session context variables as explicitly cleared (``""``, not ``_UNSET``), so
    ``get_session_env`` returns empty instead of stale ``os.environ`` values.  Async-delivery
    goes back to ``_UNSET``: a cleared context is default-supported, not opted-out."""
    for var in _SESSION_VARS:
        var.set("")
    _SESSION_ASYNC_DELIVERY.set(_UNSET)
    # Same reasoning for wake-delivery capability — see _SESSION_WAKE_DELIVERY.
    _SESSION_WAKE_DELIVERY.set(_UNSET)
    _runtime_cwd("clear_session_cwd")


def reset_session_vars() -> None:
    """Reset every session var to ``_UNSET`` ("never bound here") for THIS context.  Call at
    the top of a fresh task *before* it binds.  ``_SESSION_ASYNC_DELIVERY`` and
    ``_SESSION_WAKE_DELIVERY`` (outside ``_VAR_MAP``) are reset explicitly too."""
    for var in _VAR_MAP.values():
        var.set(_UNSET)
    _SESSION_ASYNC_DELIVERY.set(_UNSET)
    _SESSION_WAKE_DELIVERY.set(_UNSET)
    _runtime_cwd("clear_session_cwd")


def get_session_env(name: str, default: str = "") -> str:
    """Read a session var by legacy ``HERMES_SESSION_*`` name; drop-in for os.getenv.  The
    ContextVar wins if ever set here (even to ``""``); else ``os.environ``; else *default*."""
    var = _VAR_MAP.get(name)
    if var is not None and (value := var.get()) is not _UNSET:
        return value
    return os.getenv(name, default)


# Surfaces that are not a human chat channel (gateway binds HERMES_SESSION_PLATFORM, CLI/TUI/
# desktop bind HERMES_SESSION_SOURCE, so both are consulted).  Default-deny: an unrecognized
# identity counts as messaging.  Mirrors LOCAL_SESSION_SOURCE_IDS in apps/desktop session-source.ts.
NON_MESSAGING_SESSION_SURFACES = frozenset({
    "", "api_server", "cli", "codex", "desktop", "gateway", "kanban", "local",
    "msgraph_webhook", "tool", "tui", "webhook",
})


def session_is_messaging_surface() -> bool:
    """Whether this turn is delivered over a human messaging channel (checks
    ``HERMES_PLATFORM``, then the session platform, then the session source)."""
    platform = os.getenv("HERMES_PLATFORM") or get_session_env("HERMES_SESSION_PLATFORM", "")
    idents = (platform, get_session_env("HERMES_SESSION_SOURCE", ""))
    idents = (str(v or "").strip().lower() for v in idents)
    return any(ident and ident not in NON_MESSAGING_SESSION_SURFACES for ident in idents)


def declare_stateless_channel() -> None:
    """Declare that this session cannot receive an async background completion.

    Binds both delivery capabilities (push AND wake) to False, leaving every
    other session var unset. Use this instead of ``set_session_vars(async_delivery=False)``
    on a pure single-process runner: ``set_session_vars`` also latches
    ``_session_context_engaged`` (see above), which switches the subprocess
    env bridge from "os.environ fallback" to "ContextVar-authoritative, strip on
    _UNSET" in ``tools/environments/local.py``. A one-shot CLI that never engages
    the session-context system must not flip that latch as a side effect of
    declaring a capability.

    This is for runners with NO gateway drain loop behind them at all — a
    finished process has nothing to self-post through, unlike the api_server
    adapter (push=False, wake=True — see ``wake_delivery_supported``), so both
    flags go False here.

    Callers that already build a full session context (cron's ``run_job``) get
    the same state by passing ``async_delivery=False, wake_delivery=False`` to
    ``set_session_vars``.

    A session that cannot take a late completion makes ``delegate_task`` fall
    through to its existing inline/synchronous path, so subagent results are
    returned within the turn instead of being dispatched to a channel that will
    never deliver them.

    See NousResearch/hermes-agent#53027 and #63142.
    """
    _SESSION_ASYNC_DELIVERY.set(False)
    _SESSION_WAKE_DELIVERY.set(False)


def async_delivery_supported() -> bool:
    """Whether the current session can deliver a background completion later.  False for
    stateless channels (:func:`declare_stateless_channel`) and Kanban workers
    (``HERMES_KANBAN_TASK``: one-shot subprocesses whose parent disappears after the turn)."""
    if os.environ.get("HERMES_KANBAN_TASK"):
        return False
    value = _SESSION_ASYNC_DELIVERY.get()
    return True if value is _UNSET else bool(value)


def wake_delivery_supported() -> bool:
    """Whether the current session's channel can WAKE the real session later.

    Distinct from :func:`async_delivery_supported` (push). API server is
    push=False/wake=True. Kanban workers and ``hermes -z`` are both False.
    """
    import os

    if os.environ.get("HERMES_KANBAN_TASK"):
        return False

    value = _SESSION_WAKE_DELIVERY.get()
    if value is _UNSET:
        return True
    return bool(value)
