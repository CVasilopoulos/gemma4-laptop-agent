# Gemma 4 Developer Agent Paper Track: plan

Written 2026-10-31. Final submission deadline 2026-11-12 23:59 UTC. Our target is to submit on 2026-11-11.

## Status and how to resume (updated 2026-11-01 04:45 EET)

- **Approvals in hand:** model downloads, PyPI test wheels, and 8081 kept idle Nov 3-8 (relayed by the coordinator, 2026-10-31).
- **Harness v3 is done:** gold 127/129, empty 2/129. Details are in NOTES.md, "Harness v3".
- **Speed** (`llama-bench`, results in `results/laptop/bench/`):

| Model | Decode, t/s (d0 / d8k / d16k) | Prefill, t/s (d0 / d8k / d16k) |
|---|---|---|
| 26B-A4B | 27.0 / 22.1 / 20.5 | 433 / 349 / 317 |
| E4B | 24.4 / 22.0 / 20.1 | 650 / 546 / 472 |
| E2B | 46.5 / 41.5 / 39.1 | 1289 / 858 / 649 |

  H1 holds: 26B-A4B decodes as fast as E4B or faster, and its prefill is 1.50x slower.
- **Gate G1 passed on 2026-11-01 04:31 EET, with the `cached` policy and thinking off.** Details are in NOTES.md, "Gate G1". The coordinator was told the config changed and may overrule.

| Arm | Folder | Resolved | Malformed | Wh/task |
|---|---|---:|---:|---:|
| `adk`, thinking 4096 | `g1_think4096` | 0/2 (stopped: about 5 min per turn) | 0% | 25.8 |
| `adk`, thinking off | `g1b_nothink` | 1/10 | 1.8% | 20.8 |
| `cached`, thinking off | `g1c_cached` | **5/10** | 1.2% | 17.8 |

  - Paired: 4 tasks resolved only under `cached`, 0 only under `adk`.
- **Main runs started 2026-11-01 04:31 EET** as one chain: `code/harness/run_chain.sh results/laptop/main_26b results/laptop/main_e4b results/laptop/main_e2b`. Progress is in `results/laptop/chain.log`.
  - main_26b covers the 60-task subset under both `adk` and `cached`, alternating task by task. Its first 20 rows were copied from G1b and G1c.
  - **Estimated end: around Nov 3 evening EET.**
- **To resume any queue:** run `code/harness/launch_queue.sh <outdir> --slots 2`. It reads `<outdir>/queue.txt` and skips finished keys. The queue runs under /usr/bin/python3, because .venv has no numpy. To stop it, create `results/laptop/STOP`.
  - Never use `pkill -f` with a pattern that also appears in the ssh command line: it kills the session.
- **Analysis:** `python3 code/analysis/summarize.py <results.jsonl ...>` gives resolve rate, Wilson CI, malformed rate, time split and Wh per task. Energy comes from `logs/power/`.

## Decision: a narrow GO, for angle A only, with a kill gate on 2026-11-03 12:00 UTC

- **Chance of any prize: about 4%** (plausible range 2 to 7%).
- **Expected value: about $450** (0.04 x the $11.7k average prize).
- **Cost:** about 45 hours of unattended limnos iGPU time, roughly 1.5 days of agent work, and about 1.5 hours of the user's time.
- **If the gate fails, stop.** Do not fall back to angle B or C. Neither is worth the time on its own (see "Angles considered").

### Why the odds are low

- **The field is large.**
  - 441 teams and 470 submissions on 2026-10-31.
  - Past Google writeup hackathons ended with about as many writeups as teams.
  - In MedGemma only 38% of writeups existed 12 days before the deadline. On that pattern, our extrapolation is 600 to 1,200 final papers.
  - There are 3 prizes, and a paper can win only one of them.
- **The serious tier has been publishing since September** and has taken most of the cheap angles. Each of these is already public:
  - harness replicas (Kirchner 127/129, Ruijie Zhu 124/129, busyaprime 119/129);
  - a task-soundness audit and 1,670 trajectories (Gluzdov);
  - an E4B failure taxonomy over 387 episodes (Kouzios);
  - 596 pre-registered runs (Wold);
  - a run-to-run variance audit (tokimwc);
  - fresh task sets (FreshSWE-Py with 663 tasks, OOD-45);
  - localization benchmarks (Ali Hassan, codegraph-loc);
  - the g4kit Ollama/MLX proxy.

