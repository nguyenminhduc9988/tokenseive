"""Prompt compression backends for TokenSeive.

Public API
----------
* :class:`RuleBasedCompressor` — zero-dependency deterministic compression.
* :class:`CompressionPipeline` — multi-backend cascade (rules / selective /
  llmlingua2 / multi).
* :class:`CompressionResult` — before/after report returned by every backend.
* :class:`LLMLingua2Compressor` / :class:`SelectiveContextCompressor` —
  optional ML backends (require ``tokenseive[ml]``).
"""

from __future__ import annotations

from .llmlingua2 import LLMLingua2Compressor
from .pipeline import CompressionPipeline
from .rule_based import CompressionResult, RuleBasedCompressor
from .selective import SelectiveContextCompressor

__all__ = [
    "CompressionResult",
    "RuleBasedCompressor",
    "CompressionPipeline",
    "LLMLingua2Compressor",
    "SelectiveContextCompressor",
]
