"""Invariant tests for the urgency classifier's score parsing.

Contract: a classifier response carrying a usable urgency judgement must reach the threshold
filter. A dropped score is indistinguishable from "nothing is urgent", which turns a broken
monitor into a permanently quiet one.
"""

from __future__ import annotations

import json

from cron.scripts.classify_items import _build_prompt, _parse_scores


def _one(**obj) -> str:
    return json.dumps([{"index": 0, **obj}])


def test_canonical_score_keys_are_parsed():
    scores = _parse_scores(_one(score=9, reason="deadline today"), 1)
    assert scores[0]["score"] == 9
    assert scores[0]["reason"] == "deadline today"


def test_alternate_key_names_still_yield_a_score():
    # Models routinely answer with urgency_score/reasoning; that judgement must not be lost.
    scores = _parse_scores(_one(urgency_score=10, reasoning="from manager"), 1)
    assert scores[0]["score"] == 10
    assert scores[0]["reason"] == "from manager"


def test_numeric_score_shapes_are_coerced():
    assert _parse_scores(_one(score="8"), 1)[0]["score"] == 8
    assert _parse_scores(_one(score=7.6), 1)[0]["score"] == 8
    assert _parse_scores(_one(score="high"), 1)[0]["score"] is None


def test_fenced_output_is_tolerated():
    assert _parse_scores(f"```json\n{_one(score=8, reason='x')}\n```", 1)[0]["score"] == 8


def test_out_of_range_index_is_ignored():
    assert _parse_scores(json.dumps([{"index": 5, "score": 10}]), 1) == {}


def test_unparseable_output_yields_no_scores():
    assert _parse_scores("I could not decide.", 2) == {}


def test_prompt_states_the_key_names_it_parses():
    # Prompt and parser are two halves of one contract: the schema the prompt asks for must
    # be a schema the parser accepts.
    prompt = _build_prompt([{"subject": "hi"}], "Urgent if it mentions a deadline.")
    assert all(key in prompt for key in ("index", "score", "reason"))
