"""Compaction that rewrites a user row's text (two queued messages merged into one) must keep the row at
its original display position — not move it after later answers (seen live as "Confirmo\\n\\nConfirmo")."""
from hermes_state import SessionDB


def test_merged_user_row_keeps_display_origin(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    sid = "merged-user"
    db.create_session(sid, source="desktop")
    db.append_messages_batch(sid, [
        {"role": "user", "content": "start", "timestamp": 100.0},
        {"role": "assistant", "content": "first answer", "timestamp": 101.0},
        {"role": "user", "content": "Confirmo", "timestamp": 102.0},
        {"role": "assistant", "content": "later answer", "timestamp": 200.0},
    ])
    history = db.get_messages_as_conversation(sid, include_row_ids=True)
    history[2]["content"] = "Confirmo\n\nConfirmo"
    db.archive_and_compact(sid, history)
    visible = db.get_messages_as_conversation(sid, include_row_ids=True, include_compacted=True)
    assert [m["content"] for m in visible] == ["start", "first answer", "Confirmo\n\nConfirmo", "later answer"]
