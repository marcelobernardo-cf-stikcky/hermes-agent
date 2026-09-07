"""Notifier descarta wake de run superada, sem perder evento legitimo.

Regressao de t_26d772ef: o notifier entregou `gave_up`/`crashed` da run 155
depois da run 156 ja estar viva, acordando o orquestrador com o resultado de
uma tentativa morta. Os dois ramos de escape (task concluida, evento sem run)
sao o que separa o filtro correto de um que engole notificacao boa.
"""
import pytest


@pytest.fixture()
def board(tmp_path, monkeypatch):
    """Board isolado; devolve (kanban_db, conn) ja inicializados."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    kb.init_db(tmp_path / "kanban.db")
    with connect_closing() as conn:
        yield kb, conn


def _claim(kb, conn, title):
    task_id = kb.create_task(conn, title=title, assignee="rigger")
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    conn.commit()
    kb.claim_task(conn, task_id)
    run_id = conn.execute(
        "SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    return task_id, int(run_id)


def _drain(kb, conn, task_id, chat_id):
    cursor, events = kb.claim_unseen_events_for_sub(
        conn, task_id=task_id, platform="api_server", chat_id=chat_id)[1:]
    return cursor, {e.kind for e in events}


def test_superseded_run_is_dropped_and_cursor_still_advances(board):
    kb, conn = board
    task_id, stale_run = _claim(kb, conn, "superseded run")
    kb.add_notify_sub(conn, task_id=task_id, platform="api_server", chat_id="c1")

    live_run = int(conn.execute(
        "INSERT INTO task_runs (task_id, profile, status, started_at) VALUES (?,?,?,?)",
        (task_id, "rigger", "running", 9e9)).lastrowid)
    conn.execute("UPDATE tasks SET current_run_id=?, status='running' WHERE id=?",
                 (live_run, task_id))
    conn.commit()
    kb._append_event(conn, task_id, "gave_up", {"n": 1}, run_id=stale_run)
    kb._append_event(conn, task_id, "done", {"n": 2}, run_id=live_run)

    cursor, kinds = _drain(kb, conn, task_id, "c1")
    last = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id=?",
                        (task_id,)).fetchone()[0]

    assert "gave_up" not in kinds, "wake de run superada acordou o orquestrador"
    assert "done" in kinds, "perdeu o evento da run atual"
    # cursor anda por cima do evento descartado, senao ele e reavaliado para sempre
    assert cursor == last


def test_finished_task_still_delivers_its_last_wake(board):
    """`current_run_id` NULL nao pode virar filtro: e o estado de quem terminou."""
    kb, conn = board
    task_id, run_id = _claim(kb, conn, "done clears run")
    kb.add_notify_sub(conn, task_id=task_id, platform="api_server", chat_id="c2")
    conn.execute("UPDATE tasks SET status='done', current_run_id=NULL WHERE id=?",
                 (task_id,))
    conn.commit()
    kb._append_event(conn, task_id, "done", {"final": True}, run_id=run_id)

    assert "done" in _drain(kb, conn, task_id, "c2")[1]


def test_event_without_run_id_is_never_filtered(board):
    """`created`/`commented` nascem sem run e valem para qualquer tentativa."""
    kb, conn = board
    task_id = kb.create_task(conn, title="no run id", assignee="rigger")
    kb.add_notify_sub(conn, task_id=task_id, platform="api_server", chat_id="c3")
    kb._append_event(conn, task_id, "commented", {"x": 1}, run_id=None)
    conn.execute("UPDATE tasks SET current_run_id=99 WHERE id=?", (task_id,))
    conn.commit()

    assert "commented" in _drain(kb, conn, task_id, "c3")[1]
