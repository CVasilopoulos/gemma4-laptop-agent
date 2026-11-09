#!/bin/bash
ROOT="${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}"
SENSOR=$(grep -l amdgpu /sys/class/hwmon/hwmon*/name | head -1 | xargs dirname)/power1_average
GTT=$(ls /sys/class/drm/card*/device/mem_info_gtt_used | head -1)
OUT="$ROOT/logs/power/power_$(date -u +%Y%m%d).csv"
[ -s "$OUT" ] || echo "epoch_ms,package_uW,gtt_used_bytes" > "$OUT"
while true; do
  NOW_OUT="$ROOT/logs/power/power_$(date -u +%Y%m%d).csv"
  if [ "$NOW_OUT" != "$OUT" ]; then OUT=$NOW_OUT; echo "epoch_ms,package_uW,gtt_used_bytes" > "$OUT"; fi
  echo "$(date +%s%3N),$(cat $SENSOR),$(cat $GTT)" >> "$OUT"
  sleep 1
done
