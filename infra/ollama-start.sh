#!/usr/bin/env bash
set -euo pipefail

MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
HOST="http://localhost:11434"

brew services start ollama

for _ in {1..30}; do
  if curl -sf "$HOST/api/tags" > /dev/null; then
    break
  fi
  sleep 1
done

if ! curl -sf "$HOST/api/tags" > /dev/null; then
  echo "Ollama daemon did not become ready at $HOST within 30s" >&2
  exit 1
fi

ollama pull "$MODEL"
echo "Ollama running on $HOST with model $MODEL"
