"""Keeps tests/demo_alice_e2e.py honest: same scenario, asserted (F4 end to end).

F4 FIXED by D-004 step 6: a paraphrased name written where an agent_id belongs
is rejected, and the follow-up reaches the agent that holds the thread.
"""

from __future__ import annotations

import pytest

import demo_alice_e2e as demo


@pytest.mark.fixed("F4")
def test_F4_e2e_paraphrased_route_is_rejected_and_thread_is_found(tmp_path):
    trace = demo.Trace(out=lambda _line: None)
    r = demo.run_scenario("Alice lunch email", tmp_path / "a", trace)
    assert r["roster"] == ["Email to Alice"]
    assert r["first_log_has_thread"] is True
    assert r["second_prompt_has_thread"] is True
    assert "thread-alice-1" in r["final_reply"]
    assert any("rejected: Unknown agent_id 'Alice lunch email'" in line for line in trace.lines)


@pytest.mark.finding("F4")
@pytest.mark.control
def test_e2e_control_card_route_keeps_thread(tmp_path):
    trace = demo.Trace(out=lambda _line: None)
    r = demo.run_scenario(None, tmp_path / "b", trace)
    assert r["roster"] == ["Email to Alice"]
    assert r["second_prompt_has_thread"] is True
    assert "thread-alice-1" in r["final_reply"]
