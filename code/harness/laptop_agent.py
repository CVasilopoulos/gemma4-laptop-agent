import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

os.environ.setdefault("SWE_REPEAT_GUARD", "0")
import agent_runner as AR
from harness_text import nudges
from sandbox_lib import Sandbox, bootstrap, image_for

API = os.environ.get("LAPTOP_API", "http://127.0.0.1:8082/v1")
GRAPH_TOOLS = ("search_similar_code", "get_code_neighbors", "get_code_subgraph")
TOOLS = ["run_command", "read_file", "edit_file", "write_file", "get_status", "submit_patch", *GRAPH_TOOLS]
NUDGE_UNCLOSED, NUDGE_LENGTH, NUDGE_NORMAL = nudges()
ADK_SUMMARY_PROMPT = ("The following is a conversation history between a user and an AI agent. Please summarize the conversation, "
                      "focusing on key information and decisions made, as well as any unresolved questions or tasks. The summary "
                      "should be concise and capture the essence of the interaction.\n\n")
CACHED_SUMMARY_REQUEST = ("Context is getting long and older turns will be removed. Reply with text only, no tool call: a compact "
                          "progress note listing the files and line ranges you inspected, what you learned about the root cause, "
                          "every edit you made (file and what changed), test commands you ran with their outcome, and the next step.")
CACHED_RESUME = "Continue the task from the progress note above using the available tools."
TOOL_MARKUP = re.compile(r"<\|?tool_call|tool_call\|>|\bcall:[A-Za-z_]+\{")


def post(body, timeout=1800):
    req = urllib.request.Request(API + "/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    out["_wall_ms"] = round((time.time() - t0) * 1000, 1)
    return out


def request_body(cfg, messages, tools, sampling=None):
    body = {"model": cfg["model"], "messages": messages, "max_tokens": cfg["max_output_tokens"], "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": cfg["thinking"]}}
    if tools:
        body["tools"] = tools
    body.update(sampling if sampling is not None else {"temperature": cfg["temperature"], "top_p": cfg["top_p"], "seed": cfg["seed"]})
    return body


