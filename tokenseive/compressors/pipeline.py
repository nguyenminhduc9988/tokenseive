"""Multi-stage compression pipeline.

The pipeline cascades backends from cheapest/most-deterministic to most
expensive/most-aggressive, stopping as soon as the target keep-rate is met:

    rules  ->  selective_context  ->  llmlingua2

Each stage only runs if the previous one did not already reach the target
keep-rate. Backends that are unavailable are skipped silently (graceful
degradation), so a pipeline configured for ``"multi"`` still produces a result
— just the rule-based one — when the ML extras are not installed.

For single-backend convenience use ``backend`` in ``{"rules", "selective",
"llmlingua2"}``.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..utils import count_tokens, restore_sentinels
from .llmlingua2 import LLMLingua2Compressor
from .rule_based import CompressionResult, RuleBasedCompressor
from .selective import SelectiveContextCompressor

#: Supported pipeline backends.
VALID_BACKENDS = ("rules", "selective", "llmlingua2", "multi")


class CompressionPipeline:
    """Multi-stage compression pipeline with graceful degradation.

    Parameters
    ----------
    backend:
        One of ``"rules"``, ``"selective"``, ``"llmlingua2"``, or ``"multi"``
        (cascade). Defaults to ``"rules"`` (always available, zero deps).
    rate:
        Target keep-rate (fraction of tokens to *retain*) for ML backends,
        in ``[0.05, 0.95]``. Ignored by the ``"rules"`` backend.

    Raises
    ------
    ValueError
        If *backend* or *rate* is out of range.
    """

    def __init__(self, backend: str = "rules", rate: float = 0.5) -> None:
        if backend not in VALID_BACKENDS:
            raise ValueError(
                f"Unknown backend {backend!r}; valid options: {VALID_BACKENDS}"
            )
        if not (0.05 <= float(rate) <= 0.95):
            raise ValueError(
                f"rate must be in [0.05, 0.95], got {rate}"
            )
        self.backend = backend
        self.rate = float(rate)
        # Always-available rules engine (used by stage 1 of the cascade and as
        # the universal fallback).
        self._rules = RuleBasedCompressor()
        # ML backends load lazily on first use.
        self._sc: Optional[SelectiveContextCompressor] = None
        self._sc_tried = False
        self._llmlingua: Optional[LLMLingua2Compressor] = None
        self._llmlingua_tried = False

    # ------------------------------------------------------------------ #
    @staticmethod
    def available_backends() -> List[str]:
        """Return the list of currently usable backends in this environment."""
        out: List[str] = ["rules"]
        if SelectiveContextCompressor.available():
            out.append("selective")
        if LLMLingua2Compressor.available():
            out.append("llmlingua2")
        return out

    def count_tokens(self, text: str) -> int:
        """Count tokens using the pipeline's rules engine."""
        return self._rules.count_tokens(text)

    def _get_sc(self) -> Optional[SelectiveContextCompressor]:
        if not self._sc_tried:
            self._sc_tried = True
            try:
                self._sc = SelectiveContextCompressor()
            except ImportError:
                self._sc = None
        return self._sc

    def _get_llmlingua(self) -> Optional[LLMLingua2Compressor]:
        if not self._llmlingua_tried:
            self._llmlingua_tried = True
            try:
                self._llmlingua = LLMLingua2Compressor()
            except ImportError:
                self._llmlingua = None
        return self._llmlingua

    # ------------------------------------------------------------------ #
    def _run_multi(
        self, masked: str, store: dict, rate: float
    ) -> Tuple[str, List[str]]:
        """Cascade: rules -> selective_context -> llmlingua2."""
        techniques: List[str] = []
        orig_tokens = self.count_tokens(masked)

        # Stage 1: rules — always available, deterministic, free reduction.
        stage1, tech1 = self._rules.run_rule_pipeline(masked)
        techniques.extend(tech1)
        techniques.append("multi_stage:rules")
        current_rate = self.count_tokens(stage1) / max(orig_tokens, 1)
        if current_rate <= rate:
            return stage1, techniques

        # Stage 2: Selective Context (phrase filtering), if available.
        sc = self._get_sc()
        if sc is not None:
            stage2 = sc._apply_protected(stage1, store, rate)
            techniques.append("multi_stage:selective_context")
            current_rate = self.count_tokens(stage2) / max(orig_tokens, 1)
            if current_rate <= rate:
                return stage2, techniques
        else:
            stage2 = stage1

        # Stage 3: LLMLingua-2 (token-level pruning) — most aggressive.
        if self._get_llmlingua() is not None:
            stage3 = self._get_llmlingua()._apply_protected(stage2, store, rate)  # type: ignore[union-attr]
            techniques.append("multi_stage:llmlingua2")
            return stage3, techniques

        return stage2, techniques

    # ------------------------------------------------------------------ #
    def compress(self, text: str, **kwargs: Any) -> CompressionResult:
        """Run the configured pipeline over *text*.

        Keyword Args
        ------------
        backend:
            Override the configured backend for this call.
        rate:
            Override the configured keep-rate for this call.
        """
        backend = str(kwargs.get("backend", self.backend))
        rate = float(kwargs.get("rate", self.rate))
        if backend not in VALID_BACKENDS:
            backend = "rules"

        original_text = text
        original_tokens = self.count_tokens(original_text)
        if not text or not text.strip():
            return CompressionResult(
                original_text=original_text,
                compressed_text=original_text or "",
                original_tokens=0,
                compressed_tokens=0,
                tokens_saved=0,
                compression_ratio=0.0,
                techniques_applied=[],
            )

        masked, store = self._rules._protect(text)
        techniques: List[str] = []

        if backend == "rules":
            current, techniques = self._rules.run_rule_pipeline(masked)
        elif backend == "selective":
            sc = self._get_sc()
            if sc is not None:
                current = sc._apply_protected(masked, store, rate)
                techniques.append(f"selective_context(rate={rate})")
            else:
                current, techniques = self._rules.run_rule_pipeline(masked)
                techniques.append("fallback:rules(selective_unavailable)")
        elif backend == "llmlingua2":
            ll = self._get_llmlingua()
            if ll is not None:
                current = ll._apply_protected(masked, store, rate)
                techniques.append(f"llmlingua2(rate={rate})")
            else:
                current, techniques = self._rules.run_rule_pipeline(masked)
                techniques.append("fallback:rules(llmlingua2_unavailable)")
        else:  # multi
            current, techniques = self._run_multi(masked, store, rate)

        # Safety net: ensure no protected sentinel was dropped by an ML stage.
        if backend != "rules" and store:
            surviving = sum(1 for s in store if s in current)
            if surviving < len(store):
                current, _ = self._rules.run_rule_pipeline(masked)
                techniques.append("fallback:rules(sentinel_loss)")

        compressed_text = restore_sentinels(current, store)
        compressed_tokens = self.count_tokens(compressed_text)
        tokens_saved = original_tokens - compressed_tokens
        return CompressionResult(
            original_text=original_text,
            compressed_text=compressed_text,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=(tokens_saved / original_tokens) if original_tokens else 0.0,
            techniques_applied=techniques,
        )