### Why it is still worth a narrow GO

- **One niche is empty.** Nobody has measured Gemma 4 SWE agents on consumer hardware:
  - no 26B-A4B numbers,
  - no energy numbers,
  - no iGPU or APU numbers.
- **The niche fits the track's stated motivation exactly:** "put powerful coding assistants in the hands of millions more developers".
- **We are well placed to fill it:**
  - limnos is a 2024 consumer APU (Ryzen AI 9 HX 370 with a Radeon 890M, 58 GiB of unified memory);
  - it has a readable package-power sensor (amdgpu `power1_average`);
  - we already have a working Vulkan llama.cpp stack.
- **The theory favours the MoE.** Our measured bandwidth model, `t/s = 73 / GiB read per token`, predicts that 26B-A4B (13.45 GiB QAT Q4_0, about 4B active) generates as fast as E4B or faster, while being a far stronger model.

## Harness diagnosis (measured 2026-10-31)

The replica in `code/harness/` was at gold 58/129 (0.450) and empty 2/129 (0.016).

### Why the 71 gold patches failed

| Root cause of the 71 failures | Count | Class |
|---|---:|---|
| Starlette 1.x installed despite the snapshot pin `<0.49`; our resolver ignored version specifiers, so `Router(on_startup)` breaks | 48 | runner bug |
| `setup.py install_requires` not parsed (requests never got urllib3) | 5 | runner bug |
| Test-only packages missing from the published wheelhouse (pytest-httpbin, python-multipart, ujson, orjson) | 7 | missing offline deps |
| Era drift: newest pygments recolours rich output (7); Python 3.13 attrs error message (1) | 8 | environment difference |
| Async tests marked for anyio, but the harness command passes `-p no:anyio` | 3 | harness-command conflict |
| Timeouts or flaky tests | 0 | none seen |

### What the v2 resolver fixed

- **The fix:** `code/harness/install_deps_v2.py`. It installs the newest wheel that the snapshot's own pins allow, and parses `setup.py`.
- **How to run it:** set `INSTALL_DEPS=install_deps_v2.py`. Subsets run with `code/harness/sweep_subset.py`.
- **Result:** gold went from 58 to **97/129 (0.752)**, with no regressions.
  - Results are in `results/v2/fidelity_gold.jsonl`.
  - Logs are in `results/v2/fidelity_gold/`.

### The 32 tasks still failing

| Remaining cause | Count | Planned fix |
|---|---:|---|
| python-multipart missing (unmasked once Starlette was fixed) | 11 | add the wheel |
| pytest-httpbin missing | 8 (requests) | add the wheel |
| 4 connect-timeout tests get ENETUNREACH under `--network none` | same 8 requests tasks | internal Docker network whose subnet covers 10.255.255.1, so connect times out instead |
| pygments era drift | 7 | per-task older pygments |
| anyio conflict | 3 | per-task calibration |
| ujson / orjson missing | 2 | add the wheels |
| attrs message drift | 1 | |

- **Effort to reach gold 0.95 or better and empty 0.02 or lower: half a day to one day.**
- **The empty-patch rate got worse under v2:** 5/129 (0.039), up from 2/129.
  - The new passes are requests_7315, requests_7427, requests_7309, fastapi_14851 and fastapi_14077.
  - Now that the newest wheels in range are installed, their test patches no longer tell a fixed tree from an unfixed one.
  - Getting back to 2 or fewer needs the same kind of per-task era pins as the gold side.
  - In v1 these tasks failed for the wrong reason (missing urllib3, wrong Starlette).
- **rich_3772 hangs under the empty patch,** until the 600 s pytest timeout.
- **The sound set today is 92 tasks:** gold passes and empty fails (fastapi 49, rich 40, requests 2, httpx 1). The list is in `results/v2/sound_tasks.txt`.
- **The harness is an instrument, not a prize angle.**
  - Kirchner published a 127/129 replica on 2026-10-22, with Apache-2.0 wheel and Python datasets.
  - The hosts published the official wheelhouse.

