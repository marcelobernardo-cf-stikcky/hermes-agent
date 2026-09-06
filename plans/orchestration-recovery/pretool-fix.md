# pre_tool_call concorrente — correção mínima

## Diagnóstico

`PluginDispatchMixin._run_hook_callback_bounded` tratava callback `(hook_name, id(cb))` já em execução como `_HOOK_SKIPPED` imediatamente. Para `pre_tool_call`, isso virava bloqueio fail-closed mesmo quando a primeira chamada era saudável; o payload da segunda chamada nunca chegava ao callback.

## Correção

- Adicionada uma `threading.Lock` por callback no `PluginManager`.
- Chamadas concorrentes aguardam até o deadline original (`timeout` total inclui a espera); depois executam seu próprio callback/payload.
- Se o holder excede o deadline, a chamada que aguarda falha fechada sem criar worker adicional.
- O cooldown de timeout real continua sendo aplicado; o lock permanece ocupado até o worker abandonado terminar.
- Não há chave por sessão nem mudança de produção/configuração.

A serialização é deliberadamente por callback e global ao manager. Reentrância do mesmo callback em outro worker não é bypassada: ela consome o orçamento e falha fechada, preservando a política de segurança. Não foi observado caller que exija reentrância neste módulo.

## RED/GREEN

RED antes da alteração: o teste concorrente saudável falhou porque a segunda chamada retornou `_HOOK_SKIPPED`/diretiva de bloqueio em vez de executar seu payload.

GREEN:

- `python -m pytest tests/hermes_cli/test_plugins.py -k 'concurrent_calls_wait_and_keep_payloads or concurrent_timeout_stays_fail_closed' -q`: 2 passed
- `python -m pytest tests/hermes_cli/test_plugins.py -q`: 78 passed
- `HERMES_PYTHON='C:/Users/Usuario/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe' ./scripts/run_tests.sh tests/hermes_cli/test_plugins.py -q`: 78 passed

## Commit/escopo

Worktree isolada: `C:/Users/Usuario/.hermes-worktrees/pretool-fix-20260906`.
Sem ativar, reiniciar, publicar ou alterar produção.
