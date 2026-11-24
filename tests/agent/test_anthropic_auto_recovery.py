"""Automatic Anthropic login recovery: adopt a live local Claude Code login instead of dying
with "No Anthropic credentials found" whenever one exists on the machine."""

import json
import time

import pytest

from agent import anthropic_credentials as ac


def _store(path, access, refresh, expires_in_s):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": access, "refreshToken": refresh,
        "expiresAt": int(time.time() * 1000) + int(expires_in_s * 1000),
        "scopes": ["user:inference"],
    }}), encoding="utf-8")
    return path


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """Isolated credential layout: shared ~/.claude file + <home>/claude-accounts/*, no env overrides."""
    home = tmp_path / "hermes"
    home.mkdir()
    shared = tmp_path / "dotclaude" / ".credentials.json"
    monkeypatch.setattr(ac, "claude_code_credentials_path", lambda: shared)
    monkeypatch.setattr(ac, "get_hermes_home", lambda: home)
    monkeypatch.setattr(ac, "_get_hermes_oauth_file", lambda: home / ".anthropic_oauth.json")
    monkeypatch.setattr(ac, "_first_env", lambda *names: "")
    monkeypatch.setattr(ac, "_auto_recovery_last_attempt", 0.0)
    monkeypatch.setattr("agent.credential_sources.adopt_external_logins_enabled", lambda: True)
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda *a, **kw: home)
    return shared, home


def _hermes_store(home):
    path = home / ".anthropic_oauth.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_live_login_in_switcher_account_is_adopted(layout, monkeypatch):
    shared, home = layout
    _store(shared, "dead-access", "dead-refresh", -3600)
    live = _store(home / "claude-accounts" / "account-3" / ".credentials.json", "live-access", "live-refresh", 3600)

    def _no_refresh(*a, **kw):
        raise ac.AnthropicOAuthError(400, "invalid_grant", "Refresh token expired", what="refresh")

    monkeypatch.setattr(ac, "refresh_anthropic_oauth_pure", _no_refresh)

    assert live in ac.local_claude_credential_stores()
    adopted = ac.adopt_local_claude_login()

    assert adopted["accessToken"] == "live-access"
    assert _hermes_store(home)["accessToken"] == "live-access"


def test_expired_store_is_refreshed_and_rotated_in_place(layout, monkeypatch):
    shared, home = layout
    _store(shared, "old-access", "old-refresh", -60)

    def _refresh(refresh_token, *, use_json=False):
        assert refresh_token == "old-refresh"
        return {"access_token": "new-access", "refresh_token": "new-refresh",
                "expires_at_ms": int(time.time() * 1000) + 3600_000}

    monkeypatch.setattr(ac, "refresh_anthropic_oauth_pure", _refresh)

    adopted = ac.adopt_local_claude_login()

    assert adopted["accessToken"] == "new-access"
    rotated = json.loads(shared.read_text(encoding="utf-8"))["claudeAiOauth"]
    assert rotated["accessToken"] == "new-access"          # the CLI that owns the store keeps working
    assert rotated["scopes"] == ["user:inference"]         # metadata preserved
    assert _hermes_store(home)["refreshToken"] == "new-refresh"


def test_dead_grants_are_skipped_without_a_login(layout, monkeypatch):
    shared, home = layout
    _store(shared, "dead-access", "dead-refresh", -3600)

    def _dead(*a, **kw):
        raise ac.AnthropicOAuthError(400, "invalid_grant", "Refresh token expired", what="refresh")

    monkeypatch.setattr(ac, "refresh_anthropic_oauth_pure", _dead)

    assert ac.adopt_local_claude_login() is None
    assert _hermes_store(home) is None


def test_recovery_attempts_at_most_once_per_cooldown(layout, monkeypatch):
    calls = []
    monkeypatch.setattr(ac, "adopt_local_claude_login", lambda: calls.append(1))

    assert ac.auto_recover_anthropic_login() is None
    assert ac.auto_recover_anthropic_login() is None
    assert len(calls) == 1


def test_recovery_skipped_when_an_explicit_credential_exists(layout, monkeypatch):
    calls = []
    monkeypatch.setattr(ac, "_first_env", lambda *names: "sk-ant-oat-static")
    monkeypatch.setattr(ac, "adopt_local_claude_login", lambda: calls.append(1))

    assert ac.auto_recover_anthropic_login() is None
    assert calls == []


def test_resolver_recovers_before_raising(layout, monkeypatch):
    from hermes_cli import runtime_provider as rp

    state = {"recovered": False, "token": None}

    def _resolve(model=None):
        if not model:
            return None
        return "recovered-token" if state["recovered"] else None

    def _recover():
        state["recovered"] = True
        return "recovered-token"

    monkeypatch.setattr(ac, "resolve_anthropic_token", _resolve)
    monkeypatch.setattr(ac, "auto_recover_anthropic_login", _recover)

    assert rp._anthropic_token_or_raise(model="claude-opus-5-5") == "recovered-token"


def test_resolver_does_not_recover_a_model_level_cooldown(layout, monkeypatch):
    from hermes_cli import runtime_provider as rp
    from hermes_cli.auth_constants import AuthError

    # Benched for this model only: a model-free resolve still finds a token.
    monkeypatch.setattr(ac, "resolve_anthropic_token",
                        lambda model=None, **kw: None if model else "benched-for-this-model")

    def _must_not_run():
        raise AssertionError("recovery must not run for a model-level cooldown")

    monkeypatch.setattr(ac, "auto_recover_anthropic_login", _must_not_run)

    with pytest.raises(AuthError, match="rate-limited"):
        rp._anthropic_token_or_raise(model="claude-opus-5-5")