def timing_row(kind, resp):
    t = resp.get("timings", {}) or {}
    u = resp.get("usage", {}) or {}
    return {"kind": kind, "wall_ms": resp.get("_wall_ms"), "prompt_n": t.get("prompt_n"), "prompt_ms": t.get("prompt_ms"),
            "cache_n": t.get("cache_n"), "predicted_n": t.get("predicted_n"), "predicted_ms": t.get("predicted_ms"),
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens")}


def context_tokens(row):
    if row.get("prompt_tokens"):
        return row["prompt_tokens"]
    return (row.get("prompt_n") or 0) + (row.get("cache_n") or 0)


def retention_start(messages, keep):
    i = max(2, len(messages) - keep)
    while i < len(messages) and messages[i].get("role") == "tool":
        i += 1
    return i


def text_events(messages):
    lines = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and c.strip() and m.get("role") in ("user", "assistant"):
            lines.append(f"{'user' if m['role'] == 'user' else 'model'}: {c.strip()}")
    return "\n".join(lines)


def call_events(messages, result_chars=4000):
    lines = []
    for m in messages:
        role = m.get("role")
        c = m.get("content")
        if role in ("user", "assistant") and isinstance(c, str) and c.strip():
            lines.append(f"{'user' if role == 'user' else 'model'}: {c.strip()}")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                f = tc.get("function", {})
                lines.append(f"model called {f.get('name')}({f.get('arguments') or ''})")
        if role == "tool" and isinstance(c, str):
            lines.append(f"{m.get('name')} returned: {c[:result_chars]}")
    return "\n".join(lines)


def compact_adk(cfg, messages, log, keep_task=False, with_calls=False):
    start = retention_start(messages, cfg["retention"])
    old, kept = messages[1:start], messages[start:]
    history = call_events(old) if with_calls else text_events(old)
    body = request_body(cfg, [{"role": "user", "content": ADK_SUMMARY_PROMPT + history}], None,
                        sampling={"temperature": 1.0, "top_p": 0.95, "top_k": 64})
    body["max_tokens"] = 2048
    body["chat_template_kwargs"] = {"enable_thinking": False}
    resp = post(body)
    log.append(timing_row("compaction", resp))
    summary = (resp["choices"][0]["message"].get("content") or "").strip()
    head = messages[:2] if keep_task else [messages[0]]
    return head + [{"role": "user", "content": "[Summary of earlier conversation]\n" + summary}] + kept


def action_digest(messages, limit=30):
    calls = []
    for m in messages[2:]:
        for tc in m.get("tool_calls") or []:
            f = tc.get("function", {})
            calls.append(f"- {f.get('name')}({(f.get('arguments') or '')[:160]})")
    return "Actions so far (most recent last):\n" + "\n".join(calls[-limit:])


def compact_cached(cfg, messages, tools, log):
    probe = messages + [{"role": "user", "content": CACHED_SUMMARY_REQUEST}]
    body = request_body(cfg, probe, tools)
    body["max_tokens"] = 2048
    body["chat_template_kwargs"] = {"enable_thinking": False}
    resp = post(body)
    log.append(timing_row("compaction", resp))
    note = (resp["choices"][0]["message"].get("content") or "").strip()
    if len(note) < 40:
        note = action_digest(messages)
    start = retention_start(messages, cfg["retention"])
    return messages[:2] + [{"role": "assistant", "content": "Progress note:\n" + note},
                           {"role": "user", "content": CACHED_RESUME}] + messages[start:]


def malformed_response(msg, names):
    calls = msg.get("tool_calls") or []
    text = (msg.get("content") or "") + (msg.get("reasoning_content") or "")
    if not calls:
        return bool(TOOL_MARKUP.search(text))
    for tc in calls:
        f = tc.get("function", {})
        if f.get("name") not in names:
            return True
        try:
            json.loads(f.get("arguments") or "{}")
        except Exception:
            return True
    return False


def run_task(task, cfg, trace_path=None):
    sb = Sandbox(image=image_for(task)).start()
    trace = {"instance_id": task["instance_id"], "config": cfg, "requests": [], "steps": [], "compactions": 0}
    patch = ""
    try:
        t_boot = time.time()
        boot = bootstrap(sb, task, "baseline")
        trace["bootstrap_seconds"] = round(time.time() - t_boot, 1)
        trace["bootstrap"] = boot["stdout"][-300:]
        budget = {"time_minutes": cfg["time_minutes"], "turns": cfg["turns"], "tool_calls": None}
        ctx = AR.Ctx(sb, task, budget)
        names = [n for n in TOOLS if n not in GRAPH_TOOLS or ctx.graph.available]
        tools = AR.tool_schema(names)
        messages = [{"role": "system", "content": cfg["system_prompt"]},
                    {"role": "user", "content": AR.build_agent_prompt(sb, task, budget, ctx.graph.available)}]
        nudges, turns, malformed, t0 = 0, 0, 0, time.time()
        ctx.start = t0
        while True:
            if ctx.remaining_time() <= 0 or turns >= cfg["turns"]:
                trace["stop"] = "budget"
                break
            turns += 1
            try:
                body = request_body(cfg, messages, tools)
                body["t_max_predict_ms"] = int(max(5000, ctx.remaining_time() * 1000))
                resp = post(body, timeout=max(60, int(ctx.remaining_time()) + 180))
            except urllib.error.HTTPError as ex:
                detail = ex.read().decode("utf-8", "replace")
                if ex.code == 500 and "Failed to parse tool call" in detail:
                    malformed += 1
                    trace["requests"].append({"kind": "agent", "parse_error": True})
                    trace["steps"].append({"turn": turns, "finish_reason": "tool_call_parse_error", "malformed": True,
                                           "content": detail[:300], "reasoning_chars": 0, "tool_calls": []})
                    nudges += 1
                    if nudges > 3:
                        trace["stop"] = "max_nudges"
                        break
                    messages.append({"role": "user", "content": NUDGE_UNCLOSED})
                    continue
                trace["stop"] = f"model_error: HTTP {ex.code} {detail[:160]}"
                break
            except Exception as ex:
                trace["stop"] = f"model_error: {str(ex)[:200]}"
                break
            row = timing_row("agent", resp)
            trace["requests"].append(row)
            ch = resp["choices"][0]
            msg, fin = ch["message"], ch.get("finish_reason") or ""
            bad = malformed_response(msg, names)
            malformed += bad
            tcs = msg.get("tool_calls") or []
            messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": tcs} if tcs else {})})
            step = {"turn": turns, "finish_reason": fin, "malformed": bad, "content": (msg.get("content") or "")[:300],
                    "reasoning_chars": len(msg.get("reasoning_content") or ""), "tool_calls": []}
            any_valid = False
            for tc in tcs:
                fnc = tc.get("function", {})
                try:
                    args = json.loads(fnc.get("arguments") or "{}")
                except Exception:
                    args = None
                t_tool = time.time()
                out = ctx.call(fnc.get("name", ""), args) if isinstance(args, dict) else {
                    "status": "error", "error_type": "ValidationError", "error_message": "arguments are not valid JSON"}
                any_valid = any_valid or (isinstance(args, dict) and fnc.get("name") in names)
                step["tool_calls"].append({"name": fnc.get("name"), "ms": round((time.time() - t_tool) * 1000, 1),
                                           "status": out.get("status") if isinstance(out, dict) else None,
                                           "args": {k: str(v)[:160] for k, v in (args or {}).items()}})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", fnc.get("name")), "name": fnc.get("name"),
                                 "content": json.dumps(out)})
            trace["steps"].append(step)
            if ctx.patch_submitted:
                trace["stop"] = "submit_patch"
                break
            if any_valid:
                nudges = 0
            else:
                nudges += 1
                if nudges > 3:
                    trace["stop"] = "max_nudges"
                    break
                text = msg.get("content") or ""
                nudge = NUDGE_UNCLOSED if ("<|tool_call" in text and "tool_call|>" not in text) else (
                    NUDGE_LENGTH if fin in ("length", "max_tokens") else NUDGE_NORMAL)
                messages.append({"role": "user", "content": nudge})
            if cfg["policy"] != "none" and context_tokens(row) >= cfg["compaction_tokens"]:
                try:
                    if cfg["policy"] == "cached":
                        messages = compact_cached(cfg, messages, tools, trace["requests"])
                    else:
                        messages = compact_adk(cfg, messages, trace["requests"], keep_task=cfg["policy"] == "adk_task",
                                               with_calls=cfg["policy"] == "adk_calls")
                    trace["compactions"] += 1
                except Exception as ex:
                    trace["compaction_error"] = str(ex)[:200]
        trace["agent_seconds"] = round(time.time() - t0, 1)
        trace["turns"] = turns
        trace["malformed"] = malformed
        trace["tool_calls_used"] = ctx.tool_calls_used
        if not ctx.patch_submitted:
            sb.sh("cd /workspace && git add -N .", timeout=120)
            ctx.submitted_patch = sb.sh("cd /workspace && git diff HEAD", timeout=120)["stdout"]
            trace["fallback_patch"] = True
        patch = ctx.submitted_patch
        trace["patch_size"] = len(patch)
    finally:
        sb.stop()
        if trace_path:
            Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
            Path(trace_path).write_text(json.dumps(trace))
    return patch, trace
