# gemma4-laptop-agent

Code, paper and pre-registration for "What the context window forgets: a Gemma 4 coding agent on a laptop chip", an entry in the Kaggle Gemma 4 Developer Agent competition (Paper Track). The study runs Gemma 4 E2B, E4B and 26B-A4B (Google's QAT Q4_0 GGUF files) as a coding agent on one Ryzen AI 9 HX 370 mini PC, with the model on the integrated Radeon 890M through llama.cpp's Vulkan backend.

The agent traces and result files live in the Kaggle dataset `chrisvas123/gemma4-laptop-agent-traces`. Raw results, the 42 GB of competition data and the models are not in this repository.

## What is here

| Path | What it is |
|---|---|
| `paper/paper.md` | The paper (Kaggle Writeup text) |
| `paper/fig_timeline_fastapi_14372.png`, `.csv` | Figure 1 and its plotted data |
| `paper/preregistration_2026-11-01.md` | The frozen plan and pre-registration, unedited (sha256 below) |
| `code/harness/sandbox_lib.py` | Docker sandbox and grader (patch application, targeted pytest) |
| `code/harness/laptop_agent.py` | Agent loop with the `adk`, `cached`, `adk_task` and `adk_calls` context policies |
| `code/harness/agent_runner.py` | Tool definitions and task prompt shared by the agent loop |
| `code/harness/harness_text.py` | Loads the reference harness's prompt sections and nudges from the competition's `HARNESS_README.md` at run time |
| `code/harness/run_queue.py` | Runs a queue of `task model policy seed` lines against llama-server and grades each run |
| `code/harness/install_deps_v3.py` | Version-aware installer, per-task calibration list (`CALIBRATION`) and shim selection |
| `code/harness/install_deps.py`, `install_deps_v2.py` | Earlier installer versions, kept for the fidelity history |
| `code/harness/build_base_images.sh` | Builds the per-repository base images (`swebench-sandbox-v3-*`) |
| `code/harness/fidelity_v3.sh`, `sweep_subset.py`, `sweep_gold.py` | Reference-patch and empty-patch fidelity sweeps |
| `code/harness/make_subset.py` | Draws the 60-task stratified sample |
| `code/harness/run_chain.sh`, `run_chain2.sh`, `run_stretch.sh`, `launch_queue.sh` | Batch launch scripts for the queues used in the paper |
| `code/harness/power_logger.sh` | 1 Hz package-power and GTT logger (amdgpu hwmon) |
| `code/harness/probe_server.py` | Quick llama-server smoke test |
| `code/harness/shims/` | pytest asyncio shim and unroutable-address shim used by the grader |
| `code/analysis/summarize.py` | Resolve rate, Wilson interval, malformed rate, time split, Wh per task |
| `code/analysis/confirmatory.py` | Pre-registered paired analysis, exact McNemar, per-arm statistics |
| `code/analysis/ablation.py`, `make_ablation_queue.py` | Post-hoc ablation queue and analysis |
| `code/analysis/fig_timeline.py` | Regenerates Figure 1 |
| `code/release/export_traces.py` | Builds the dataset from raw results: the system prompt is replaced by its hash, bootstrap output is dropped, and problem-statement text quoted by the model is redacted (needs the competition's `data/raw/tasks.jsonl`) |
| `code/prompts/v1_testfirst.md` | An earlier system prompt of ours, not used in the paper's runs |
| `code/fetch_models.sh` | Downloads the three GGUF files from Hugging Face |

The competition's sample system prompt, its problem statements, reference patches and test patches are not in this repository or the dataset. The harness reads the system prompt from the competition data at run time; its sha256 is `f1cbf7943ee9687382321b1dfd9cd88581d608c1c8b8e6ea61a763bd1a3234af`. Problem-statement fragments that the model quoted in its own text or tool arguments are redacted in the dataset (see its README).

The reference harness's task-prompt sections, execution rules and continuation nudges are not in this repository either. `code/harness/harness_text.py` reads them at run time from `data/raw/HARNESS_README.md` under `GEMMA_ROOT`, the harness guide that ships with the competition data, and fails with an error naming that path if the file is absent. Participants get it by accepting the competition rules and downloading the competition data.

## Where everything lives: GEMMA_ROOT

Every script finds the project through the `GEMMA_ROOT` environment variable (default `~/competitions/gemma-4-agent`). Point it at a clone of this repository, then add the data, models and results inside it (they are in `.gitignore`):

```
export GEMMA_ROOT=$PWD
mkdir -p models logs/power results/laptop results/v3
```

Expected layout under `GEMMA_ROOT`: `code/` (this repository), `data/raw/` (competition data), `data/overlay_wheels/`, `models/`, `logs/power/`, `results/`.

## Re-running the analysis without the GPU

Download the dataset, then map its files into `GEMMA_ROOT`:

```
cp -r <dataset>/runs/* "$GEMMA_ROOT/results/laptop/"
cp <dataset>/subset_60_seed20261101.txt <dataset>/sound_tasks.txt "$GEMMA_ROOT/results/laptop/"
cp <dataset>/power/power_*.csv "$GEMMA_ROOT/logs/power/"
```

Then, from `GEMMA_ROOT`:

```
python3 code/analysis/confirmatory.py results/laptop/main_26b
python3 code/analysis/summarize.py results/laptop/stretch_26b_think1024/results.jsonl
python3 code/analysis/ablation.py
```

`confirmatory.py` needs no third-party packages and reproduces the pre-registered test and the Table 3 figures; energy comes from `logs/power/`. For Figure 1 (needs matplotlib), put the run under a `run/` directory and call the script with that parent:

```
mkdir -p /tmp/fig && ln -s "$GEMMA_ROOT/results/laptop/main_26b" /tmp/fig/run
python3 code/analysis/fig_timeline.py fastapi_14372 /tmp/fig
```

## Re-running the experiments

You need Docker, Python 3 with numpy (for the code-graph tools), a Vulkan build of llama.cpp at `/opt/llama.cpp/build/bin/llama-server` (`run_queue.py` starts its own server on port 8082 and stops it afterwards), an AMD GPU with the amdgpu power sensor for the energy numbers, and about 58 GiB of unified memory for 26B-A4B.

1. Accept the competition rules and download its data into `data/raw/` (`HARNESS_README.md`, `tasks.jsonl`, `snapshots/`, `graphs/`, `embeddings/`, `sandbox/`, `docker/`, `wheels/`, `sample_submission/`).
2. Build the competition sandbox image from its Dockerfile (`data/raw/docker/Dockerfile.public`, whose context holds `imp.py`, `telnetlib.py` and a `wheels/` directory) and tag it `swebench-sandbox-local:latest`.
3. Fetch the 19 test-only wheels from PyPI into `data/overlay_wheels/` (`pip download --no-deps`, cp313 x86-64 or any-platform builds): annotated_doc 0.0.5, blinker 1.9.0, brotlicffi 1.2.1.0, cffi 2.1.1, decorator 5.3.1, greenlet 3.5.6, httpbin 0.10.4, orjson 3.13.0, pycparser 3.12, Pygments 2.15.1, 2.16.1, 2.18.0, 2.19.2, pytest 8.4.2, pytest_httpbin 2.1.0, python_multipart 0.0.33, shellingham 1.5.4, six 1.17.0, ujson 6.0.0.
4. Build and validate the grader, then draw the sample. The sweeps need `.venv/bin/python` under `GEMMA_ROOT`:

```
code/harness/build_base_images.sh
python3 -m venv .venv
code/harness/fidelity_v3.sh
python3 code/harness/make_subset.py "$GEMMA_ROOT" 60 20261101
```

5. Download the models, start the power logger and run a queue. A queue file holds lines of `task model policy seed` (the queues used in the paper are in the dataset, under `runs/<run>/queue.txt`):

```
code/fetch_models.sh
setsid nohup code/harness/power_logger.sh >/dev/null 2>&1 < /dev/null &
python3 code/harness/run_queue.py --queue results/laptop/main_26b/queue.txt --out results/laptop/main_26b --slots 2
```

The thinking arms add `--thinking --reasoning-budget 1024` (or 4096). A file `results/laptop/STOP` halts a running queue. Sandboxes run with 4 GiB and 2 vCPUs and no network.

## Pre-registration

`paper/preregistration_2026-11-01.md` is the document frozen before any confirmatory run. Its sha256 is `32717a8ea2cdffb88e02c39a91d2fb0f85ceadfda58ec5a2dfc3cdcc79e571a3`.

```
sha256sum paper/preregistration_2026-11-01.md
```

## Licence and attribution

The code, paper and pre-registration are released under the Apache-2.0 licence (see `LICENSE`). The ADK summary prompt in `laptop_agent.py` follows google-adk (Apache-2.0). The per-task pins and asyncio shim in the grader follow Z. Kirchner's public notebook "Gemma 4 Agent: Do Gold Patches Pass Locally?". Agent traces in the dataset carry fragments of fastapi, rich, requests and httpx under those projects' own licences.

AI-assisted, human-directed.
