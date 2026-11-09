import json, os, random, sys
from pathlib import Path

ROOT = Path(os.environ.get("GEMMA_ROOT", Path.home() / "competitions/gemma-4-agent"))
ids = [l.strip() for l in open(ROOT / "results/laptop/subset_60_seed20261101.txt") if l.strip()]
C = ids[10:]
rows = [json.loads(l) for l in open(ROOT / "results/laptop/main_26b/results.jsonl") if l.strip()]
res = {(r["task"], r["policy"]): r["resolved"] for r in rows if r["model"] == "26b-a4b"}
paired = [t for t in C if (t, "adk") in res and (t, "cached") in res]
cached_only = [t for t in paired if res[(t, "cached")] and not res[(t, "adk")]]
adk_only = [t for t in paired if res[(t, "adk")] and not res[(t, "cached")]]
both = [t for t in paired if res[(t, "adk")] and res[(t, "cached")]]
neither = [t for t in paired if not res[(t, "adk")] and not res[(t, "cached")]]
rng = random.Random(20261102)
controls_both = rng.sample(both, min(3, len(both)))
controls_neither = rng.sample(neither, min(3, len(neither)))
tasks = cached_only + adk_only + controls_both + controls_neither
calls_tasks = tasks if len(tasks) <= 20 else cached_only + adk_only
out = ROOT / "results/laptop/ablation_26b"
out.mkdir(parents=True, exist_ok=True)
lines = [f"{t} 26b-a4b adk_task 1" for t in tasks] + [f"{t} 26b-a4b adk_calls 1" for t in calls_tasks]
(out / "queue.txt").write_text("\n".join(lines) + "\n")
sel = {"paired": len(paired), "cached_only": cached_only, "adk_only": adk_only, "controls_both": controls_both,
       "controls_neither": controls_neither, "adk_task_tasks": len(tasks), "adk_calls_tasks": len(calls_tasks), "seed": 20261102}
(out / "selection.json").write_text(json.dumps(sel, indent=1))
print(json.dumps(sel))
