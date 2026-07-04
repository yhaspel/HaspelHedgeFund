"""Shared agent-layer exceptions."""
from __future__ import annotations


class OfflineLLMViolation(RuntimeError):
    """Raised by ``registry.get_llm`` when ``settings.OFFLINE_MODE`` is on and a
    non-Ollama adapter was requested (P4-OFF R3).

    This is the hard guarantee that offline runs use the local model *only* — it
    fires no matter how the cloud slug arrived (per-agent override, Settings
    default model, a stored strategy preset, a rerun of a pre-offline run). The
    execution-seam preset forcing means it should never fire on a healthy L1 run;
    it exists to make a leak fail loudly rather than reach the network.
    """
