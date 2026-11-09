#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}" || exit 1
log=results/laptop/chain.log
wait_pid=$1
shift
echo "$(date -Is) chain2 waiting for main_26b pid $wait_pid" >> $log
while kill -0 "$wait_pid" 2>/dev/null; do sleep 60; done
echo "$(date -Is) end results/laptop/main_26b (pid $wait_pid exited)" >> $log
[ -f results/laptop/STOP ] && { echo "$(date -Is) STOP present, chain2 halted" >> $log; exit 0; }
python3 code/analysis/make_ablation_queue.py >> $log 2>&1
for d in results/laptop/ablation_26b "$@"; do
  [ -f results/laptop/STOP ] && { echo "$(date -Is) STOP present, chain halted before $d" >> $log; exit 0; }
  echo "$(date -Is) start $d" >> $log
  /usr/bin/python3 code/harness/run_queue.py --queue "$d/queue.txt" --out "$d" --slots 2 >> "$d/run.log" 2>&1 < /dev/null
  echo "$(date -Is) end $d rc=$?" >> $log
done
echo "$(date -Is) chain finished" >> $log
