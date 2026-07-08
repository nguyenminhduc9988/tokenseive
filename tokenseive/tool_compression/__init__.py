"""Tool-output compression backends for TokenSeive.

Tool outputs (search results, command stdout, file listings, JSON payloads)
are the single biggest consumer of context-window tokens in an agent loop.
This sub-package provides compressors specialised for that structured
content.

Public API
----------
* :class:`HeadroomCompressor` — wraps the optional ``headroom-ai`` SmartCrusher
  engine with a deterministic built-in fallback (works with zero dependencies).
* :class:`ToolCompressionResult` — before/after report.
* :data:`DEFAULT_TOOL_POLICY` — which tools are worth compressing.
"""

from __future__ import annotations

from .headroom import (
    DEFAULT_TOOL_POLICY,
    HeadroomCompressor,
    ToolCompressionResult,
    headroom_available,
)

__all__ = [
    "HeadroomCompressor",
    "ToolCompressionResult",
    "DEFAULT_TOOL_POLICY",
    "headroom_available",
]
