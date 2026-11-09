import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from confirmatory import max_identical_run, trace_of
from summarize import ROOT

MAIN = ROOT / "results/laptop/main_26b"
ABL = ROOT / "results/laptop/ablation_26b"
POLICIES = ["adk", "adk_task", "adk_calls", "cached"]
GROUPS = [("cached_only", "cached only"), ("controls_both", "both"), ("controls_neither", "neither")]


def load(out):
    return [json.loads(l) for l in open(out / "results.jsonl") if l.strip()]


def main():
    sel = json.loads((ABL / "selection.json").read_text())
    tasks = sel["cached_only"] + sel["adk_only"] + sel["controls_both"] + sel["controls_neither"]
    group_of = {t: g for g, _ in GROUPS for t in sel[g]}
    group_of.update({t: "adk_only" for t in sel["adk_only"]})
    cells = {}
    for out, rows in ((MAIN, load(MAIN)), (ABL, load(ABL))):
        for r in rows:
            if r["model"] == "26b-a4b" and r["task"] in tasks and r["policy"] in POLICIES:
                t = trace_of(out, r["key"])
                cells[(r["task"], r["policy"])] = {
                    "resolved": bool(r["resolved"]), "stop": (r["stop"] or "").split(":")[0], "turns": r["turns"],
                    "compactions": r["compactions"], "patch_size": r["patch_size"], "verify_error": r.get("verify_error") or "",
                    "max_run": max_identical_run(t) if t else None, "agent_min": round((r.get("agent_seconds") or 0) / 60, 1)}
    missing = [(t, p) for t in tasks for p in POLICIES if (t, p) not in cells]
    arms = {}
    for p in POLICIES:
        cs = [cells[(t, p)] for t in tasks if (t, p) in cells]
        runs = [c["max_run"] for c in cs if c["max_run"] is not None]
        arms[p] = {"n": len(cs),
                   "solved_by_group": {g: sum(cells[(t, p)]["resolved"] for t in sel[g] if (t, p) in cells) for g, _ in GROUPS},
                   "solved": sum(c["resolved"] for c in cs),
                   "loop_tasks_ge10": sum(x >= 10 for x in runs),
                   "compactions_per_task": round(sum(c["compactions"] or 0 for c in cs) / max(1, len(cs)), 1),
                   "stops": dict(Counter(c["stop"] for c in cs)),
                   "empty_patch": sum(1 for c in cs if not c["patch_size"]),
                   "mean_agent_min": round(sum(c["agent_min"] for c in cs) / max(1, len(cs)), 1)}
    per_task = {t: {"group": group_of[t], **{p: cells.get((t, p), {}).get("resolved") for p in POLICIES}} for t in tasks}
    discordant = sel["cached_only"]
    recovered = {p: [t for t in discordant if cells.get((t, p), {}).get("resolved")] for p in ("adk_task", "adk_calls")}
    lost_controls = {p: [t for t in sel["controls_both"] if not cells.get((t, p), {}).get("resolved")] for p in ("adk_task", "adk_calls")}
    gained_controls = {p: [t for t in sel["controls_neither"] if cells.get((t, p), {}).get("resolved")] for p in ("adk_task", "adk_calls")}
    res = {"post_hoc": True, "selection": sel, "missing": missing, "arms": arms, "recovered_discordant": recovered,
           "lost_on_both_controls": lost_controls, "gained_on_neither_controls": gained_controls, "per_task": per_task,
           "cells": {f"{t}|{p}": c for (t, p), c in sorted(cells.items())}}
    (ABL / "ablation_2026-11-03.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("missing", "arms", "recovered_discordant", "lost_on_both_controls", "gained_on_neither_controls")}, indent=1))
    for t in tasks:
        print(t, per_task[t]["group"], " ".join(f"{p}={int(bool(per_task[t][p]))}" for p in POLICIES),
              " runs:", " ".join(str(cells.get((t, p), {}).get("max_run")) for p in POLICIES),
              " stops:", " ".join(cells.get((t, p), {}).get("stop", "-") for p in POLICIES))


if __name__ == "__main__":
    main()
