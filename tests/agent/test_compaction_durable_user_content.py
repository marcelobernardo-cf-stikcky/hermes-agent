"""Compaction must persist the current-turn user row in its durable (``@image:``) form, not the model-only
image hint — otherwise the Desktop shows the question twice / out of order after an in-place compaction."""
from types import SimpleNamespace

from agent.conversation_compression import _durable_current_user_content

HINT = "[The user attached an image: a.png]\n[Examine it with the vision_analyze tool using image_url: C:/a.png]\n\noi"
CLEAN = "oi\n@image:C:/a.png"


def test_compacted_insert_sees_durable_user_content_and_live_dict_is_restored():
    live = {"role": "user", "content": HINT}
    carried = {"role": "user", "content": HINT}
    agent = SimpleNamespace(_persist_user_message_idx=1, _persist_user_message_override=CLEAN)
    with _durable_current_user_content(agent, [{"role": "system", "content": "s"}, live], [carried]):
        assert carried["content"] == CLEAN
        assert carried["api_content"] == HINT  # sent bytes kept as the replay sidecar
    assert carried == {"role": "user", "content": HINT}


def test_no_override_is_a_noop():
    carried = {"role": "user", "content": "x"}
    agent = SimpleNamespace(_persist_user_message_idx=0, _persist_user_message_override=None)
    with _durable_current_user_content(agent, [{"role": "user", "content": "x"}], [carried]):
        assert carried == {"role": "user", "content": "x"}
