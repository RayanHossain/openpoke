"""Post-hoc analyses of round 2 (requested after the round-2 report; not pre-registered). No model calls.

    python -m routing_eval.posthoc     # writes docs/evaluation-data/routing-eval-tier-a/posthoc/{report.md,data.json}

1. ID visibility: was the target's agent ID (registry: ``ea_k``) or name (baseline) present in the visible
   history tail, only in the working-memory summary, or absent? Accuracy by that split and by N; plus how many
   agents' result lines the tail shows per world, against the range the production 100/10 fold can leave.
2. Registry errors on keyed probes, by category and type.
3. Registry correct routes decomposed: key match on a shown card / card shown without a key match (hot set or
   show-all) / target not on any card (ID from history only).
Worlds are rebuilt deterministically from the dataset and the frozen names/summaries.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Dict, List

from . import catalog
from .analysis import groups_of, load
from .generator import build_world, folded_count
from .keys_pure import agents_for_keys, keybook, text_keys
from .labeling import ALIAS_COLLISION
from .round2 import OUT
from .schema import SUMMARY_TAIL, SUMMARY_THRESHOLD
from .views import conversation_entries, summary_text

POST = OUT / "posthoc"
WORLDS = ("N3-high", "N10-high", "N30-high", "N50-high", "N50-low")


def _targets(row: Dict) -> List[str]:
    for a in row["acceptable"]:
        if a.startswith("route("):
            return a[6:-1].split(",")
    return []


class Worlds:
    def __init__(self):
        self.cache = {}
        self.probes = {p.probe_id: p for p in catalog.PROBES}

    def get(self, probe: str, n: int, sim: str):
        k = (probe, n, sim)
        if k not in self.cache:
            w = build_world(self.probes[probe], n, sim)
            info = {"world": w}
            for arm in ("baseline", "registry"):
                entries = conversation_entries(w, arm)
                cut = folded_count(len(entries))
                info[arm] = {"tail": "\n".join(p for t, _, p in entries[cut:]), "summary": summary_text(w, arm),
                             "tail_entries": len(entries) - cut,
                             "tail_agents": {e for e in (ev.agent_ref for ev in self._tail_events(w, cut)) if e}}
            book = keybook(w)
            info["book"] = book
            self.cache[k] = info
        return self.cache[k]

    @staticmethod
    def _tail_events(w, cut):
        """Step events whose agent_message entry is in the unfolded tail."""
        idx = 0
        out = []
        for ev in w.events:
            if ev.kind == "step":
                if idx + 1 >= cut:  # entry order: user, agent, reply
                    out.append(ev)
                idx += 3
            else:
                idx += 2
        return [e for e in out]


def _visible(where: Dict, token_re: str) -> str:
    if re.search(token_re, where["tail"]):
        return "tail"
    if re.search(token_re, where["summary"]):
        return "summary_only"
    return "absent"


def visibility_class(row: Dict, wi: Dict) -> str:
    world = wi["world"]
    targets = _targets(row)
    if not targets:
        return "n/a"
    classes = []
    for t in targets:
        agent = world.agent_for_ws(t)
        if row["router"] == "registry":
            classes.append(_visible(wi["registry"], rf"\bea_{agent.index}\b"))
        else:
            classes.append(_visible(wi["baseline"], re.escape(agent.name)))
    order = {"tail": 0, "summary_only": 1, "absent": 2}
    return max(classes, key=order.get)  # the least visible target decides


def route_source(row: Dict, wi: Dict) -> str:
    """For a correct registry route: how the target agent was reachable."""
    world, book = wi["world"], wi["book"]
    targets = _targets(row)
    cand = set(row["candidates_ws"])
    key_refs = set(agents_for_keys(text_keys(row["message_text"], book), book, world)) if row.get("message_text") else set()
    out = []
    for t in targets:
        ref = world.agent_for_ws(t).ref
        if t not in cand:
            out.append("not_on_cards_history_only")
        elif ref in key_refs:
            out.append("card_by_key_match")
        else:
            out.append("card_without_key_match" + ("_show_all" if world.n <= 8 else "_hot_set"))
    return sorted(set(out))[-1] if len(set(out)) > 1 else out[0]


def run() -> Dict:
    rows = load("main")
    W = Worlds()
    probes = {p.probe_id: p for p in catalog.PROBES}
    for r in rows:
        r["message_text"] = probes[r["probe"]].message
    res: Dict = {}

    # ---- 1. ID visibility -------------------------------------------------------------
    vis = defaultdict(list)
    for r in rows:
        if r["router"] not in ("registry", "baseline"):
            continue
        wi = W.get(r["probe"], r["n"], r["sim"])
        c = visibility_class(r, wi)
        r["visibility"] = c
        if c != "n/a":
            vis[(r["router"], f"N{r['n']}-{r['sim']}", c)].append(r["correct"])
    res["id_visibility"] = {
        router: {w: {c: {"n": len(vis[(router, w, c)]),
                         "acc": (mean(vis[(router, w, c)]) if vis[(router, w, c)] else None)}
                     for c in ("tail", "summary_only", "absent")} for w in WORLDS}
        for router in ("registry", "baseline")}
    tails = defaultdict(list)
    for (probe, n, sim), wi in W.cache.items():
        w = wi["world"]
        total_entries = len(conversation_entries(w, "registry"))
        tails[f"N{n}-{sim}"].append({"agents": len(w.agents), "tail_entries": wi["registry"]["tail_entries"],
                                     "tail_agents": len(wi["registry"]["tail_agents"]),
                                     "folded": folded_count(total_entries) > 0})
    res["tail_visibility"] = {
        k: {"worlds": len(v), "agents": v[0]["agents"],
            "tail_entries_mean": round(mean(x["tail_entries"] for x in v), 1),
            "tail_entries_min": min(x["tail_entries"] for x in v), "tail_entries_max": max(x["tail_entries"] for x in v),
            "agents_with_result_line_in_tail_mean": round(mean(x["tail_agents"] for x in v), 1),
            "share_of_agents_visible_mean": round(mean(x["tail_agents"] / x["agents"] for x in v), 2),
            "worlds_folded": sum(x["folded"] for x in v)}
        for k, v in tails.items()}
    res["production_fold_range"] = {
        "rule": f"fold the oldest {SUMMARY_THRESHOLD} entries whenever {SUMMARY_THRESHOLD + SUMMARY_TAIL} are unsummarised",
        "tail_entries_after_any_fold": [SUMMARY_TAIL, SUMMARY_THRESHOLD + SUMMARY_TAIL - 1],
        "agent_turns_in_tail": [SUMMARY_TAIL // 3, (SUMMARY_THRESHOLD + SUMMARY_TAIL - 1) // 3],
        "note": "Each world is a snapshot at a fixed point in that cycle (tail = all entries minus folded ones); "
                "the same rule as production, but production's tail can be as short as 10 entries (~3 agent turns) "
                "right after a fold.",
    }

    # ---- 2. registry errors on keyed probes ---------------------------------------------
    keyed_err = []
    for r in rows:
        if r["router"] != "registry" or "keyed" not in groups_of(r) or r["correct"]:
            continue
        wi = W.get(r["probe"], r["n"], r["sim"])
        world, book = wi["world"], wi["book"]
        matched = agents_for_keys(text_keys(r["message_text"], book), book, world)
        flags = []
        if r["probe"] in ALIAS_COLLISION:
            flags.append("alias_collision")
        if r["category"] == "hub_key":
            flags.append("hub_key")
        if len(matched) > 1:
            flags.append(f"key_matches_{min(len(matched), 5)}+_agents" if len(matched) >= 5 else f"key_matches_{len(matched)}_agents")
        if "ask" in r["acceptable"]:
            flags.append("ask_acceptable")
        keyed_err.append({"probe": r["probe"], "category": r["category"], "world": f"N{r['n']}-{r['sim']}",
                          "error": r["error"], "decision": r["decision"], "flags": flags,
                          "key_matched_agents": len(matched)})
    res["registry_keyed_errors"] = {
        "total": len(keyed_err),
        "by_category": Counter(e["category"] for e in keyed_err).most_common(),
        "by_error": Counter(e["error"] for e in keyed_err).most_common(),
        "by_flag": Counter(f for e in keyed_err for f in e["flags"]).most_common(),
        "by_probe": Counter(e["probe"] for e in keyed_err).most_common(),
        "rows": keyed_err,
    }
    # RAG on the same keyed probes, for contrast
    rag_k = [r for r in rows if r["router"] == "rag" and "keyed" in groups_of(r)]
    reg_k = [r for r in rows if r["router"] == "registry" and "keyed" in groups_of(r)]
    by_probe = lambda rs: {p: mean(x["correct"] for x in rs if x["probe"] == p) for p in {x["probe"] for x in rs}}  # noqa: E731
    rb, gb = by_probe(rag_k), by_probe(reg_k)
    res["keyed_by_probe"] = sorted(({"probe": p, "category": probes[p].category, "registry": round(gb.get(p, 0), 2),
                                     "rag": round(rb.get(p, 0), 2)} for p in gb), key=lambda d: d["registry"] - d["rag"])

    # ---- 2b. decisions by message source (user vs watcher/webhook) -------------------------
    by_src = defaultdict(lambda: {"n": 0, "correct": 0, "decisions": Counter()})
    for r in rows:
        k = f"{r['router']}|{'user' if r['source'] == 'user' else 'watcher/webhook'}"
        by_src[k]["n"] += 1
        by_src[k]["correct"] += r["correct"]
        by_src[k]["decisions"][r["decision"]] += 1
    res["by_source"] = {k: {"n": v["n"], "acc": v["correct"] / v["n"], "decisions": dict(v["decisions"])}
                        for k, v in sorted(by_src.items())}

    # ---- 3. registry correct routes decomposed --------------------------------------------
    src = defaultdict(Counter)
    for r in rows:
        if r["router"] == "registry" and r["correct"] and r["decision"] == "route":
            wi = W.get(r["probe"], r["n"], r["sim"])
            src[f"N{r['n']}-{r['sim']}"][route_source(r, wi)] += 1
    res["registry_correct_route_sources"] = {w: dict(src[w]) for w in WORLDS}
    return res


def to_md(res: Dict) -> str:
    f = lambda x: "–" if x is None else f"{x:.0%}"  # noqa: E731
    L = ["# Round 2: post-hoc analyses (not pre-registered)", "",
         "Requested after the round-2 report. No model calls; worlds rebuilt from the dataset and frozen names/summaries.",
         "", "## 1. Is the target's agent ID (registry) / name (baseline) visible?", "",
         "Accuracy (n decisions) by where the least visible target appears.", "",
         "| Router | World | in history tail | only in summary | absent |", "|---|---|---|---|---|"]
    for router in ("registry", "baseline"):
        for w in WORLDS:
            d = res["id_visibility"][router][w]
            L.append(f"| {router} | {w} | " + " | ".join(f"{f(d[c]['acc'])} ({d[c]['n']})" for c in ("tail", "summary_only", "absent")) + " |")
    L += ["", "Visible history per world (registry arm; baseline identical apart from the line prefix):", "",
          "| World | agents | tail entries (mean, min–max) | agents with a result line in the tail (mean) | share | worlds folded |",
          "|---|---|---|---|---|---|"]
    for w in WORLDS:
        t = res["tail_visibility"][w]
        L.append(f"| {w} | {t['agents']} | {t['tail_entries_mean']} ({t['tail_entries_min']}–{t['tail_entries_max']}) | "
                 f"{t['agents_with_result_line_in_tail_mean']} | {t['share_of_agents_visible_mean']:.0%} | {t['worlds_folded']}/{t['worlds']} |")
    p = res["production_fold_range"]
    L += ["", f"Production fold: {p['rule']}; the tail holds {p['tail_entries_after_any_fold'][0]}–"
              f"{p['tail_entries_after_any_fold'][1]} entries (about {p['agent_turns_in_tail'][0]}–{p['agent_turns_in_tail'][1]} agent turns). "
              + p["note"], "", "## 2. Registry errors on keyed probes", "",
          f"Total errors: {res['registry_keyed_errors']['total']}.", "",
          "| By category | n |", "|---|---|"]
    L += [f"| {c} | {n} |" for c, n in res["registry_keyed_errors"]["by_category"]]
    L += ["", "| By error type | n |", "|---|---|"] + [f"| {c} | {n} |" for c, n in res["registry_keyed_errors"]["by_error"]]
    L += ["", "| By flag (several per error possible) | n |", "|---|---|"] + \
         [f"| {c} | {n} |" for c, n in res["registry_keyed_errors"]["by_flag"]]
    L += ["", "Keyed probes where the registry trails RAG most (mean accuracy over all worlds and runs):", "",
          "| probe | category | registry | RAG |", "|---|---|---|---|"]
    L += [f"| {d['probe']} | {d['category']} | {d['registry']:.0%} | {d['rag']:.0%} |" for d in res["keyed_by_probe"][:12]]
    L += ["", "### Decisions by message source", "",
          "Inbound watcher/webhook events reach the IA through handle_agent_message as <new_agent_message>; the IA "
          "prompt tells it to summarise agent messages for the user, so both IA routers mostly tell the user instead "
          "of dispatching to the owner, which the labels count as a dropped request. RAG only routes.", "",
          "| Router · source | n | accuracy | decisions |", "|---|---|---|---|"]
    L += [f"| {k} | {v['n']} | {v['acc']:.0%} | {v['decisions']} |" for k, v in res["by_source"].items()]
    L += ["", "## 3. Registry correct routes: how was the target reachable?", "",
          "| World | card by key match | card, no key match (show-all ≤ 8) | card, no key match (hot set) | not on any card (history ID only) |",
          "|---|---|---|---|---|"]
    for w in WORLDS:
        s = res["registry_correct_route_sources"][w]
        L.append(f"| {w} | {s.get('card_by_key_match', 0)} | {s.get('card_without_key_match_show_all', 0)} | "
                 f"{s.get('card_without_key_match_hot_set', 0)} | {s.get('not_on_cards_history_only', 0)} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = run()
    POST.mkdir(parents=True, exist_ok=True)
    (POST / "data.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    md = to_md(res)
    (POST / "report.md").write_text(md, encoding="utf-8")
    print(md)
