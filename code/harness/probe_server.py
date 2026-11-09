import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_queue as RQ
import laptop_agent as LA
import agent_runner as AR

model = sys.argv[1]
RQ.start_server(model, 2, 32768, 4096)
try:
    tools = AR.tool_schema(["run_command", "read_file", "submit_patch"])
    msgs = [{"role": "system", "content": "You are a coding agent. Use tools."},
            {"role": "user", "content": "List the files in /workspace with a shell command, then stop."}]
    for thinking in (True, False):
        cfg = {"model": model, "thinking": thinking, "temperature": 0.2, "top_p": 0.95, "seed": 1, "max_output_tokens": 4096}
        r = LA.post(LA.request_body(cfg, msgs, tools))
        m = r["choices"][0]["message"]
        print(json.dumps({"thinking": thinking, "finish": r["choices"][0].get("finish_reason"), "content": (m.get("content") or "")[:200],
                          "reasoning_chars": len(m.get("reasoning_content") or ""), "reasoning_head": (m.get("reasoning_content") or "")[:150],
                          "tool_calls": m.get("tool_calls"), "timings": r.get("timings"), "usage": r.get("usage"),
                          "malformed": LA.malformed_response(m, ["run_command", "read_file", "submit_patch"])}, indent=1))
        r2 = LA.post(LA.request_body(cfg, msgs, tools))
        print("repeat timings", r2.get("timings"))
    print("gtt_own_GiB", RQ.state["own_gtt"] / 1024 ** 3)
finally:
    RQ.stop_server()
