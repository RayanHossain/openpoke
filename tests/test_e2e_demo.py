"""Keeps tests/demo_alice_e2e.py honest: same scenario, asserted (F4 end to end)."""

from __future__ import annotations

import pytest

import demo_alice_e2e as demo


@pytest.mark.finding("F4")
def test_F4_e2e_paraphrased_route_loses_thread(tmp_path):
    trace = demo.Trace(out=lambda _line: None)
    r = demo.run_scenario("Alice lunch email", tmp_path / "a", trace)
    assert r["roster"] == ["Email to Alice", "Alice lunch email"]  # FLIPS
    assert r["first_log_has_thread"] is True
    assert r["second_prompt_has_thread"] is False  # FLIPS
    assert r["final_reply"].startswith("I have no record")  # FLIPS


@pytest.mark.finding("F4")
@pytest.mark.control
def test_e2e_control_exact_name_keeps_thread(tmp_path):
    trace = demo.Trace(out=lambda _line: None)
    r = demo.run_scenario("Email to Alice", tmp_path / "b", trace)
    assert r["roster"] == ["Email to Alice"]
    assert r["second_prompt_has_thread"] is True
    assert "thread-alice-1" in r["final_reply"]
