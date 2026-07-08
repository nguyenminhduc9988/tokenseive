"""LLMLingua-2 backend (optional dependency).

Wraps Microsoft's LLMLingua-2 (XLM-RoBERTa token-classification compressor) so
it can be used through the same :class:`CompressionResult` interface as the
rule-based compressor. LLMLingua-2 is **not** a required dependency: it is
imported lazily and :meth:`available` reports whether it can be used.

Requires::

    pip install tokenseive[ml]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..utils import count_tokens, restore_sentinels, split_on_sentinels
from .rule_based import CompressionResult, RuleBasedCompressor

DEFAULT_MODEL = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"

#: Structural punctuation LLMLingua-2 is asked to keep so the result stays
#: parseable even at aggressive keep-rates.
_DEFAULT_FORCE_TOKENS = ["\n", "?", ".", ",", ":", ";", "!", "-", "*"]


class LLMLingua2Compressor:
    """LLMLingua-2 token-level prompt compressor.

    Parameters
    ----------
    model_name:
        HuggingFace model id. Defaults to the MeetingBank-fine-tuned
        XLM-RoBERTa-large classifier.
    device:
        Torch device map, e.g. ``"cpu"`` or ``"cuda"``.

    Raises
    ------
    ImportError
        If the ``llmlingua`` package is not installed. Install with
        ``pip install tokenseive[ml]``.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
    ) -> None:
        try:
            from llmlingua import PromptCompressor as _LLMLinguaCompressor  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "LLMLingua-2 requires the 'llmlingua' package. "
                "Install it with: pip install tokenseive[ml]"
            ) from exc

        self.model_name = model_name
        self.device = device
        self._compressor = _LLMLinguaCompressor(
            model_name=model_name,
            use_llmlingua2=True,
            device_map=device,
        )
        # Reuse the rule-based compressor for protected-region handling and
        # token counting (zero extra dependencies beyond llmlingua itself).
        self._protector = RuleBasedCompressor()

    @staticmethod
    def available() -> bool:
        """Return ``True`` if ``llmlingua`` is importable."""
        try:
            import llmlingua  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    def count_tokens(self, text: str) -> int:
        """Count tokens (tiktoken if available, else heuristic)."""
        return self._protector.count_tokens(text)

    def _compress_raw(self, text: str, rate: float) -> str:
        """Run LLMLingua-2 over a single prose span at *rate* keep-rate."""
        keep_rate = max(0.1, min(0.9, float(rate)))
        result = self._compressor.compress_prompt(
            [text],
            rate=keep_rate,
            force_tokens=_DEFAULT_FORCE_TOKENS,
        )
        compressed = (
            result.get("compressed_prompt") if isinstance(result, dict) else None
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
                out_parts.append(part)  # protected region -> verbatim
                continue
            if len(part.strip()) < 24:
                out_parts.append(part)  # too small to compress usefully
                continue
            try:
                out_parts.append(self._compress_raw(part, rate))
            except Exception:
                out_parts.append(part)
        return "".join(out_parts)

    def compress(self, text: str, rate: float = 0.5, **kwargs: Any) -> CompressionResult:
        """Compress *text* with LLMLingua-2 at *rate* keep-rate.

        Protected regions (code blocks, XML/HTML, identity lines) are masked
        beforehand and restored verbatim, so they are never dropped by the
        model. Falls back to the input unchanged if compression produces no
        usable output.
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

        # Safety net: if the model dropped a protected sentinel, fall back to
        # the rule pipeline so nothing protected is ever lost.
        if store:
            surviving = sum(1 for s in store if s in current)
            if surviving < len(store):
                current, _ = self._protector.run_rule_pipeline(masked)

        compressed_text = restore_sentinels(current, store)
        compressed_tokens = self.count_tokens(compressed_text)
        techniques = [f"llmlingua2(rate={rate})"]
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
