#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}" || exit 1
log=results/laptop/chain.log
run() {
  d=$1; shift
  [ -f results/laptop/STOP ] && { echo "$(date -Is) STOP present, stretch halted before $d" >> $log; exit 0; }
  echo "$(date -Is) start $d $*" >> $log
  /usr/bin/python3 code/harness/run_queue.py --queue "$d/queue.txt" --out "$d" --slots 2 "$@" >> "$d/run.log" 2>&1 < /dev/null
  echo "$(date -Is) end $d rc=$?" >> $log
}
run results/laptop/stretch_e4b_cached
run results/laptop/stretch_26b_think1024 --thinking --reasoning-budget 1024
echo "$(date -Is) stretch finished" >> $log
