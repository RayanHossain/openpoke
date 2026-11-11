"""Self-test for routing_summary: proves the 'after the fix' output says what we promise.

Runs a tiny throwaway suite in-process (pytester) with one bug that still
reproduces (F1), one that no longer does (F2), a control and a harness check.
"""

from __future__ import annotations

FAKE_SUITE = '''
import pytest

@pytest.mark.finding("F1")
def test_bug_still_present():
    pass

@pytest.mark.finding("F2")
def test_bug_fixed_so_characterization_fails():
    assert False

@pytest.mark.finding("F2")
@pytest.mark.control
def test_control():
    pass

@pytest.mark.harness
def test_harness():
    pass
'''


def test_summary_reports_reproduced_changed_and_counts(pytester):
    pytester.makeconftest("from routing_summary import pytest_configure  # noqa: F401\n")
    pytester.makepyfile(test_fake=FAKE_SUITE)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:asyncio")

    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines([
        "*Results by problem (FIXED = no longer happens; still present = known issue, left as is)*",
        "F1: * still present (known issue)  1/1",  # unmapped IDs fall back to the ID itself
        "F2: * CHANGED, check # FLIPS lines (fixed?)  0/1 still reproduce",
        "controls OK 1/1 | harness OK 1/1",
        "Known issues * 2 *",
        "Controls * 1 *",
        "Harness checks * 1 *",
    ])


def test_summary_flags_broken_harness(pytester):
    pytester.makeconftest("from routing_summary import pytest_configure  # noqa: F401\n")
    pytester.makepyfile(test_fake='''
import pytest

@pytest.mark.harness
def test_harness():
    assert False

@pytest.mark.finding("F1")
def test_bug():
    pass
''')
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:asyncio")
    result.stdout.fnmatch_lines([
        "HARNESS BROKEN (1/1 failed): results below are not trustworthy",
        "F1: * still present (known issue)  1/1",
        "*harness FAILED 0/1",
    ])


def test_summary_reports_fixed_and_regressed(pytester):
    pytester.makeconftest("from routing_summary import pytest_configure  # noqa: F401\n")
    pytester.makepyfile(test_fake='''
import pytest

@pytest.mark.fixed("F3")
def test_fix_holds():
    pass

@pytest.mark.fixed("F4")
def test_fix_regressed():
    assert False

@pytest.mark.finding("F4")
def test_other_part_of_f4_still_reproduces():
    pass
''')
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:asyncio")
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines([
        "reworded follow-ups: * still present (known issue)  1/1",  # F4 is printed by its plain name
        "F3: * FIXED  1/1",
        "reworded follow-ups: * FIX REGRESSED  0/1 hold",
        "Known issues * 1 *",
        "Fixed problems * 2 *",
    ])
