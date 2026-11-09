import argparse, glob, json, os, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("INSTALL_DEPS", "install_deps_v3.py")
os.environ.setdefault("SWE_IMAGE_PREFIX", "swebench-sandbox-v3")
from sandbox_lib import ROOT, load_tasks, verify
import laptop_agent as LA

MODELS = {"26b-a4b": "gemma-4-26B_q4_0-it.gguf", "e4b": "gemma-4-E4B_q4_0-it.gguf", "e2b": "gemma-4-E2B_q4_0-it.gguf"}
SERVER_BIN = "/opt/llama.cpp/build/bin/llama-server"
PORT = 8082
FLOOR_GB = 12.0
SANDBOX_GB = 4.5
STOP_FILE = ROOT / "results/laptop/STOP"
lock = threading.Lock()
state = {"server": None, "model": None, "own_gtt": 0, "slots": 2}


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


def mem_available_gb():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable:"):
            return int(l.split()[1]) / 1048576
    return 0.0


def gtt_used():
    f = glob.glob("/sys/class/drm/card*/device/mem_info_gtt_used")
    return int(open(f[0]).read()) if f else 0


def router_pids():
    pids = []
    for c in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            argv = open(c, "rb").read().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"llama-server") and b"--port" in argv:
            i = argv.index(b"--port")
            if i + 1 < len(argv) and argv[i + 1] == b"8081":
                pids.append(c.split("/")[2])
    return pids


def router_children():
    kids = []
    for pid in router_pids():
        for p in glob.glob(f"/proc/{pid}/task/*/children"):
            try:
                kids += open(p).read().split()
            except OSError:
                pass
    return kids


def foreign_gpu_use():
    if router_children():
        return True
    base = state["own_gtt"] if state["server"] else 0
    return gtt_used() > base + 2 * 1024 ** 3


