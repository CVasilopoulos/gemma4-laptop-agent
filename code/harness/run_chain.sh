#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}" || exit 1
for d in "$@"; do
  [ -f results/laptop/STOP ] && { echo "$(date -Is) STOP present, chain halted before $d" >> results/laptop/chain.log; exit 0; }
  echo "$(date -Is) start $d" >> results/laptop/chain.log
  /usr/bin/python3 code/harness/run_queue.py --queue "$d/queue.txt" --out "$d" --slots 2 >> "$d/run.log" 2>&1 < /dev/null
  echo "$(date -Is) end $d rc=$?" >> results/laptop/chain.log
done
echo "$(date -Is) chain finished" >> results/laptop/chain.log
