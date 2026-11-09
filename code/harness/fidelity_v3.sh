#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}"
export INSTALL_DEPS=install_deps_v3.py SWE_IMAGE_PREFIX=swebench-sandbox-v3 SWE_PYTEST_TIMEOUT=300
.venv/bin/python code/harness/sweep_subset.py gold all results/v3/fidelity_gold.jsonl 3 > results/v3/gold.out 2>&1
.venv/bin/python code/harness/sweep_subset.py empty all results/v3/fidelity_empty.jsonl 3 > results/v3/empty.out 2>&1
