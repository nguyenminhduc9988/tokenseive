"""Selective Context backend (optional dependency).

Wraps `Selective Context <https://github.com/liyucheng09/Selective_Context>`_
(GPT-2 self-information phrase filtering) behind the standard
:class:`CompressionResult` interface. Selective Context is **not** a required
dependency: it is imported lazily.

Requires::

    pip install tokenseive[ml]
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..utils import count_tokens, restore_sentinels, split_on_sentinels
from .rule_based import CompressionResult, RuleBasedCompressor


class SelectiveContextCompressor:
    """Selective Context phrase-filtering compressor.

    Parameters
    ----------
    model_type:
        Underlying language model used for self-information scoring. Defaults
        to ``"gpt2"`` (the only fully-local option).
    lang:
        Language code passed to Selective Context (default ``"en"``).

    Raises
    ------
    ImportError
        If the ``selective_context`` package is not installed. Install with
        ``pip install tokenseive[ml]``.
    """

    def __init__(self, model_type: str = "gpt2", lang: str = "en") -> None:
        try:
            from selective_context import SelectiveContext  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "Selective Context requires the 'selective-context' package. "
                "Install it with: pip install tokenseive[ml]"
            ) from exc

        self.model_type = model_type
        self.lang = lang
        self._sc = SelectiveContext(model_type=model_type, lang=lang)
        self._protector = RuleBasedCompressor()

    @staticmethod
    def available() -> bool:
        """Return ``True`` if ``selective_context`` is importable."""
        try:
            import selective_context  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    def count_tokens(self, text: str) -> int:
        """Count tokens (tiktoken if available, else heuristic)."""
        return self._protector.count_tokens(text)

    def _compress_raw(self, text: str, rate: float) -> str:
        """Run Selective Context over a single prose span.

        *rate* is a keep-rate; Selective Context's ``reduce_ratio`` is the
        fraction *removed*, so we invert it (``reduce_ratio = 1 - rate``).
        Phrase level preserves more prompt structure than sentence level.
        """
        reduce_ratio = max(0.05, min(0.95, 1.0 - float(rate)))
        compressed, _removed = self._sc(
            text, reduce_ratio=reduce_ratio, reduce_level="phrase"
        )
        if isinstance(compressed, str) and compressed.strip():
            return compressed
        return text

    def _apply_protected(
        self, masked: str, store: Dict[str, str], rate: float
    ) -> str:
        """Compress only the non-protected spans, leaving sentinels intact."""
        if not store:
            return self._compress_raw(masked, rate)
        out_parts: List[str] = []
        for part in split_on_sentinels(masked, store):
            if not part:
                continue
            if part in store:
                out_parts.append(part)
                continue
            if len(part.strip()) < 24:
                out_parts.append(part)
                continue
            try:
                out_parts.append(self._compress_raw(part, rate))
            except Exception:
                out_parts.append(part)
        return "".join(out_parts)

    def compress(self, text: str, rate: float = 0.5, **kwargs: Any) -> CompressionResult:
        """Compress *text* with Selective Context at *rate* keep-rate.

        Protected regions are masked beforehand and restored verbatim.
        """
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

        masked, store = self._protector._protect(text)
        try:
            current = self._apply_protected(masked, store, rate)
        except Exception:
            current = masked

        if store:
            surviving = sum(1 for s in store if s in current)
            if surviving < len(store):
                current, _ = self._protector.run_rule_pipeline(masked)

        compressed_text = restore_sentinels(current, store)
        compressed_tokens = self.count_tokens(compressed_text)
        techniques = [f"selective_context(rate={rate})"]
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
