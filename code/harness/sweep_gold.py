import json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sandbox_lib import load_tasks, verify

MODE = sys.argv[1] if len(sys.argv) > 1 else "gold"
CONC = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = Path(f"results/fidelity_{MODE}.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)
done = set()
if OUT.exists():
    done = {json.loads(l)["instance_id"] for l in OUT.open()}

T = load_tasks()
todo = [t for k, t in T.items() if k not in done]
print(f"mode={MODE} todo={len(todo)} done={len(done)}", flush=True)


def one(t):
    p = t["patch"] if MODE == "gold" else ""
    try:
        r = verify(t, p, log_dir=f"results/fidelity_{MODE}_logs")
    except Exception as ex:
        r = {"instance_id": t["instance_id"], "repo": t["repo"], "resolved": None, "error": f"{type(ex).__name__}: {ex}"[:300]}
    return {k: v for k, v in r.items() if k not in ("pytest_tail", "setup_tail")}


t0 = time.time()
n = 0
with OUT.open("a") as f, ThreadPoolExecutor(CONC) as ex:
    futs = {ex.submit(one, t): t["instance_id"] for t in todo}
    for fut in as_completed(futs):
        r = fut.result()
        f.write(json.dumps(r) + "\n")
        f.flush()
        n += 1
        if n % 10 == 0:
            print(f"{n}/{len(todo)} {time.time()-t0:.0f}s", flush=True)
rows = [json.loads(l) for l in OUT.open()]
res = sum(1 for r in rows if r.get("resolved"))
print(f"DONE {MODE}: resolved {res}/{len(rows)} = {res/len(rows):.3f} in {time.time()-t0:.0f}s", flush=True)
