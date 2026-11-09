import json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sandbox_lib import load_tasks, verify

mode, ids_file, out_path, conc = sys.argv[1], sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
T = load_tasks()
ids = [l.strip() for l in open(ids_file) if l.strip()] if ids_file != "all" else list(T)
out_path.parent.mkdir(parents=True, exist_ok=True)
log_dir = out_path.with_suffix("")
done = {json.loads(l)["instance_id"] for l in out_path.open()} if out_path.exists() else set()
todo = [T[i] for i in ids if i not in done]


def one(t):
    try:
        r = verify(t, t["patch"] if mode == "gold" else "", log_dir=str(log_dir))
    except Exception as ex:
        r = {"instance_id": t["instance_id"], "repo": t["repo"], "resolved": None, "error": f"{type(ex).__name__}: {ex}"[:300]}
    r["setup_tail"] = r.get("setup_tail", "")[-400:]
    r.pop("pytest_tail", None)
    return r


t0 = time.time()
with out_path.open("a") as f, ThreadPoolExecutor(conc) as ex:
    for fut in as_completed([ex.submit(one, t) for t in todo]):
        f.write(json.dumps(fut.result()) + "\n")
        f.flush()
rows = [json.loads(l) for l in out_path.open()]
res = sum(1 for r in rows if r.get("resolved"))
print(f"DONE {mode}: resolved {res}/{len(rows)} in {time.time()-t0:.0f}s", flush=True)
