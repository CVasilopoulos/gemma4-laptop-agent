#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}" || exit 1
out=$1
shift
mkdir -p "$out"
setsid nohup /usr/bin/python3 code/harness/run_queue.py --queue "$out/queue.txt" --out "$out" "$@" >> "$out/run.log" 2>&1 < /dev/null &
echo "launched $!"