## Angles considered

| Angle | Prize it targets | Novelty | Quality | Relevance | Verifiability | Clarity | Average | Odds of any prize |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **A. A laptop-class SWE agent:** Gemma 4 E2B / E4B / 26B-A4B on one consumer APU (resolve rate, minutes and watt-hours per fix, where the time goes), plus cache-stable context management | Best Paper, or Application at a stretch | 3 | 3.5 | 4.5 | 4 | 3.5-4 | ~3.7 | ~4% |
| B. Cache-stable context compaction only (trace replay, no new pass-rate data) | Best Paper | 2.5-3 | 3.5 | 3 | 4 | 4 | ~3.4 | ~1.5% |
| C. A validated consumer-hardware replica harness | Best New Resource | 1 (preempted) | 3 | 3 | 4 | 3.5 | ~2.9 | <0.5% |

Only A clears the bar. B and C become sections of A rather than papers of their own.

## Angle A in detail

### Claims to test (revised after G1)

- **H1, speed: confirmed by the benchmarks.** 26B-A4B decodes at least as fast as E4B. Its prefill is 1.5x slower.
- **H2, capability:** on the 60-task subset, 26B-A4B (`cached`) resolves several times more tasks than E4B and E2B.
- **H3, cost:** for each model and policy we report minutes per resolved task and Wh per resolved task, from package power at 1 Hz.
- **H4, revised: decode, not prefill, is the bottleneck.**
  - With thinking 4096, decode is 74% of agent time and prefill 3%, at a 96% prefix-cache hit rate.
  - With thinking off, prefill rises to about 25%. Most of the increase comes from the ADK-style summary, which is a fresh prompt that misses the cache.
- **H5, the main method claim: context policy decides capability on small-context local agents.**
  - **`adk`** reproduces the scorer's ADK compaction: at 14,336 prompt tokens it requests a fresh-prompt, text-only summary at temperature 1.0 and keeps the last 5 events. The task statement and tool results are lost, and the agent falls into repetition loops.
  - **`cached`** asks for the progress note as a continuation of the cached conversation, so only the decode is paid. It keeps system + task prompt + note + the last 5 events.
  - **Test:** paired over the 60 tasks with McNemar. We also compare loop rate, prefill share and Wh per resolved task.
- **H6, the price of thinking:** on a laptop, the official thinking budget (4096) roughly quadruples the time per turn. Measured on a small arm if time allows.

### Models

All three are Google's official QAT Q4_0 GGUFs. They are Apache-2.0 and not gated.

| Model | File | Size |
|---|---|---|
| E2B | `gemma-4-E2B_q4_0-it.gguf` | 3.12 GiB |
| E4B | `gemma-4-E4B_q4_0-it.gguf` | 4.80 GiB |
| 26B-A4B | `gemma-4-26B_q4_0-it.gguf` | 13.45 GiB |

- **Where the files are:** `~/competitions/gemma-4-agent/models/`, **not** `/opt/models`. The user's 8081 router auto-loads anything in `/opt/models`.
- **Verified:** all three sha256 match the Hugging Face LFS ids (`models/SHA256SUMS`). The 26B download was cut short once and resumed with `code/resume_26b.sh`.
- **How they are served:** our own llama-server on port 8082, started and stopped by `code/harness/run_queue.py`, with `--jinja`, `-fa on`, `--reasoning-format deepseek`, 2 slots of 32k each. **Port 8081 is never touched.**

### Task set

- **Task set S:** the tasks whose gold patch passes and whose empty patch fails in harness v3. That is 125 tasks: fastapi 65, rich 47, requests 12, httpx 1.
- **Sample:** N = 60, stratified by repo with seed 20261101: fastapi 31, rich 22, requests 6, httpx 1. It is written to `results/laptop/subset_60_seed20261101.txt` before any run.

### Runs (revised after G1)

| Folder | Model | Policies | Tasks | Runs | Status |
|---|---|---|---:|---:|---|
| `main_26b` | 26B-A4B | `adk` and `cached`, paired | 60 | 120 | running (20 seeded from G1) |
| `main_e4b` | E4B | `adk` | 60 | 60 | queued |
| `main_e2b` | E2B | `adk` | 60 | 60 | queued |

