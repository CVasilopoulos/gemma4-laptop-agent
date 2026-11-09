import hashlib, json, os, shutil, sys
from pathlib import Path

ROOT = Path(os.environ.get("GEMMA_ROOT", Path.home() / "competitions/gemma-4-agent"))
RUNS = ["g1_think4096", "g1b_nothink", "g1c_cached", "main_26b", "main_e4b", "main_e2b", "stretch_e4b_cached", "stretch_26b_think1024", "ablation_26b"]
MARKER = "[task text removed]"
MIN_FRAGMENT = 60


def load_tasks():
    f = ROOT / "data/raw/tasks.jsonl"
    if not f.exists():
        sys.exit(f"{f} not found: the competition's tasks.jsonl is needed to redact quoted task text")
    return {json.loads(l)["instance_id"]: json.loads(l) for l in open(f)}


def squeeze(text):
    chars, where = [], []
    for i, c in enumerate(text):
        if c.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                where.append(i)
        else:
            chars.append(c)
            where.append(i)
    return "".join(chars), where


def task_grams(task):
    grams = set()
    for field in ("problem_statement", "hints_text"):
        s = squeeze(task.get(field) or "")[0]
        grams |= {s[i:i + MIN_FRAGMENT] for i in range(len(s) - MIN_FRAGMENT + 1)}
    return grams


def redact(text, grams):
    s, where = squeeze(text)
    spans = []
    for i in range(len(s) - MIN_FRAGMENT + 1):
        if s[i:i + MIN_FRAGMENT] in grams:
            if spans and i <= spans[-1][1]:
                spans[-1][1] = i + MIN_FRAGMENT
            else:
                spans.append([i, i + MIN_FRAGMENT])
    out, last = [], 0
    for a, b in spans:
        out += [text[last:where[a]], MARKER]
        last = where[b - 1] + 1
    return "".join(out) + text[last:]


def scrub(trace, tasks):
    cfg = dict(trace.get("config", {}))
    sp = cfg.pop("system_prompt", None)
    if sp is not None:
        cfg["system_prompt_sha256"] = hashlib.sha256(sp.encode()).hexdigest()
        cfg["system_prompt_source"] = "competition data: sample_submission/prompts/system.md"
    trace["config"] = cfg
    trace.pop("bootstrap", None)
    grams = task_grams(tasks[trace["instance_id"]])
    for step in trace.get("steps", []):
        if step.get("content"):
            step["content"] = redact(step["content"], grams)
        for call in step.get("tool_calls", []):
            call["args"] = {k: redact(str(v), grams) for k, v in (call.get("args") or {}).items()}
    return trace


def main(dest):
    dest = Path(dest)
    tasks = load_tasks()
    for run in RUNS:
        src = ROOT / "results/laptop" / run
        if not (src / "results.jsonl").exists():
            continue
        out = dest / "runs" / run
        (out / "traces").mkdir(parents=True, exist_ok=True)
        shutil.copy(src / "results.jsonl", out / "results.jsonl")
        if (src / "queue.txt").exists():
            shutil.copy(src / "queue.txt", out / "queue.txt")
        for t in sorted((src / "traces").glob("*.json")):
            (out / "traces" / t.name).write_text(json.dumps(scrub(json.loads(t.read_text()), tasks)))
    for f in ["subset_60_seed20261101.txt", "sound_tasks.txt"]:
        shutil.copy(ROOT / "results/laptop" / f, dest / f)
    print(f"exported to {dest}")


if __name__ == "__main__":
    main(sys.argv[1])
