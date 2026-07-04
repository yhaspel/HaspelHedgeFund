#!/usr/bin/env bash
# Start the local Ollama daemon for offline mode (P4-OFF).
#
# Reworked from the original unconditional `ollama pull` (which hard-failed
# under `set -e` precisely when you were already offline). Now:
#   * OLLAMA_NUM_PARALLEL=1 serializes generations — a council run fans out 13+
#     agent calls and Celery --concurrency=8 can issue several at once; since
#     Ollama 0.2 the default is auto (4 on a 24 GB M4 w/ a ~5 GB model), which
#     would run four generations concurrently and thrash memory. Serialize so
#     calls queue instead (the adapter's 900 s timeout absorbs the queueing).
#   * `--ensure-model` pulls the model only when it's missing AND a pull can
#     proceed, so "enter offline mode" stays one script even when the WAN is up
#     but you just want to start the daemon.
#   * OFFLINE_LLM_MODEL is the canonical model var; OLLAMA_MODEL kept as a
#     fallback for compat.
set -euo pipefail

MODEL="${OFFLINE_LLM_MODEL:-${OLLAMA_MODEL:-qwen2.5:7b}}"
HOST="http://localhost:11434"
ENSURE_MODEL=0

for arg in "$@"; do
  case "$arg" in
    --ensure-model) ENSURE_MODEL=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# Serialize generations regardless of free memory (see header). Exported so the
# daemon launched by `brew services` inherits it.
export OLLAMA_NUM_PARALLEL=1

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

if [ "$ENSURE_MODEL" = "1" ]; then
  if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "Model $MODEL already present — no pull needed."
  else
    echo "Model $MODEL missing — pulling (needs connectivity)…"
    # Don't let a failed pull (already offline) abort the script: the daemon is
    # up and other models may already be present.
    ollama pull "$MODEL" || echo "Pull failed — start offline mode only after '$MODEL' (or another model) is pulled." >&2
  fi
fi

echo "Ollama running on $HOST (OLLAMA_NUM_PARALLEL=1). Default offline model: $MODEL"