- **Settings:** thinking off, temperature 0.2, top_p 0.95, 2 slots.
- **Budgets:** official parity, meaning 60 minutes, 500 turns, 300 s per command, 5,000-character output cap, and a 4 GiB / 2 vCPU sandbox with no network.
- **Time:** about 18 minutes per run in effect, so about 60 hours in total.
- **If time remains after the chain** (stretch, in this order):
  1. E4B with `cached` (60 runs), so the model comparison is under the better policy as well.
  2. 26B-A4B `cached` with thinking budget 1024, on 20 tasks (H6).
  3. A second seed of 26B-A4B `cached` on 30 tasks.
- **What we log:** per-turn llama-server timings (prompt, cached and decode tokens, ms for each), tool time, compactions, and power samples.
- **Dropped:** the trace-replay study. G1 showed the prefill premise was wrong, and the end-to-end paired runs answer H5 directly.

### Stretch: 31B anchor

- Only if the main runs finish by 2026-11-06.
- 31B QAT on one Kaggle L4 (llama.cpp CUDA build recipe in memory `kaggle-gpu-kernel-recipes`).
- 30 tasks, via the official swegemma subprocess sandbox plus the wheelhouse.
- About 5 GPU-hours.

## Milestones

| When (EET) | Milestone | State |
|---|---|---|
| Oct 31 | Harness v3: gold 127/129, empty 2/129 | done |
| Oct 31 | Benchmarks for all three models | done |
| Nov 1 04:31 | Gate G1 | passed (`cached`, thinking off): 5/10, 1.2% malformed, 27 t/s |
| Nov 1 to Nov 3 | Main chain: 26B-A4B paired, then E4B, then E2B. The confirmatory analysis is pre-registered at the end of this file. | running |
| Nov 3 to Nov 7 | Stretch arms, as listed above | |
| Nov 4 to Nov 9 | Paper draft (at most 3,000 judged words), figures, release bundle, in `docs/paper/draft/` | |
| Nov 10-11 | User review. User creates the Kaggle Writeup and clicks submit. | |

## Paper outline (about 2,800 words)

1. Introduction (350): can a single consumer chip run a useful SWE agent?
2. Setup (500): hardware, models, runner parity and its deviations, harness fidelity numbers, the subset.
3. Capability against cost (600): resolve rate, minutes and Wh per fix, MoE against dense.
4. Where the time goes (450): prefill against decode against tools, context growth, the cache cost of compaction.
5. Cache-stable context management (450): design, replay results, end-to-end check.
6. Limitations (250): N = 60, replica rather than official scorer, Q4 quantisation, one machine.
7. Release (200).

Writing rules:

- Every number comes from a logged file.
- No filler, because hosts said that "slop" scores badly.
- The appendix may not be read, so everything load-bearing goes in the main text.

## Release and compliance

- **Code:** harness v3, runner, power logger and analysis, under Apache-2.0 (which is also the winner licence).
- **Traces:** the host allows traces if upstream licences are cited (2026-10-27). We strip problem statements and hints, and ship a script that restores them for people who have accepted the rules.
- **Never redistributed:** problem statements, gold or test patches, official graphs or embeddings.
- **Where it goes:** a public GitHub repo or a public Kaggle notebook. Publishing is the user's call.

## Compute and safety rules for these runs

- **Run at most 1 to 2 tasks at once.** Sandboxes are capped at `--memory 4g --cpus 2`.
- **Memory budget:** model 13.45 GiB plus KV about 3 GiB plus 2 sandboxes at 8 GiB. That fits only while the 12 GB free-memory floor holds. A run pauses if MemAvailable drops below 12 GB, or if another heavy job brings the count above 2.
- **GTT is 31.4 GB in total.** If the user's 8081 router auto-loads Qwen3-30B (18.5 GB) at the same time, the two do not fit together.
  - Before each task, the runner checks `mem_info_gtt_used`. It never queries or touches 8081.
  - It pauses while other models hold GTT.
