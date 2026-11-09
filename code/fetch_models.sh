#!/bin/bash
cd "${GEMMA_ROOT:-$HOME/competitions/gemma-4-agent}/models" || exit 1
for spec in "gemma-4-26B-A4B-it-qat-q4_0-gguf/gemma-4-26B_q4_0-it.gguf" "gemma-4-E4B-it-qat-q4_0-gguf/gemma-4-E4B_q4_0-it.gguf" "gemma-4-E2B-it-qat-q4_0-gguf/gemma-4-E2B_q4_0-it.gguf"; do
  repo=${spec%%/*}; file=${spec#*/}
  echo "$(date -Is) start $file"
  curl -fL --retry 5 --retry-delay 10 -C - -o "$file" "https://huggingface.co/google/$repo/resolve/main/$file" || { echo "$(date -Is) FAILED $file"; continue; }
  echo "$(date -Is) done $file $(stat -c %s "$file")"
done
sha256sum *.gguf > SHA256SUMS
echo "$(date -Is) all done"
