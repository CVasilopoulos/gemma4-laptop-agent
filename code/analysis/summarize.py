import bisect, csv, glob, json, math, os, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("GEMMA_ROOT", Path.home() / "competitions/gemma-4-agent"))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_power():
    ts, w = [], []
    for f in sorted(glob.glob(str(ROOT / "logs/power/power_*.csv"))):
        with open(f) as fh:
            for row in csv.DictReader(fh):
                try:
                    ts.append(int(row["epoch_ms"]) / 1000)
                    w.append(int(row["package_uW"]) / 1e6)
                except (ValueError, KeyError):
                    pass
    return ts, w


def energy_wh(rows, ts, w):
    spans = [(r["t_start"], r["t_end"]) for r in rows]
    out = []
    for a, b in spans:
        i, j = bisect.bisect_left(ts, a), bisect.bisect_right(ts, b)
        e = 0.0
        for k in range(i, min(j, len(ts) - 1)):
            dt = ts[k + 1] - ts[k]
            if dt > 5:
                continue
            active = sum(1 for x, y in spans if x <= ts[k] <= y) or 1
            e += w[k] * dt / active
        out.append(e / 3600)
    return out


def idle_watts(rows, ts, w):
    busy = sorted((r["t_start"], r["t_end"]) for r in rows)
    vals = [p for t, p in zip(ts, w) if not any(a - 120 <= t <= b + 120 for a, b in busy)]
    return statistics.median(vals) if vals else None


def summarize(paths):
    rows = []
    for p in paths:
        rows += [json.loads(l) for l in open(p) if l.strip()]
    ts, w = load_power()
    for r, e in zip(rows, energy_wh(rows, ts, w) if ts else [None] * len(rows)):
        r["energy_wh"] = e
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["policy"] + ("+think" if r.get("thinking") else ""), r["seed"])].append(r)
    out = []
    for (m, pol, seed), g in sorted(groups.items()):
        n, k = len(g), sum(r["resolved"] for r in g)
        resp = sum(r["responses"] or 0 for r in g)
        mal = sum(r["malformed"] or 0 for r in g)
        pn, cn = sum(r["prompt_n"] for r in g), sum(r["cache_n"] for r in g)
        pms, dms = sum(r["prompt_ms"] for r in g), sum(r["predicted_ms"] for r in g)
        tms, cms = sum(r["tool_ms"] for r in g), sum(r["compaction_ms"] for r in g)
        wall = sum(r["agent_seconds"] or 0 for r in g) * 1000
        en = [r["energy_wh"] for r in g if r.get("energy_wh") is not None]
        lo, hi = wilson(k, n)
        out.append({"model": m, "policy": pol, "seed": seed, "n": n, "resolved": k, "rate": round(k / n, 3),
                    "ci95": [round(lo, 3), round(hi, 3)], "malformed_rate": round(mal / max(1, resp), 4), "responses": resp,
                    "decode_tps": round(sum(r["predicted_n"] for r in g) / max(1, dms) * 1000, 1),
                    "prefill_tps": round(pn / max(1, pms) * 1000, 1), "cache_hit": round(cn / max(1, cn + pn), 3),
                    "mean_agent_min": round(wall / n / 60000, 1),
                    "share_prefill": round(pms / max(1, wall), 3), "share_decode": round(dms / max(1, wall), 3),
                    "share_tools": round(tms / max(1, wall), 3), "share_compaction": round(cms / max(1, wall), 3),
                    "compactions_per_task": round(sum(r["compactions"] or 0 for r in g) / n, 2),
                    "mean_max_context": round(statistics.mean(r["max_context"] for r in g)),
                    "wh_per_task": round(statistics.mean(en), 2) if en else None,
                    "wh_per_resolved": round(sum(en) / k, 2) if en and k else None,
                    "min_per_resolved": round(wall / 60000 / k, 1) if k else None,
                    "stops": dict(sorted(defaultdict(int, {s: sum(1 for r in g if (r["stop"] or "").split(":")[0] == s)
                                                          for s in {(r["stop"] or "").split(":")[0] for r in g}}).items()))})
    return out, idle_watts(rows, ts, w) if ts else None


if __name__ == "__main__":
    res, idle = summarize(sys.argv[1:])
    print(json.dumps({"idle_package_watts": idle, "groups": res}, indent=1))
