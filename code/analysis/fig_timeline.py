import csv, json, sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(sys.argv[2])
TASK = sys.argv[1] if len(sys.argv) > 1 else "fastapi_14372"
RUN = ROOT / "run"
OUT = ROOT / "out"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
LINE, LOOP = "#2a78d6", "#eb6834"
MIN_RUN = 5


def series(policy):
    t = json.loads((RUN / "traces" / f"{TASK}__26b-a4b__{policy}__1.json").read_text())
    ctx, compactions, n_agent = [], [], 0
    for r in t["requests"]:
        if r.get("kind") == "agent":
            n_agent += 1
            ctx.append((n_agent, r.get("prompt_tokens") or ((r.get("prompt_n") or 0) + (r.get("cache_n") or 0))))
        elif r.get("kind") == "compaction":
            compactions.append(n_agent + 0.5)
    calls = [(s["turn"], c["name"], json.dumps(c["args"], sort_keys=True)) for s in t["steps"] for c in s["tool_calls"]]
    bands, start = [], 0
    for i in range(1, len(calls) + 1):
        if i == len(calls) or calls[i][1:] != calls[start][1:]:
            if i - start >= MIN_RUN:
                bands.append((calls[start][0], calls[i - 1][0]))
            start = i
    return t, ctx, compactions, bands


rows = json.loads("[" + ",".join(l for l in (RUN / "results.jsonl").read_text().splitlines() if f'"{TASK}|' in l) + "]")
outcome = {r["policy"]: r for r in rows}
plt.rcParams.update({"font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "font.family": "DejaVu Sans"})
fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.8), sharex=True, sharey=True, facecolor=SURFACE)
table = []
for ax, policy in zip(axes, ["adk", "cached"]):
    t, ctx, comps, bands = series(policy)
    ax.set_facecolor(SURFACE)
    for a, b in bands:
        ax.axvspan(a - 0.5, b + 0.5, color=LOOP, alpha=0.22, lw=0)
    for c in comps:
        ax.axvline(c, color=INK2, lw=0.8, ls=(0, (3, 3)))
    xs, ys = zip(*ctx)
    ax.plot(xs, ys, color=LINE, lw=1.4)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    r = outcome[policy]
    verdict = "resolved" if r["resolved"] else "not resolved"
    ax.set_title(f"{policy}: {verdict}, {r['turns']} turns, {r['compactions']} compactions, {r['agent_seconds'] / 60:.0f} min",
                 loc="left", fontsize=9, color=INK)
    ax.set_ylabel("prompt tokens")
    for x, y in ctx:
        table.append({"policy": policy, "turn": x, "prompt_tokens": y, "compaction_before_next_turn": any(abs(c - (x + 0.5)) < 1e-9 for c in comps),
                      "in_repeat_run": any(a <= x <= b for a, b in bands)})
axes[-1].set_xlabel("agent turn")
axes[0].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
handles = [Line2D([0], [0], color=LINE, lw=1.4, label="prompt tokens per turn"),
           Line2D([0], [0], color=INK2, lw=0.8, ls=(0, (3, 3)), label="compaction"),
           Patch(facecolor=LOOP, alpha=0.22, label=f"same tool call repeated {MIN_RUN}+ times in a row")]
fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=8, bbox_to_anchor=(0.5, 0.0))
fig.suptitle(f"{TASK}, Gemma 4 26B-A4B, thinking off (exploratory task)", x=0.01, ha="left", y=0.995, fontsize=9.5, color=INK)
fig.tight_layout(rect=(0, 0.06, 1, 0.95))
OUT.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT / f"fig_timeline_{TASK}.png", dpi=200, facecolor=SURFACE)
with open(OUT / f"fig_timeline_{TASK}.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(table[0]))
    w.writeheader()
    w.writerows(table)
print("saved", OUT / f"fig_timeline_{TASK}.png", len(table), "rows")
