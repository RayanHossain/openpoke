"""End-of-run summary for the characterization suite.

In this suite a PASS on a bug test means the bug is still present, so the plain
"N passed" line is easy to misread. This plugin prints, per finding, whether the
bug reproduced, plus whether the controls and harness checks held.

Roles come from markers:
  @pytest.mark.harness      harness sanity (isolation, stubs)
  @pytest.mark.control      control/context: mechanism works when the cause is removed
  @pytest.mark.finding(id)  anything else with a finding marker = bug reproduction
  @pytest.mark.fixed(id)    a finding whose fix landed: its # FLIPS lines were inverted
                            into spec assertions, so a PASS means the fix holds
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Plain names printed in the summary instead of finding IDs.
# The same table, with what each problem was, is in docs/tests-detailed.md.
PLAIN_NAMES: Dict[str, str] = {
    "F4": "reworded follow-ups",
    "F7": "similar names sharing memory",
    "F33": "reminders only reachable by their creator",
    "F33-residual": "vague follow-ups with many agents",
    "F23": "Gmail actions logged apart from the agent",
    "F31": "timeout leaves no final answer in the log",
    "L22": "timeout reported as failed after sending",
    "F34": "reminder firings missing from agent history",
    "F37": "slow Gmail call blocks other agents",
    "log-roundtrip": "agent log alters line breaks",
}


def plain(fid: str) -> str:
    return PLAIN_NAMES.get(fid, fid)


ROLE_ROWS = [
    ("bug", "Known issues", "the old behaviour still happens (left as is on purpose)"),
    ("control", "Controls", "the normal path works when the cause is removed"),
    ("harness", "Harness checks", "the test setup is isolated and its stand-ins behave"),
    ("fixed", "Fixed problems", "the fix holds"),
]


def _fid_sort_key(fid: str) -> Tuple[int, int, str]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", fid)
    if m:
        return (0 if m.group(1).upper() == "F" else 1, int(m.group(2)), fid)
    return (2, 0, fid)


class RoutingSummary:
    def __init__(self) -> None:
        self.meta: Dict[str, Tuple[str, List[str]]] = {}   # nodeid -> (role, fids)
        self.outcome: Dict[str, Optional[bool]] = {}       # True pass, False fail, None skip

    # Only selected (run) items reach setup, so -k / -m filtering is respected.
    def pytest_runtest_setup(self, item) -> None:
        fids = []
        markers = list(item.iter_markers("fixed")) or list(item.iter_markers("finding"))
        for mark in markers:
            fid = str(mark.args[0]) if mark.args else "?"
            if fid not in fids:
                fids.append(fid)
        if item.get_closest_marker("harness"):
            role = "harness"
        elif item.get_closest_marker("fixed"):
            role = "fixed"
        elif item.get_closest_marker("control"):
            role = "control"
        elif fids:
            role = "bug"
        else:
            role = "other"
        self.meta[item.nodeid] = (role, sorted(fids, key=_fid_sort_key))

    def pytest_runtest_logreport(self, report) -> None:
        if report.failed:
            self.outcome[report.nodeid] = False
        elif report.skipped:
            self.outcome.setdefault(report.nodeid, None)
        elif report.when == "call" and report.nodeid not in self.outcome:
            self.outcome[report.nodeid] = True

    def _ids(self, role: str) -> List[str]:
        return [n for n, (r, _) in self.meta.items() if r == role and n in self.outcome]

    def _tally(self, nodeids: List[str]) -> Tuple[int, int, int]:
        ran = [n for n in nodeids if self.outcome[n] is not None]
        passed = sum(1 for n in ran if self.outcome[n])
        return passed, len(ran) - passed, len(ran)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if not self.meta:
            return
        tr = terminalreporter
        tr.write_sep("=", "Results by problem (FIXED = no longer happens; still present = known issue, left as is)")

        h_pass, h_fail, h_total = self._tally(self._ids("harness"))
        if h_fail:
            tr.write_line(f"HARNESS BROKEN ({h_fail}/{h_total} failed): results below are not trustworthy", red=True)

        bug_ids = self._ids("bug")
        all_fids = sorted({f for n in bug_ids for f in self.meta[n][1]}, key=_fid_sort_key)
        fixed_fids = {f for n in self._ids("fixed") for f in self.meta[n][1]}
        width = max([len(plain(f)) + 1 for f in [*all_fids, *fixed_fids]] + [6])
        for fid in all_fids:
            tagged = [n for n in bug_ids if fid in self.meta[n][1]]
            passed, failed, total = self._tally(tagged)
            others = sorted({f for n in tagged for f in self.meta[n][1] if f != fid}, key=_fid_sort_key)
            cross = [n for n in tagged if len(self.meta[n][1]) > 1]
            note = f"   ({len(cross)} also counted under: {', '.join(plain(o) for o in others)})" if cross else ""
            label = f"{plain(fid)}:"
            if failed:
                status = f"CHANGED, check # FLIPS lines (fixed?)  {passed}/{total} still reproduce"
                tr.write_line(f"{label:<{width}}  {status}{note}", yellow=True)
            else:
                tr.write_line(f"{label:<{width}}  still present (known issue)  {passed}/{total}{note}")

        fixed_ids = self._ids("fixed")
        for fid in sorted({f for n in fixed_ids for f in self.meta[n][1]}, key=_fid_sort_key):
            passed, failed, total = self._tally([n for n in fixed_ids if fid in self.meta[n][1]])
            label = f"{plain(fid)}:"
            if failed:
                tr.write_line(f"{label:<{width}}  FIX REGRESSED  {passed}/{total} hold", red=True)
            else:
                tr.write_line(f"{label:<{width}}  FIXED  {passed}/{total}", green=True)

        c_pass, c_fail, c_total = self._tally(self._ids("control"))
        c_text = (f"controls FAILED {c_pass}/{c_total}: bug results may not mean what they say"
                  if c_fail else f"controls OK {c_pass}/{c_total}")
        h_text = f"harness FAILED {h_pass}/{h_total}" if h_fail else f"harness OK {h_pass}/{h_total}"
        tr.write_line(f"{c_text} | {h_text}", red=bool(c_fail or h_fail))

        tr.write_line("")
        tr.write_line(f"{'Kind':<20} {'Count':>5}   A pass means")
        for role, label, meaning in ROLE_ROWS:
            tr.write_line(f"{label:<20} {len(self._ids(role)):>5}   {meaning}")
        other = len(self._ids("other"))
        if other:
            tr.write_line(f"(+{other} other tests: the new building blocks, and this summary's own self-test)")


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "finding(fid): links a test to an F-ID in claude/openpoke-findings.md")
    config.addinivalue_line("markers", "control: control/context test; passes when the mechanism works normally")
    config.addinivalue_line("markers", "harness: harness sanity check (isolation, stubs)")
    config.addinivalue_line("markers", "fixed(fid): spec assertion for a finding whose fix landed")
    if not config.pluginmanager.has_plugin("routing-summary"):
        config.pluginmanager.register(RoutingSummary(), "routing-summary")
