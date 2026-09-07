from types import SimpleNamespace

import pytest

from gateway.kanban_watchers_notifier import MAX_SEND_FAILURES, _KanbanNotification


def _notification():
    runner = SimpleNamespace(_kanban_sub_fail_counts={})
    notification = _KanbanNotification.__new__(_KanbanNotification)
    notification.runner = runner
    notification.sub = {"task_id": "task-1", "platform": "telegram", "chat_id": "chat-1"}
    notification.board_slug = "board"
    notification.sub_key = ("task-1", "telegram", "chat-1", "")
    notification.sub_fail_counts = runner._kanban_sub_fail_counts
    notification.task_id = "task-1"
    notification.platform_str = "telegram"
    return notification


@pytest.mark.asyncio
async def test_twelve_failures_mark_once_and_unsubscribe(monkeypatch):
    notification = _notification()
    marked = []
    unsubscribed = []
    async def record_drop(failures):
        marked.append(failures)

    async def unsub():
        unsubscribed.append(True)

    notification.record_drop = record_drop
    notification.unsub = unsub

    async def rewind():
        return None

    notification.rewind = rewind

    for _ in range(MAX_SEND_FAILURES):
        await notification.delivery_failed("failure %s/%s: %s", (), "drop %s %s %s", RuntimeError("x"), False)

    assert marked == [MAX_SEND_FAILURES]
    assert unsubscribed == [True]
    assert notification.sub_fail_counts == {}


@pytest.mark.asyncio
async def test_eleven_failures_only_rewind(monkeypatch):
    notification = _notification()
    marked = []
    rewound = []
    async def record_drop(failures):
        marked.append(failures)

    async def rewind():
        rewound.append(True)

    notification.record_drop = record_drop
    notification.rewind = rewind

    for _ in range(MAX_SEND_FAILURES - 1):
        await notification.delivery_failed("failure %s/%s: %s", (), "drop %s %s %s", RuntimeError("x"), False)

    assert marked == []
    assert len(rewound) == MAX_SEND_FAILURES - 1


@pytest.mark.asyncio
async def test_unsubscribe_and_counter_cleanup_survive_mark_failure():
    notification = _notification()
    unsubscribed = []

    async def fail_mark(failures):
        raise RuntimeError("board unavailable")

    async def unsubscribe():
        unsubscribed.append(True)

    notification.record_drop = fail_mark
    notification.unsub = unsubscribe
    notification.sub_fail_counts[notification.sub_key] = MAX_SEND_FAILURES - 1

    await notification.delivery_failed("failure %s/%s: %s", (), "drop %s %s %s", RuntimeError("x"), False)

    assert unsubscribed == [True]
    assert notification.sub_fail_counts == {}