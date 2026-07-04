#!/usr/bin/env bash
set -euo pipefail

MODEL="${OFFLINE_LLM_MODEL:-${OLLAMA_MODEL:-qwen2.5:7b}}"

ollama stop "$MODEL" 2>/dev/null || true
brew services stop ollama
echo "Ollama stopped"
