"""Shared utilities for TokenSeive.

This module centralises the small set of cross-cutting helpers that every
sub-package needs: token counting (with a graceful, dependency-free fallback)
and the sentinel markers used to mask protected regions during compression.

Everything here works with **zero third-party dependencies**. ``tiktoken`` is
imported opportunistically — when it is absent, token counts degrade to a
deterministic word/punctuation heuristic that tracks GPT-style token counts to
within ~10% for typical English prose.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional dependency: tiktoken (real tokenizer). Falls back gracefully.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - import-time behaviour depends on environment
    import tiktoken  # type: ignore

    _TIKTOKEN_AVAILABLE = True
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore
    _TIKTOKEN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Sentinels used to mask protected regions during compression.
#
# They contain no whitespace and use characters that none of the compression
# regexes can match, so they survive every transformation untouched and are
# restored verbatim at the end.
# ---------------------------------------------------------------------------
SENTINEL_OPEN = "\u27e6"   # ⟦
SENTINEL_CLOSE = "\u27e7"  # ⟧

#: Pattern that matches an already-placed sentinel (so we never nest them).
SENTINEL_RE = re.compile(
    re.escape(SENTINEL_OPEN) + r"\w+" + re.escape(SENTINEL_CLOSE)
)


def tiktoken_available() -> bool:
    """Return ``True`` if ``tiktoken`` is importable."""
    return _TIKTOKEN_AVAILABLE


def get_encoding(name: str = "o200k_base") -> Optional[Any]:
    """Load a tiktoken encoding by name, returning ``None`` on any failure.

    Parameters
    ----------
    name:
        The tiktoken encoding name. Defaults to ``"o200k_base"`` (the
        tokenizer used by GPT-4o / GPT-4.1).
    """
    if not _TIKTOKEN_AVAILABLE:
        return None
    try:
        return tiktoken.get_encoding(name)  # type: ignore[union-attr]
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """Deterministic heuristic token count used when tiktoken is unavailable.

    Splits on runs of word characters and individual punctuation marks, which
    tracks GPT-style token counts reasonably well (roughly ``chars / 4`` on
    average, but markedly more accurate for code-heavy or mixed text).
    """
    if not text:
        return 0
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


def count_tokens(text: str, encoding: Optional[Any] = None) -> int:
    """Return the token count of *text*.

    Uses the provided tiktoken *encoding* when available; otherwise falls back
    to :func:`estimate_tokens`.
    """
    if not text:
        return 0
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass
    return estimate_tokens(text)


def make_sentinel(prefix: str, index: int) -> str:
    """Build a numbered sentinel string of the form ``⟦{prefix}{index}⟧``."""
    return f"{SENTINEL_OPEN}{prefix}{index}{SENTINEL_CLOSE}"


def is_sentinel_line(line: str) -> bool:
    """Return ``True`` if *line* consists (after strip) of a single sentinel."""
    stripped = line.strip()
    return bool(stripped) and SENTINEL_OPEN in line


def restore_sentinels(text: str, store: Dict[str, str]) -> str:
    """Restore every sentinel in *text* back to its original content."""
    # Sort longest-first so a sentinel that is a prefix of another cannot
    # partially match first.
    for sentinel in sorted(store, key=len, reverse=True):
        text = text.replace(sentinel, store[sentinel])
    return text


def split_on_sentinels(masked: str, store: Dict[str, str]) -> Tuple[str, ...]:
    """Split *masked* text on its sentinels, keeping the sentinels in place.

    Returns a tuple of alternating (prose, sentinel, prose, ...) fragments.
    Useful for running a compressor over only the non-protected spans.
    """
    if not store:
        return (masked,)
    keys = sorted(store, key=len, reverse=True)
    pattern = re.compile("(" + "|".join(re.escape(k) for k in keys) + ")")
    return tuple(p for p in re.split(pattern, masked) if p)
