import json, random, sys
from collections import defaultdict
from pathlib import Path

root = Path(sys.argv[1])
n, seed = int(sys.argv[2]), int(sys.argv[3])
gold = {json.loads(l)["instance_id"]: json.loads(l) for l in open(root / "results/v3/fidelity_gold.jsonl")}
empty = {json.loads(l)["instance_id"]: json.loads(l) for l in open(root / "results/v3/fidelity_empty.jsonl")}
sound = sorted(k for k in gold if gold[k].get("resolved") and k in empty and not empty[k].get("resolved"))
by_repo = defaultdict(list)
for k in sound:
    by_repo[gold[k]["repo"]].append(k)
rng = random.Random(seed)
quota = {r: max(1, n * len(v) / len(sound)) for r, v in by_repo.items()}
alloc = {r: int(q) for r, q in quota.items()}
for r in sorted(quota, key=lambda r: quota[r] - int(quota[r]), reverse=True):
    if sum(alloc.values()) >= n:
        break
    alloc[r] += 1
picked = []
for r in sorted(by_repo):
    picked += rng.sample(sorted(by_repo[r]), min(alloc[r], len(by_repo[r])))
rng.shuffle(picked)
(root / "results/laptop").mkdir(parents=True, exist_ok=True)
(root / "results/laptop/sound_tasks.txt").write_text("\n".join(sound) + "\n")
(root / f"results/laptop/subset_{n}_seed{seed}.txt").write_text("\n".join(picked) + "\n")
counts = {r: len(v) for r, v in by_repo.items()}
print(f"sound={len(sound)} by_repo={counts} alloc={alloc} picked={len(picked)}")
print("first10:", picked[:10])