- **Scheduling:** the grid-7k crons (03:10, 09:10, 15:10) and the 2026-11-02 15:10 one-off get priority. We avoid those windows.

## What the user must do

**Steps 1 to 3 were approved on 2026-10-31,** as relayed by the coordinator. The models live in `~/competitions/gemma-4-agent/models/`. The test wheels are in `data/overlay_wheels/`, with `VERSIONS.txt` and `SHA256SUMS`.

1. **Approve these downloads to limnos `/opt/models`.** All are from huggingface.co and Apache-2.0:
   - `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` / `gemma-4-26B_q4_0-it.gguf` (13.45 GiB)
   - `google/gemma-4-E4B-it-qat-q4_0-gguf` / `gemma-4-E4B_q4_0-it.gguf` (4.80 GiB)
   - `google/gemma-4-E2B-it-qat-q4_0-gguf` / `gemma-4-E2B_q4_0-it.gguf` (3.12 GiB)
2. **Approve one of these for the missing test-only wheels:**
   - about 15 wheels from PyPI (python-multipart, ujson, orjson, pytest-httpbin, httpbin and its dependencies, older pygments), all small; or
   - Kirchner's Apache-2.0 Kaggle datasets `zbigniewkirchner/gemma-4-agent-local-eval-wheels` and `-pythons`.
3. **Keep the 8081 Qwen server idle during the Nov 3-8 run windows,** or accept that our runs pause while it holds the GPU.
4. **At the end:**
   - review the draft;
   - choose where the release goes (GitHub or Kaggle) and approve publishing it;
   - create the Kaggle Writeup and click submit (by Nov 11).

## Pre-registered confirmatory analysis (fixed 2026-11-01, before any non-G1 main_26b row was read)

**Split**
- **Confirmatory set C (50 tasks):** the tasks in `results/laptop/subset_60_seed20261101.txt` that are not among its first 10 lines.
- **Exploratory set E (10 tasks):** the first 10 lines, i.e. the G1 tasks. They shaped the design of the `cached` policy, so they are reported separately and never pooled into the primary test.

**Primary claim**
- On C, 26B-A4B with `cached` resolves more tasks than 26B-A4B with `adk`.
- Both arms: thinking off, temperature 0.2, top_p 0.95, seed 1, 60-minute budget, harness v3.
- Test: exact two-sided McNemar (binomial on the discordant pairs, p = 0.5), alpha 0.05.
- We report the counts, the discordant pair counts (b = cached only, c = adk only), the p-value, and the resolve rate of each arm with a Wilson 95% interval.
- A task whose run ends in an infrastructure error (not a model error) is re-run in both arms, not dropped. Any such re-run is listed.

**Secondary measures (descriptive, reported for C and E separately)**
1. **Loop rate:** the number of tasks with N or more identical consecutive tool calls, for N = 5 and N = 10. "Identical" means the same tool name and the same arguments as stored in the trace, where each argument value is truncated to 160 characters.
2. Malformed-response rate.
3. Stop reasons.
4. Compactions per task.
5. Mean agent minutes per task; minutes and Wh per resolved task, from package power at 1 Hz, shared out among concurrent jobs.
6. Time split: prefill, decode, compaction, tools.

**Model comparison**
- E4B (`adk`) and E2B (`adk`) against 26B-A4B (`adk`) on C, paired, descriptive with McNemar.
- If the stretch arm E4B `cached` runs, it is compared with E4B `adk` in the same way. That comparison is exploratory, because it was not part of the primary claim.

**Also reported as results, not footnotes**
- `adk` resolved 1/10 on E.
- The `adk` repetition loops (for example the 426-repeat case).
- The thinking-4096 arm: 0/2, stopped early.
- The corrected premise: the bottleneck is decode and thinking, not prefill.
- Two deviations from the gate config: thinking off, and the gate judged on `cached`.

**Harness changes**
- From this point, any change to the harness, loop handling, nudges or parsing applies to both arms or to neither.
- Any such change, and the runs it affects, is listed in NOTES.md.

**Interim looks**
- The coordinator asked to be told early if `adk` beats `cached` on C. Interim counts are read only for that.
- There is no stopping or modification rule based on them, and the primary test uses all 50 pairs.
