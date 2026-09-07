"""Notifier descarta wake de run superada, sem perder evento legitimo.

Regressao de t_26d772ef: o notifier entregou `gave_up`/`crashed` da run 155
depois da run 156 ja estar viva, acordando o orquestrador com resultado de uma
tentativa morta. Os tres casos de borda abaixo sao o que separa o filtro
correto de um que engole notificacao boa.
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _fresh_board(tmp_path):
    os.environ["HERMES_HOME"] = str(tmp_path)
    os.environ["HERMES_KANBAN_DB"] = str(tmp_path / "kanban.db")
    from hermes_cli import kanban_db as kb
    kb.init_db(tmp_path / "kanban.db")
    return kb


def _claimed_task(kb, conn, title):
    task_id = kb.create_task(conn, title=title, assignee="rigger")
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    conn.commit()
    kb.claim_task(conn, task_id)
    run_id = int(conn.execute(
        "SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
    return task_id, run_id


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="notify-superseded-"))
    kb = _fresh_board(tmp)
    from hermes_cli.kanban_db_connect import connect_closing

    failures = []
    with connect_closing() as conn:
        # 1. evento de run superada nao acorda ninguem; cursor avanca por cima dele
        tid, run_old = _claimed_task(kb, conn, "superseded run")
        kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id="c1")
        cur = conn.execute(
            "INSERT INTO task_runs (task_id, profile, status, started_at) VALUES (?,?,?,?)",
            (tid, "rigger", "running", 9e9))
        run_new = int(cur.lastrowid)
        conn.execute("UPDATE tasks SET current_run_id=?, status='running' WHERE id=?",
                     (run_new, tid))
        conn.commit()
        kb._append_event(conn, tid, "gave_up", {"n": 1}, run_id=run_old)
        kb._append_event(conn, tid, "done", {"n": 2}, run_id=run_new)

        _, cursor, evs = kb.claim_unseen_events_for_sub(
            conn, task_id=tid, platform="api_server", chat_id="c1")
        kinds = {e.kind for e in evs}
        last = int(conn.execute(
            "SELECT id FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()[0])
        if "gave_up" in kinds:
            failures.append("entregou evento de run superada")
        if "done" not in kinds:
            failures.append("perdeu o evento da run atual")
        if cursor != last:
            failures.append(f"cursor parou em {cursor}, ultimo evento e {last}")

        # 2. task concluida (current_run_id NULL) ainda entrega o wake final
        tid2, run2 = _claimed_task(kb, conn, "done clears run")
        kb.add_notify_sub(conn, task_id=tid2, platform="api_server", chat_id="c2")
        conn.execute("UPDATE tasks SET status='done', current_run_id=NULL WHERE id=?", (tid2,))
        conn.commit()
        kb._append_event(conn, tid2, "done", {"final": True}, run_id=run2)
        _, _, evs2 = kb.claim_unseen_events_for_sub(
            conn, task_id=tid2, platform="api_server", chat_id="c2")
        if "done" not in {e.kind for e in evs2}:
            failures.append("wake final perdido quando current_run_id e NULL")

        # 3. evento sem run_id (created/commented) nunca e descartado
        tid3 = kb.create_task(conn, title="no run id", assignee="rigger")
        kb.add_notify_sub(conn, task_id=tid3, platform="api_server", chat_id="c3")
        kb._append_event(conn, tid3, "commented", {"x": 1}, run_id=None)
        conn.execute("UPDATE tasks SET current_run_id=99 WHERE id=?", (tid3,))
        conn.commit()
        _, _, evs3 = kb.claim_unseen_events_for_sub(
            conn, task_id=tid3, platform="api_server", chat_id="c3")
        if "commented" not in {e.kind for e in evs3}:
            failures.append("evento sem run_id foi descartado")

    for f in failures:
        print("FAIL:", f)
    print("NOTIFY_SUPERSEDED_SELFCHECK=" + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