def health():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def stop_server():
    p = state["server"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()
    state.update(server=None, model=None, own_gtt=0)
    time.sleep(3)


def start_server(model, slots, ctx_per_slot, reasoning_budget):
    stop_server()
    before = gtt_used()
    logf = open(ROOT / f"logs/llama_{PORT}_{model}.log", "a")
    cmd = [SERVER_BIN, "-m", str(ROOT / "models" / MODELS[model]), "--host", "127.0.0.1", "--port", str(PORT),
           "-a", model, "-c", str(ctx_per_slot * slots), "-np", str(slots), "-ngl", "99", "-fa", "on", "--jinja",
           "--metrics", "--reasoning-format", "deepseek", "--reasoning-budget", str(reasoning_budget)]
    log("start server: " + " ".join(cmd))
    state["server"] = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(600):
        if health():
            break
        if state["server"].poll() is not None:
            raise RuntimeError("llama-server exited during load")
        time.sleep(1)
    state.update(model=model, own_gtt=gtt_used() - before)
    log(f"server ready model={model} own_gtt={state['own_gtt'] / 1024 ** 3:.1f} GiB")


def wait_until_clear(job_key):
    waited = False
    while True:
        if STOP_FILE.exists():
            return False
        foreign = foreign_gpu_use()
        if foreign and state["server"]:
            log(f"foreign GPU use detected, yielding: stopping our server ({job_key})")
            stop_server()
        if not foreign and mem_available_gb() >= FLOOR_GB + SANDBOX_GB:
            if waited:
                log(f"clear again ({job_key})")
            return True
        if not waited:
            log(f"pausing {job_key}: foreign={foreign} mem_available={mem_available_gb():.1f} GB")
            waited = True
        time.sleep(30)


def done_keys(out):
    f = out / "results.jsonl"
    if not f.exists():
        return set()
    return {json.loads(l)["key"] for l in f.open() if l.strip()}


def job_key(j):
    return f"{j['task']}|{j['model']}|{j['policy']}|{j['seed']}"


def run_job(j, args, tasks, out, system_prompt):
    key = job_key(j)
    for attempt in range(3):
        with lock:
            if not wait_until_clear(key):
                return None
            if state["model"] != j["model"] or not health():
                start_server(j["model"], args.slots, args.ctx, args.reasoning_budget)
        cfg = {"model": j["model"], "policy": j["policy"], "seed": int(j["seed"]), "thinking": args.thinking,
               "temperature": 0.2, "top_p": 0.95, "max_output_tokens": args.max_output_tokens, "time_minutes": args.time_minutes,
               "turns": 500, "retention": 5, "compaction_tokens": args.compaction_tokens, "system_prompt": system_prompt}
        t_start = time.time()
        safe = key.replace("|", "__")
        patch, trace = LA.run_task(tasks[j["task"]], cfg, trace_path=out / "traces" / f"{safe}.json")
        if str(trace.get("stop", "")).startswith("model_error") and (STOP_FILE.exists() or foreign_gpu_use() or not health()):
            log(f"model error during yield or server loss, retrying {key}: {trace.get('stop')}")
            continue
        t_agent_end = time.time()
        v = verify(tasks[j["task"]], patch, log_dir=str(out / "verify")) if patch.strip() else {"resolved": False, "error": "empty patch"}
        reqs = trace.get("requests", [])
        agent_reqs = [r for r in reqs if r["kind"] == "agent"]
        row = {"key": key, **j, "resolved": bool(v.get("resolved")), "verify_error": (v.get("error") or "")[:200],
               "stop": trace.get("stop"), "turns": trace.get("turns"), "tool_calls": trace.get("tool_calls_used"),
               "malformed": trace.get("malformed"), "responses": len(agent_reqs), "compactions": trace.get("compactions"),
               "patch_size": len(patch), "agent_seconds": trace.get("agent_seconds"), "bootstrap_seconds": trace.get("bootstrap_seconds"),
               "prompt_n": sum(r.get("prompt_n") or 0 for r in reqs), "cache_n": sum(r.get("cache_n") or 0 for r in reqs),
               "predicted_n": sum(r.get("predicted_n") or 0 for r in reqs), "prompt_ms": round(sum(r.get("prompt_ms") or 0 for r in reqs)),
               "predicted_ms": round(sum(r.get("predicted_ms") or 0 for r in reqs)),
               "compaction_ms": round(sum(r.get("wall_ms") or 0 for r in reqs if r["kind"] == "compaction")),
               "tool_ms": round(sum(c.get("ms") or 0 for s in trace.get("steps", []) for c in s.get("tool_calls", []))),
               "max_context": max([LA.context_tokens(r) for r in agent_reqs] or [0]),
               "t_start": round(t_start, 1), "t_agent_end": round(t_agent_end, 1), "t_end": round(time.time(), 1),
               "slots": args.slots, "thinking": args.thinking}
        with lock:
            with (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
        log(f"done {key} resolved={row['resolved']} stop={row['stop']} turns={row['turns']} malformed={row['malformed']} "
            f"agent_s={row['agent_seconds']} decode_tps={row['predicted_n'] / max(1, row['predicted_ms']) * 1000:.1f}")
        return row
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--ctx", type=int, default=32768)
    ap.add_argument("--time-minutes", type=float, default=60.0)
    ap.add_argument("--max-output-tokens", type=int, default=16384)
    ap.add_argument("--reasoning-budget", type=int, default=4096)
    ap.add_argument("--compaction-tokens", type=int, default=14336)
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks()
    system_prompt = (ROOT / "data/raw/sample_submission/prompts/system.md").read_text()
    jobs = []
    for l in open(a.queue):
        p = l.split()
        if len(p) == 4 and not l.startswith("#"):
            jobs.append({"task": p[0], "model": p[1], "policy": p[2], "seed": p[3]})
    done = done_keys(out)
    todo = [j for j in jobs if job_key(j) not in done]
    if a.limit:
        todo = todo[:a.limit]
    log(f"queue={len(jobs)} done={len(done)} todo={len(todo)} slots={a.slots}")
    try:
        with ThreadPoolExecutor(a.slots) as ex:
            list(ex.map(lambda j: run_job(j, a, tasks, out, system_prompt), todo))
    finally:
        stop_server()
        log("queue finished, server stopped")


if __name__ == "__main__":
    main()
