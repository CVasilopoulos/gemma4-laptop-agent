import json, math, statistics, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from summarize import ROOT, energy_wh, load_power, wilson

SUBSET = ROOT / "results/laptop/subset_60_seed20261101.txt"


def split_sets():
    ids = [l.strip() for l in open(SUBSET) if l.strip()]
    return set(ids[10:]), set(ids[:10])


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def trace_of(out, key):
    f = out / "traces" / f"{key.replace('|', '__')}.json"
    return json.loads(f.read_text()) if f.exists() else None


def max_identical_run(trace):
    seq = [(c.get("name"), json.dumps(c.get("args"), sort_keys=True)) for s in trace.get("steps", []) for c in s.get("tool_calls", [])]
    best, cur = 0, 0
    for i, x in enumerate(seq):
        cur = cur + 1 if i and x == seq[i - 1] else 1
        best = max(best, cur)
    return best


def time_split(trace):
    agent = [r for r in trace.get("requests", []) if r.get("kind") == "agent"]
    comp = [r for r in trace.get("requests", []) if r.get("kind") == "compaction"]
    tools = sum(c.get("ms") or 0 for s in trace.get("steps", []) for c in s.get("tool_calls", []))
    return {"agent_prefill_ms": sum(r.get("prompt_ms") or 0 for r in agent), "agent_decode_ms": sum(r.get("predicted_ms") or 0 for r in agent),
            "compaction_ms": sum(r.get("wall_ms") or 0 for r in comp), "tool_ms": tools}


def arm_stats(rows, out):
    n, k = len(rows), sum(r["resolved"] for r in rows)
    lo, hi = wilson(k, n)
    runs, splits = [], Counter()
    for r in rows:
        t = trace_of(out, r["key"])
        if t:
            runs.append(max_identical_run(t))
            for a, v in time_split(t).items():
                splits[a] += v
    wall = sum((r.get("agent_seconds") or 0) for r in rows) * 1000
    en = [r["energy_wh"] for r in rows if r.get("energy_wh") is not None]
    return {"n": n, "resolved": k, "rate": round(k / n, 3) if n else None, "wilson95": [round(lo, 3), round(hi, 3)],
            "loop_tasks_ge5": sum(x >= 5 for x in runs), "loop_tasks_ge10": sum(x >= 10 for x in runs), "max_run_max": max(runs or [0]),
            "malformed_rate": round(sum(r["malformed"] or 0 for r in rows) / max(1, sum(r["responses"] or 0 for r in rows)), 4),
            "stops": dict(Counter((r["stop"] or "").split(":")[0] for r in rows)),
            "compactions_per_task": round(sum(r["compactions"] or 0 for r in rows) / max(1, n), 2),
            "mean_agent_min": round(wall / max(1, n) / 60000, 1), "min_per_resolved": round(wall / 60000 / k, 1) if k else None,
            "wh_per_task": round(statistics.mean(en), 2) if en else None, "wh_per_resolved": round(sum(en) / k, 1) if en and k else None,
            "share": {a: round(v / max(1, wall), 3) for a, v in splits.items()}}


def paired(rows, a, b, ids):
    by = {(r["task"], r["policy"] if a[0] == b[0] else r["model"]): r for r in rows}
    ta = {r["task"]: r for r in rows if (r["model"], r["policy"]) == a and r["task"] in ids}
    tb = {r["task"]: r for r in rows if (r["model"], r["policy"]) == b and r["task"] in ids}
    common = sorted(set(ta) & set(tb))
    b_only = sum(1 for t in common if tb[t]["resolved"] and not ta[t]["resolved"])
    a_only = sum(1 for t in common if ta[t]["resolved"] and not tb[t]["resolved"])
    return {"pairs": len(common), f"{b[0]}:{b[1]}_only": b_only, f"{a[0]}:{a[1]}_only": a_only,
            "both": sum(1 for t in common if ta[t]["resolved"] and tb[t]["resolved"]), "mcnemar_exact_p": round(mcnemar_exact(b_only, a_only), 4)}


def main(dirs):
    C, E = split_sets()
    rows = []
    for d in dirs:
        out = ROOT / d
        for l in open(out / "results.jsonl"):
            if l.strip():
                r = json.loads(l)
                r["_out"] = out
                rows.append(r)
    ts, w = load_power()
    for r, e in zip(rows, energy_wh(rows, ts, w) if ts else [None] * len(rows)):
        r["energy_wh"] = e
    report = {"confirmatory_tasks": len(C), "exploratory_tasks": len(E)}
    arms = sorted({(r["model"], r["policy"] + ("+think" if r.get("thinking") else "")) for r in rows})
    for name, ids in (("C", C), ("E", E)):
        report[name] = {f"{m}:{p}": arm_stats([r for r in rows if (r["model"], r["policy"] + ("+think" if r.get("thinking") else "")) == (m, p) and r["task"] in ids], rows[0]["_out"] if False else next(r["_out"] for r in rows if (r["model"], r["policy"]) == (m, p.replace("+think", ""))))
                        for m, p in arms}
        report[name]["primary_26b_cached_vs_adk"] = paired(rows, ("26b-a4b", "adk"), ("26b-a4b", "cached"), ids)
        for m in ("e4b", "e2b"):
            if any(r["model"] == m for r in rows):
                report[name][f"26b_vs_{m}_adk"] = paired(rows, (m, "adk"), ("26b-a4b", "adk"), ids)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["results/laptop/main_26b"])
