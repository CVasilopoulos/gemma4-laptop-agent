#!/bin/bash
set -euo pipefail
ROOT="${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}"
SRC=swebench-sandbox-local:latest
for pair in fastapi:fastapi/fastapi rich:Textualize/rich requests:psf/requests httpx:encode/httpx; do
  own=${pair%%:*}; repo=${pair#*:}
  name=swe_base_$own
  docker rm -f $name >/dev/null 2>&1 || true
  docker run -d --name $name --network none --memory 2g --cpus 2 $SRC sleep infinity >/dev/null
  docker exec $name mkdir -p /sandbox /wheels_overlay
  docker cp "$ROOT/data/overlay_wheels/." $name:/wheels_overlay/
  docker cp "$ROOT/code/harness/install_deps_v3.py" $name:/sandbox/install_deps_v3.py
  docker exec $name python3 /sandbox/install_deps_v3.py --base --repo $repo
  docker exec $name rm -f /sandbox/install_deps_v3.py
  docker commit -c "WORKDIR /workspace" -c "ENV TEST_TMPDIR=/tmp" $name swebench-sandbox-v3-$own:latest >/dev/null
  docker rm -f $name >/dev/null
  echo "built swebench-sandbox-v3-$own"
done
