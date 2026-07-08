"""TokenSeive — Multi-layer token optimization for LLM applications.

Three independent layers, each usable in isolation:

* **Compressors** (:mod:`tokenseive.compressors`) — shrink prompts without
  calling a model. Rule-based (zero-dep), LLMLingua-2, Selective Context, or a
  multi-stage cascade.
* **Mapper** (:mod:`tokenseive.mapper`) — turn a codebase into a token-budgeted
  ranked repo map / code graph so you can feed the model *just* the symbols it
  needs instead of whole files.
* **Behavioral** (:mod:`tokenseive.behavioral`) — an injectable ruleset that
  cuts *output* tokens by steering the model toward the shortest working diff.

Quick start
-----------
>>> from tokenseive import RuleBasedCompressor
>>> rc = RuleBasedCompressor()
>>> result = rc.compress("It is important to note that you must be careful.")
>>> round(result.compression_ratio, 1) >= 0.0
True
"""

from __future__ import annotations

from .behavioral import BehavioralRuleset
from .compressors import (
    CompressionPipeline,
    CompressionResult,
    LLMLingua2Compressor,
    RuleBasedCompressor,
    SelectiveContextCompressor,
)
from .mapper import CodebaseMapper

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "RuleBasedCompressor",
    "CompressionPipeline",
    "CompressionResult",
    "LLMLingua2Compressor",
    "SelectiveContextCompressor",
    "BehavioralRuleset",
    "CodebaseMapper",
]
