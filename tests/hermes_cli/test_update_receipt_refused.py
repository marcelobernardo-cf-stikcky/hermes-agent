"""RED->GREEN: recibo de update RECUSADO nao pode alegar runtime obsoleto.

Cenario real (2026-09-01): `hermes update` saiu com exit_code=2 antes de puxar
qualquer codigo (steps=[], pre_update.sha == post_update.sha). O recibo ficou
"unfinished", entao `plan.runtimes[].code_sha` — capturado ANTES do pull, 6 dias
atras — foi comparado com o checkout de hoje e disparou, para sempre, o aviso
"pulled new code but did not restart running gateways".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_cli.update_cmd_fleet import _receipt_reports_stale_runtime  # noqa: E402

CUR = "6a05f76c2b3c8db143c81f98c1882cbcbf3bd014"
OLD = "4f22543509d1b91dc45bcb369447126c5eb14fb7"


def _receipt(**over):
    base = {
        "outcome": "refused",
        "exit_code": 2,
        "stop_reason": "sys.exit(2)",
        "steps": [],
        "pre_update": {"sha": OLD},
        "post_update": {"sha": OLD},
        "plan": {"runtimes": [{"profile": "default", "code_sha": OLD}]},
        "fleet": [],
    }
    base.update(over)
    return base


def _patched(monkeypatch_target, receipt):
    import hermes_cli.update_cmd_fleet as m
    orig = m.read_latest_receipt if hasattr(m, "read_latest_receipt") else None
    import hermes_cli.update_receipt as ur
    saved = ur.read_latest_receipt
    ur.read_latest_receipt = lambda: receipt
    try:
        return _receipt_reports_stale_runtime(CUR)
    finally:
        ur.read_latest_receipt = saved
        if orig is not None:
            m.read_latest_receipt = orig


def main() -> int:
    fails = []

    # 1. O bug real: update recusado, nada puxado -> NAO e runtime obsoleto.
    if _patched(None, _receipt()):
        fails.append("update recusado (pre==post, steps=[]) alegou runtime obsoleto")

    # 2. Update que REALMENTE puxou codigo e nao reiniciou -> continua avisando.
    real = _receipt(outcome="partial", exit_code=0, stop_reason=None,
                    steps=[{"name": "pull"}],
                    pre_update={"sha": OLD}, post_update={"sha": CUR})
    if not _patched(None, real):
        fails.append("update que puxou codigo (pre!=post) deixou de avisar - regressao")

    # 3. Matriz fleet explicitamente stale continua mandando, sem depender do plan.
    fl = _receipt(fleet=[{"profile": "default", "state": "stale", "code_sha": OLD}])
    if not _patched(None, fl):
        fails.append("fleet marcada stale deixou de avisar - regressao")

    # 4. Frota no SHA certo nao avisa.
    ok = _receipt(fleet=[{"profile": "default", "state": "ok", "code_sha": CUR}])
    if _patched(None, ok):
        fails.append("frota no SHA correto avisou a toa")

    for f in fails:
        print("FAIL:", f)
    print("UPDATE_RECEIPT_SELFCHECK=" + ("PASS" if not fails else "FAIL"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
