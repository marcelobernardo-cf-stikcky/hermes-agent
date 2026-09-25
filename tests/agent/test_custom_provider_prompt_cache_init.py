"""Regression for custom-provider prompt-cache capability during AIAgent construction."""

import json

from run_agent import AIAgent


def test_custom_provider_prompt_cache_is_resolved_after_route_config_load(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        json.dumps(
            {
                "model": {
                    "default": "claude-cache-qa",
                    "provider": "custom:cache-qa",
                },
                "prompt_caching": {"cache_ttl": "5m"},
                "custom_providers": [
                    {
                        "name": "cache-qa",
                        "base_url": "https://cache-qa.invalid/v1",
                        "models": {
                            "claude-cache-qa": {
                                "prompt_caching": True,
                                "context_length": 200_000,
                            }
                        },
                    }
                ],
                "compression": {"enabled": False},
                "toolsets": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    agent = AIAgent(
        model="claude-cache-qa",
        provider="custom:cache-qa",
        base_url="https://cache-qa.invalid/v1",
        api_key="test-key",
        api_mode="chat_completions",
        enabled_toolsets=[],
        skip_context_files=True,
        skip_memory=True,
        skip_background_review=True,
        quiet_mode=True,
        reasoning_config={"enabled": True, "effort": "low"},
    )
    try:
        assert agent._use_prompt_caching is True
        assert agent._use_native_cache_layout is False
    finally:
        agent.close()
