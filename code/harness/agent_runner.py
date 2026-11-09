import json, os, re, shlex, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness_text import nudges, prompt_blocks
from sandbox_lib import RAW, Sandbox, bootstrap, image_for, load_tasks

API = os.environ.get("SWE_API_BASE", "http://127.0.0.1:8082/v1")
MODEL = os.environ.get("SWE_MODEL", "gemma-4-e4b")
MAX_STDOUT = 5000
MAX_FILE_LINES = 150
MAX_FILE_CHARS = 10000
PROMPT_HEADER, PROMPT_HINTS, PROMPT_BUDGET, PROMPT_RULES, PROMPT_GRAPH = prompt_blocks()

TOOL_SPECS = {
    "run_command": ("Execute a shell command with /bin/bash -c inside /workspace.",
                    {"command": {"type": "string", "description": "Shell command to run"}}, ["command"]),
    "read_file": ("Read a file from /workspace with 1-indexed inclusive line slicing.",
                  {"filepath": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["filepath"]),
    "edit_file": ("Replace old_string with new_string in an existing file under /workspace.",
                  {"filepath": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
                   "allow_multiple": {"type": "boolean"}}, ["filepath", "old_string", "new_string"]),
    "write_file": ("Create or overwrite a file at /workspace/<filepath>.",
                   {"filepath": {"type": "string"}, "content": {"type": "string"}}, ["filepath", "content"]),
    "get_status": ("Return live budget consumption and patch status.", {}, []),
    "submit_patch": ("Capture git diff HEAD as the final patch and end the task.", {}, []),
    "search_similar_code": ("Find graph nodes most similar to a symbol name.",
                            {"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]),
    "get_code_neighbors": ("Find callers/callees of a symbol in the repository graph.",
                           {"node": {"type": "string"}, "edge_type": {"type": "string"}, "max_neighbors": {"type": "integer"}}, ["node"]),
    "get_code_subgraph": ("Induced subgraph for a list of symbols.", {"nodes": {"type": "array", "items": {"type": "string"}}}, ["nodes"]),
}


def tool_schema(names):
    out = []
    for n in names:
        desc, props, req = TOOL_SPECS[n]
        out.append({"type": "function", "function": {"name": n, "description": desc,
                                                     "parameters": {"type": "object", "properties": props, "required": req}}})
    return out


class GraphTools:
    def __init__(self, instance_id):
        self.nodes, self.edges, self.emb = {}, [], None
        g = RAW / "graphs" / f"{instance_id}.json"
        e = RAW / "embeddings" / f"{instance_id}.npz"
        if g.exists() and g.stat().st_size > 100:
            d = json.loads(g.read_text())
            self.nodes = {n["id"]: n for n in d.get("nodes", [])}
            self.edges = d.get("edges", [])
        if e.exists() and e.stat().st_size > 100:
            import numpy as np
            self.emb = dict(np.load(e))
        self.available = bool(self.nodes) and self.emb is not None

    def resolve(self, name):
        if name in self.nodes:
            return name
        for k in self.nodes:
            if k.endswith("." + name) or k.endswith("/" + name):
                return k
        low = name.lower()
        for k in self.nodes:
            if k.lower() == low:
                return k
        for k in self.nodes:
            if low in k.lower():
                return k
        return None

    def search_similar_code(self, query, k=10):
        if self.emb is None:
            return {"status": "error", "error_type": "GraphUnavailable", "error_message": "no embeddings"}
        import numpy as np
        key = self.resolve(query)
        if key is None or key not in self.emb:
            cands = [n for n in self.emb if query.lower() in n.lower()]
            if not cands:
                return {"status": "error", "error_type": "NodeNotFound", "error_message": f"no node matching {query}"}
            key = cands[0]
        v = np.asarray(self.emb[key], dtype="float32").ravel()
        keys = list(self.emb.keys())
        M = np.stack([np.asarray(self.emb[x], dtype="float32").ravel() for x in keys])
        sim = M @ v / (np.linalg.norm(M, axis=1) * np.linalg.norm(v) + 1e-9)
        idx = np.argsort(-sim)[: k + 1]
        res = []
        for i in idx:
            if keys[i] == key:
                continue
            nd = self.nodes.get(keys[i], {})
            res.append({"node_name": keys[i], "code": (nd.get("text") or "")[:1200], "similarity": round(float(sim[i]), 4)})
            if len(res) >= k:
                break
        return {"status": "ok", "query": query, "results": res, "count": len(res)}

    def get_code_neighbors(self, node, edge_type=None, max_neighbors=50):
        key = self.resolve(node)
        if key is None:
            return {"status": "error", "error_type": "NodeNotFound", "error_message": f"no node matching {node}"}
        nb = []
        for e in self.edges:
            if edge_type and e.get("type") != edge_type:
                continue
            if e.get("source") == key:
                nb.append(e.get("target"))
            elif e.get("target") == key:
                nb.append(e.get("source"))
        nb = list(dict.fromkeys(nb))[:max_neighbors]
        return {"status": "ok", "node": key, "neighbors": nb, "count": len(nb)}

    def get_code_subgraph(self, nodes):
        keys = [k for k in (self.resolve(n) for n in nodes) if k]
        s = set(keys)
        eds = [{"from": e["source"], "to": e["target"], "type": e.get("type")} for e in self.edges
               if e.get("source") in s and e.get("target") in s]
        return {"status": "ok", "nodes": keys, "edges": eds, "node_count": len(keys), "edge_count": len(eds)}


class Ctx:
    def __init__(self, sb, task, budget):
        self.sb, self.task = sb, task
        self.budget = budget
        self.tool_calls_used = 0
        self.patch_submitted = False
        self.submitted_patch = ""
        self.graph = GraphTools(task["instance_id"])
        self.seen_calls = {}
        self.repeat_guard = os.environ.get("SWE_REPEAT_GUARD", "1") == "1"
        self.start = time.time()

    def remaining_time(self):
        return self.budget["time_minutes"] * 60 - (time.time() - self.start)

    def remaining_calls(self):
        m = self.budget.get("tool_calls")
        return None if m is None else m - self.tool_calls_used

    def call(self, name, args):
        key = name + "|" + json.dumps(args, sort_keys=True)
        if key in self.seen_calls and name not in ("submit_patch", "get_status", "run_command"):
            self.seen_calls[key] += 1
            if self.repeat_guard:
                self.tool_calls_used += 1
                return {"status": "error", "error_type": "RepeatedCall",
                        "error_message": f"You already made this exact {name} call {self.seen_calls[key]} time(s) and the result has not changed. Do NOT repeat it. Either narrow it (for read_file pass start_line/end_line around the target, found with grep -n), act on what you already know by calling edit_file, or call submit_patch."}
        self.seen_calls[key] = self.seen_calls.get(key, 0) + 1
        gated = name not in ("submit_patch", "get_status")
        if gated:
            r = self.remaining_calls()
            if r is not None and r <= 0:
                return {"status": "error", "error_type": "BudgetExhausted", "error_message": "tool call budget exhausted"}
            if self.remaining_time() <= 0:
                return {"status": "error", "error_type": "BudgetExhausted", "error_message": "time budget exhausted"}
            self.tool_calls_used += 1
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            return {"status": "error", "error_type": "UnknownTool", "error_message": name}
        try:
            out = fn(**args)
        except TypeError as ex:
            return {"status": "error", "error_type": "ValidationError", "error_message": str(ex)}
        except Exception as ex:
            return {"status": "error", "error_type": type(ex).__name__, "error_message": str(ex)[:300]}
        r = self.remaining_calls()
        if isinstance(out, dict) and r is not None and self.tool_calls_used >= 20 and r <= 10:
            out["budget_warning"] = f"Only {r} tool call(s) remaining."
        return out

    def t_run_command(self, command):
        to = min(300, max(5, int(self.remaining_time())))
        try:
            r = self.sb.sh(command, timeout=to)
        except Exception:
            return {"status": "error", "error_type": "TimeoutExceeded", "error_message": f"timed out after {to}s"}
        if r["exit_code"] == 0:
            return {"status": "ok", "stdout": r["stdout"][:MAX_STDOUT], "exit_code": 0}
        return {"status": "error", "error_type": "CommandError",
                "error_message": f"Command failed with exit code {r['exit_code']}",
                "details": {"stdout": r["stdout"][:MAX_STDOUT], "stderr": r["stderr"][:MAX_STDOUT], "exit_code": r["exit_code"]}}

    def _p(self, filepath):
        fp = filepath.lstrip("/")
        if fp.startswith("workspace/"):
            fp = fp[len("workspace/"):]
        if ".." in fp.split("/"):
            raise ValueError("path traversal")
        return fp

    def t_read_file(self, filepath, start_line=None, end_line=None):
        fp = self._p(filepath)
        r = self.sb.sh(f"cat -- {shlex.quote(fp)}", timeout=60)
        if r["exit_code"] != 0:
            return {"status": "error", "error_type": "FileReadError", "error_message": r["stderr"][:300]}
        lines = r["stdout"].split("\n")
        total = len(lines)
        s = max(1, start_line or 1)
        e = min(total, end_line or total)
        sel = lines[s - 1:e][:MAX_FILE_LINES]
        content = "\n".join(sel)
        trunc = len(sel) < (e - s + 1)
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS]
            trunc = True
        return {"status": "ok", "filepath": fp, "content": content, "start_line": s,
                "end_line": s + len(sel) - 1, "total_lines": total, "is_truncated": trunc}

    def t_edit_file(self, filepath, old_string, new_string, allow_multiple=False):
        fp = self._p(filepath)
        payload = json.dumps({"fp": fp, "old": old_string, "new": new_string, "multi": bool(allow_multiple)})
        self.sb.put_text(payload, "/tmp/edit.json")
        script = r'''
import json,re,sys
d=json.load(open("/tmp/edit.json"))
p="/workspace/"+d["fp"]
try: s=open(p).read()
except Exception as e: print(json.dumps({"status":"error","error_type":"FileEditError","error_message":str(e)})); sys.exit()
if not s: print(json.dumps({"status":"error","error_type":"FileEditError","error_message":"file is empty"})); sys.exit()
old,new=d["old"].replace("\r\n","\n"),d["new"].replace("\r\n","\n"); t=s.replace("\r\n","\n")
strategy=None; n=t.count(old)
if n>0: strategy="exact"
else:
    ol=[x.strip() for x in old.split("\n") if x.strip()]
    tl=t.split("\n"); hit=None
    for i in range(len(tl)):
        if [x.strip() for x in tl[i:i+len(ol)] if x.strip()][:len(ol)]==ol:
            hit=i; break
    if hit is not None:
        base=tl[hit][:len(tl[hit])-len(tl[hit].lstrip())]
        blk="\n".join(tl[hit:hit+len(old.split("\n"))])
        rep="\n".join((base+x.strip()) if x.strip() else x for x in new.split("\n"))
        t=t.replace(blk,rep,1); strategy="flexible"; n=1
    else:
        toks=[re.escape(x) for x in re.split(r"([()\[\]{}:><=])",old) if x.strip()]
        pat=r"\s*".join(toks)
        m=list(re.finditer(pat,t))
        if m: t=t[:m[0].start()]+new+t[m[0].end():]; strategy="regex"; n=len(m)
if strategy is None:
    print(json.dumps({"status":"error","error_type":"FileEditError","error_message":"old_string not found"})); sys.exit()
if n>1 and not d["multi"] and strategy=="exact":
    print(json.dumps({"status":"error","error_type":"FileEditError","error_message":f"{n} occurrences; set allow_multiple"})); sys.exit()
if strategy=="exact": t=t.replace(old,new) if d["multi"] else t.replace(old,new,1)
open(p,"w").write(t)
print(json.dumps({"status":"ok","filepath":d["fp"],"occurrences":n,"strategy":strategy}))
'''
        self.sb.put_text(script, "/tmp/edit.py")
        r = self.sb.sh("python3 /tmp/edit.py", timeout=60)
        try:
            return json.loads(r["stdout"].strip().split("\n")[-1])
        except Exception:
            return {"status": "error", "error_type": "FileEditError", "error_message": (r["stdout"] + r["stderr"])[:300]}

    def t_write_file(self, filepath, content):
        fp = self._p(filepath)
        self.sb.put_text(content, "/tmp/wf.txt")
        r = self.sb.sh(f"mkdir -p $(dirname {shlex.quote(fp)}) && cp /tmp/wf.txt {shlex.quote(fp)} && wc -c < {shlex.quote(fp)}", timeout=60)
        if r["exit_code"] != 0:
            return {"status": "error", "error_type": "FileWriteError", "error_message": r["stderr"][:300]}
        return {"status": "ok", "filepath": fp, "size": int(r["stdout"].strip() or 0)}

    def t_get_status(self):
        return {"tool_calls_used": self.tool_calls_used, "patch_submitted": self.patch_submitted,
                "patch_size": len(self.submitted_patch),
                "tool_calls_remaining": self.remaining_calls(),
                "max_tool_calls": self.budget.get("tool_calls"),
                "time_seconds_remaining": round(self.remaining_time(), 1),
                "max_time_minutes": self.budget["time_minutes"],
                "agent_elapsed_seconds": round(time.time() - self.start, 1),
                "max_turns": self.budget.get("turns"), "command_timeout_seconds": 300}

    def t_submit_patch(self):
        self.sb.sh("cd /workspace && git add -N .", timeout=120)
        r = self.sb.sh("cd /workspace && git diff HEAD", timeout=120)
        self.submitted_patch = r["stdout"]
        self.patch_submitted = True
        files = len(re.findall(r"^\+\+\+ b/", self.submitted_patch, re.M))
        return {"status": "ok", "patch_size": len(self.submitted_patch), "files_changed": files}

    def t_search_similar_code(self, query, k=10):
        return self.graph.search_similar_code(query, k)

    def t_get_code_neighbors(self, node, edge_type=None, max_neighbors=50):
        return self.graph.get_code_neighbors(node, edge_type, max_neighbors)

    def t_get_code_subgraph(self, nodes):
        return self.graph.get_code_subgraph(nodes)


def build_agent_prompt(sb, task, budget, graph_available):
    p = [PROMPT_HEADER.replace("{task.repo}", task["repo"]).replace("{task.problem_statement}", task["problem_statement"]) + "\n"]
    if (task.get("hints_text") or "").strip():
        p.append(PROMPT_HINTS.replace("{task.hints_text}", task["hints_text"].strip()) + "\n")
    heading, time_line, calls_line, turns_line = PROMPT_BUDGET.split("\n")[:4]
    b = [heading, time_line.replace("60.0", str(budget["time_minutes"]))]
    if budget.get("tool_calls"):
        b.append(calls_line.replace("<N>", str(budget["tool_calls"])))
    if budget.get("turns"):
        b.append(turns_line.replace("<N>", str(budget["turns"])))
    p.append("\n".join(b) + "\n")
    p.append(PROMPT_RULES + "\n")
    p.append("""## Instructions
0. Work strictly under /workspace.
1. Inspect the existing code and conventions before editing.
2. Make the minimal source change that resolves the problem statement.
3. Verify with a targeted check (inline python3 -c assertions or a single targeted test file).
4. Call submit_patch once the fix is complete and verified.
5. Return a short final text response summarising the fix.
""")
    if graph_available:
        p.append(PROMPT_GRAPH + "\n")
    layout = sb.sh("find . -maxdepth 3 -not -path '*/.git/*' -not -name '*.pyc' -not -path '*__pycache__*' | head -150", timeout=120)["stdout"]
    p.append("## Workspace Layout\n" + layout)
    return "\n".join(p)


def chat(messages, tools, max_tokens, temperature, timeout=1800):
    body = {"model": MODEL, "messages": messages, "tools": tools, "max_tokens": max_tokens,
            "temperature": temperature, "top_p": 0.95}
    req = urllib.request.Request(API + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_, NUDGE_LEN, NUDGE_NORMAL = nudges()
NUDGE_LEN = NUDGE_LEN.replace("\u2014", "-")


def compact(messages, keep_tail=12):
    """Mirror the harness EventsCompactionConfig: keep system + task prompt + recent exchanges."""
    if len(messages) <= keep_tail + 2:
        return messages
    head, tail = messages[:2], messages[2:]
    cut = 0
    for i in range(len(tail) - keep_tail, -1, -1):
        if tail[i].get("role") == "user" or (tail[i].get("role") == "assistant" and not tail[i].get("tool_calls")):
            cut = i
            break
    dropped = cut
    kept = tail[cut:]
    if dropped:
        head = head + [{"role": "user", "content": f"[{dropped} earlier tool exchanges were compacted away. Do not repeat work you already did; act on what you know.]"}]
    return head + kept


def run_agent(task, system_prompt, tool_names, budget, max_output_tokens=4096, temperature=0.2, trace_path=None):
    sb = Sandbox(image=image_for(task)).start()
    trace = {"instance_id": task["instance_id"], "steps": []}
    try:
        t_boot = time.time()
        boot = bootstrap(sb, task, "baseline")
        trace["bootstrap_seconds"] = round(time.time() - t_boot, 1)
        trace["bootstrap"] = boot["stdout"][-300:]
        ctx = Ctx(sb, task, budget)
        names = [n for n in tool_names if n not in ("search_similar_code", "get_code_neighbors", "get_code_subgraph") or ctx.graph.available]
        tools = tool_schema(names)
        user = build_agent_prompt(sb, task, budget, ctx.graph.available)
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]
        nudges, turns = 0, 0
        t0 = time.time()
        while True:
            if ctx.remaining_time() <= 0 or turns >= budget.get("turns", 500):
                trace["stop"] = "budget"
                break
            turns += 1
            if len(messages) > 26:
                messages = compact(messages)
            try:
                resp = chat(messages, tools, max_output_tokens, temperature)
            except Exception as ex:
                trace["stop"] = f"model_error: {ex}"
                break
            ch = resp["choices"][0]
            msg = ch["message"]
            fin = ch.get("finish_reason", "")
            tcs = msg.get("tool_calls") or []
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             **({"tool_calls": tcs} if tcs else {})})
            step = {"turn": turns, "finish_reason": fin, "content": (msg.get("content") or "")[:400],
                    "tool_calls": [], "timings": resp.get("timings", {})}
            if tcs:
                nudges = 0
                for tc in tcs:
                    fnc = tc["function"]
                    try:
                        args = json.loads(fnc.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    out = ctx.call(fnc["name"], args)
                    step["tool_calls"].append({"name": fnc["name"], "args": {k: str(v)[:200] for k, v in args.items()},
                                               "result": json.dumps(out)[:600]})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", fnc["name"]),
                                     "name": fnc["name"], "content": json.dumps(out)[:6000]})
                trace["steps"].append(step)
                if ctx.patch_submitted:
                    trace["stop"] = "submit_patch"
                    break
            else:
                trace["steps"].append(step)
                if ctx.patch_submitted:
                    trace["stop"] = "submit_patch"
                    break
                nudges += 1
                if nudges > 3:
                    trace["stop"] = "max_nudges"
                    break
                messages.append({"role": "user", "content": NUDGE_LEN if fin in ("length", "MAX_TOKENS") else NUDGE_NORMAL})
        trace["agent_seconds"] = round(time.time() - t0, 1)
        trace["turns"] = turns
        trace["tool_calls_used"] = ctx.tool_calls_used
        if not ctx.patch_submitted:
            sb.sh("cd /workspace && git add -N .", timeout=120)
            ctx.submitted_patch = sb.sh("cd /workspace && git diff HEAD", timeout=120)["stdout"]
            trace["fallback_patch"] = True
        trace["patch_size"] = len(ctx.submitted_patch)
        patch = ctx.submitted_patch
    finally:
        sb.stop()
    if trace_path:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        Path(trace_path).write_text(json.dumps(trace, indent=1))
    return patch, trace
