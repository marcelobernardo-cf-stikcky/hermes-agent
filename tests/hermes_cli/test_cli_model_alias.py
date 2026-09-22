"""``hermes --cli -m <alias>`` must resolve config.yaml ``model_aliases:``.

Regression for the Kanban dispatcher spawn path (``hermes -p X --cli -m sonnet``):
the literal alias reached Anthropic as ``model: sonnet`` (HTTP 404), the run
fell to the slowest fallback and the card's routing intent was lost.
Measured 2026-09-05, board choker-os, t_61c7b91c run 197.
"""

import importlib
import sys
import types

import pytest

from hermes_cli import model_switch as ms


@pytest.fixture
def cli_mod():
    for name in list(sys.modules):
        if name in ("cli", "run_agent", "tools") or name.startswith("tools."):
            sys.modules.pop(name, None)
    sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
    yield importlib.import_module("cli")


@pytest.fixture
def sonnet_alias(monkeypatch):
    # _ensure_direct_aliases() keeps a caller-seeded dict untouched.
    monkeypatch.setitem(
        ms.DIRECT_ALIASES, "sonnet",
        ms.DirectAlias(model="claude-sonnet-5", provider="anthropic", base_url=""),
    )


def test_dash_m_alias_resolves_model_and_provider(cli_mod, sonnet_alias):
    shell = cli_mod.HermesCLI(model="sonnet", compact=True, max_turns=1)
    assert shell.model == "claude-sonnet-5"
    assert shell.requested_provider == "anthropic"
    assert shell._explicit_model_override is True


def test_explicit_provider_wins_over_alias(cli_mod, sonnet_alias):
    shell = cli_mod.HermesCLI(model="sonnet", provider="xai-oauth", compact=True, max_turns=1)
    # Native contract (model_switch.resolve_startup_model_route): an explicit
    # --provider wins over the alias LABEL; the alias still contributes model
    # + base_url. So the model IS resolved, only the provider is preserved.
    assert shell.requested_provider == "xai-oauth"
    assert shell.model == "claude-sonnet-5"


def test_non_alias_model_untouched(cli_mod, sonnet_alias):
    shell = cli_mod.HermesCLI(model="grok-4.6", compact=True, max_turns=1)
    assert shell.model == "grok-4.6"
