"""Tool-output compression using Headroom (optional dependency).

Headroom compresses structured tool outputs (JSON, search results, command
stdout, file listings) by 85-93% using its *SmartCrusher* engine — array
truncation, structure collapsing and result aggregation.

This module is the bridge between TokenSeive and the ``headroom-ai`` library.
``headroom-ai`` is **not** a required dependency:

* When it *is* installed (``pip install tokenseive[headroom]``) the real
  SmartCrusher engine is used.
* When it is *absent*, a deterministic built-in fallback (array truncation +
  structure collapsing) keeps the public API fully functional with no hard
  dependency, so :class:`HeadroomCompressor` always returns a usable result.

Public API
----------
* :class:`ToolCompressionResult` — before/after report (mirrors
  :class:`tokenseive.compressors.CompressionResult`).
* :class:`HeadroomCompressor` — compress a single tool result, or
  batch-compress a whole conversation history.
* :data:`DEFAULT_TOOL_POLICY` — which tool outputs are worth compressing.
* :func:`headroom_available` — is the real engine importable?

Install
-------
::

    pip install tokenseive[headroom]     # pulls in headroom-ai
    # or, equivalently:
    pip install headroom-ai
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict as _asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..utils import count_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency: headroom-ai (real SmartCrusher engine)
# ---------------------------------------------------------------------------
_headroom = None  # type: ignore[assignment]
_headroom_available = False

try:  # pragma: no cover - import-time behaviour depends on environment
    import headroom as _headroom  # type: ignore

    _headroom_available = True
except Exception:  # pragma: no cover
    _headroom = None  # type: ignore[assignment]
    _headroom_available = False


# ---------------------------------------------------------------------------
# Default tool-compression policy
# ---------------------------------------------------------------------------
#: Policy describing which tool outputs are worth compressing. Callers may
#: pass their own ``policy=`` to :class:`HeadroomCompressor` to override.
DEFAULT_TOOL_POLICY: Dict[str, Any] = {
    # Tools whose output is large structured data — always a good candidate
    # *once it clears the size threshold*.
    "always_compress": [
        "search_files",
        "grep",
        "list_files",
        "read_file",
        "web_search",
        "execute_command",
        "code_search",
    ],
    # Tools whose output is small, mutating, or security-critical — never
    # touch them (a truncated write/edit result would mislead the model).
    "never_compress": [
        "bash",
        "auth_status",
        "write_file",
        "edit_file",
    ],
    # Minimum payload size before compression is worthwhile.
    "min_chars": 200,
    "min_tokens": 50,
}

# Field names that typically carry large blobs worth collapsing.
_LARGE_TEXT_FIELDS = (
    "stdout",
    "stderr",
    "output",
    "content",
    "text",
    "body",
    "data",
)

# Number of items kept at each end when truncating a large array.
_ARRAY_HEAD = 5
_ARRAY_TAIL = 5
# Threshold above which an array/list is considered "large" and truncated.
_LARGE_ARRAY_LEN = 20


def headroom_available() -> bool:
    """Return ``True`` if the real ``headroom-ai`` engine is importable."""
    return _headroom_available


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class ToolCompressionResult:
    """Before/after report returned by :class:`HeadroomCompressor`.

    Mirrors :class:`tokenseive.compressors.CompressionResult` so callers can
    treat both interchangeably.
    """

    original_text: str
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_ratio: float
    transforms_applied: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Return the report as a plain ``dict`` (handy for JSON / logging)."""
        return _asdict(self)

    # Friendly dict-style access for callers migrating from dict results.
    def __getitem__(self, key: str) -> Any:
        return _asdict(self)[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style ``.get()`` accessor."""
        return _asdict(self).get(key, default)


# ---------------------------------------------------------------------------
# Compressor
# ---------------------------------------------------------------------------
class HeadroomCompressor:
    """Compress tool outputs using the ``headroom-ai`` library.

    Parameters
    ----------
    model, model_limit, protect_recent, min_tokens, optimize:
        Forwarded to Headroom's SmartCrusher when the real engine is used.
        They have no effect on the built-in fallback.
    policy:
        Tool-selection policy (defaults to :data:`DEFAULT_TOOL_POLICY`).
    strict:
        If ``True``, raise :class:`ImportError` immediately when
        ``headroom-ai`` is not installed. Defaults to ``False`` so the
        compressor always works via its built-in fallback.

    Raises
    ------
    ImportError
        Only when ``strict=True`` and ``headroom-ai`` is not installed.

    Examples
    --------
    >>> from tokenseive.tool_compression import HeadroomCompressor
    >>> c = HeadroomCompressor()
    >>> r = c.compress_tool_output("search_files", '{"files": [1,2,3]}')
    >>> r.tokens_saved >= 0
    True
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        model_limit: int = 200000,
        protect_recent: int = 4,
        min_tokens: int = 250,
        optimize: bool = False,
        policy: Optional[Dict[str, Any]] = None,
        strict: bool = False,
    ) -> None:
        self.model = model
        self.model_limit = model_limit
        self.protect_recent = protect_recent
        self.min_tokens = min_tokens
        self.optimize = optimize
        self.policy = dict(DEFAULT_TOOL_POLICY)
        if policy:
            self.policy.update(policy)

        # Lazy-loaded engine — never touched until compression is requested.
        self._crusher: Any = None
        self._crusher_loaded = False

        if strict and not _headroom_available:
            raise ImportError(
                "HeadroomCompressor(strict=True) requires the 'headroom-ai' "
                "package. Install it with: pip install tokenseive[headroom] "
                "(or: pip install headroom-ai)"
            )

    # ------------------------------------------------------------------ #
    # Availability
    # ------------------------------------------------------------------ #
    @staticmethod
    def available() -> bool:
        """Return ``True`` if ``headroom-ai`` is importable (no raise)."""
        return _headroom_available

    def require_headroom(self) -> Any:
        """Explicitly demand the real engine.

        Lazily imports ``headroom-ai`` and returns the SmartCrusher instance.
        Raises :class:`ImportError` with a helpful message if it is not
        installed.
        """
        crusher = self._ensure_crusher()
        if crusher is None:
            raise ImportError(
                "headroom-ai is not installed. Install it with: "
                "pip install tokenseive[headroom]  (or: pip install headroom-ai)"
            )
        return crusher

    def _ensure_crusher(self) -> Any:
        """Lazily build the Headroom SmartCrusher. Returns it or ``None``.

        Memoised: the import is attempted exactly once per instance. Any
        failure degrades silently to the built-in fallback.
        """
        if self._crusher_loaded:
            return self._crusher
        self._crusher_loaded = True
        if not _headroom_available:
            return None
        try:  # pragma: no cover - headroom-ai not installed in CI
            crusher: Any = None
            kwargs = dict(
                model=self.model,
                model_limit=self.model_limit,
                protect_recent=self.protect_recent,
                min_tokens=self.min_tokens,
                optimize=self.optimize,
            )
            if hasattr(_headroom, "SmartCrusher"):
                crusher = _headroom.SmartCrusher(**_filter_kwargs(_headroom.SmartCrusher, kwargs))
            elif hasattr(_headroom, "Headroom"):
                crusher = _headroom.Headroom(**_filter_kwargs(_headroom.Headroom, kwargs))
            self._crusher = crusher
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("headroom-ai engine init failed, using fallback: %s", exc)
            self._crusher = None
        return self._crusher

    # ------------------------------------------------------------------ #
    # Policy: should a given result be compressed?
    # ------------------------------------------------------------------ #
    def should_compress(self, tool_name: str, content: Any) -> bool:
        """Return ``True`` if *content* from *tool_name* is worth compressing.

        Decision rules (in order):

        1. ``never_compress`` tools → ``False``.
        2. Empty / tiny payloads (below ``min_chars`` / ``min_tokens``) →
           ``False``.
        3. Everything else → ``True``.
        """
        if not content:
            return False

        name = (tool_name or "").strip()
        if name in self.policy.get("never_compress", []):
            return False

        text = _as_text(content)

        if len(text) < int(self.policy.get("min_chars", 200)):
            return False
        if count_tokens(text) < int(self.policy.get("min_tokens", 50)):
            return False

        # ``always_compress`` tools that already cleared the size gate are
        # definite yeses; other tools are still compressed if they cleared
        # the gate and aren't blacklisted.
        return True

    # ------------------------------------------------------------------ #
    # Single-result compression
    # ------------------------------------------------------------------ #
    def compress_tool_output(
        self, tool_name: str, content: Any
    ) -> ToolCompressionResult:
        """Compress a single tool result.

        Returns a :class:`ToolCompressionResult` even when nothing was
        compressed (``tokens_saved == 0``) — callers never need to handle a
        missing engine.
        """
        text = _as_text(content)
        original_tokens = count_tokens(text)

        if not self.should_compress(tool_name, text):
            return ToolCompressionResult(
                original_text=text,
                compressed_text=text,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                tokens_saved=0,
                compression_ratio=0.0,
                transforms_applied=[],
            )

        crusher = self._ensure_crusher()
        compressed: Optional[str] = None
        transforms: List[str] = []

        if crusher is not None:
            try:  # pragma: no cover - headroom-ai not installed in CI
                compressed, transforms = self._compress_with_headroom(crusher, text)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("headroom-ai compress failed, using fallback: %s", exc)
                compressed = None

        if compressed is None:
            compressed, transforms = self._fallback_compress(text)

        # Never let "compression" expand the payload.
        if len(compressed) >= len(text):
            compressed = text
            transforms = []

        compressed_tokens = count_tokens(compressed)
        tokens_saved = max(0, original_tokens - compressed_tokens)
        ratio = (tokens_saved / original_tokens) if original_tokens > 0 else 0.0

        return ToolCompressionResult(
            original_text=text,
            compressed_text=compressed,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=ratio,
            transforms_applied=transforms,
        )

    def _compress_with_headroom(
        self, crusher: Any, text: str
    ) -> Tuple[str, List[str]]:  # pragma: no cover - headroom-ai not in CI
        """Run the real SmartCrusher engine over *text*.

        Defensively probes the known ``headroom-ai`` call shapes so a minor
        API drift degrades to the fallback instead of crashing.
        """
        for method_name in ("compress", "crush", "smart_crush"):
            method = getattr(crusher, method_name, None)
            if callable(method):
                try:
                    out = method(text)
                except TypeError:
                    out = method(text, min_tokens=self.min_tokens)
                candidate = _extract_compressed(out)
                if candidate:
                    return candidate, ["headroom:smart_crusher"]
        # Top-level functional form: ``headroom.compress(text)``.
        compress_fn = getattr(_headroom, "compress", None)
        if callable(compress_fn):
            candidate = _extract_compressed(compress_fn(text))
            if candidate:
                return candidate, ["headroom:compress"]
        raise RuntimeError("no usable headroom compression entry point")

    # ------------------------------------------------------------------ #
    # Built-in fallback (no headroom-ai required)
    # ------------------------------------------------------------------ #
    def _fallback_compress(self, text: str) -> Tuple[str, List[str]]:
        """Deterministic SmartCrusher-style fallback.

        Strategies, applied in order:

        * If the text is JSON (object/array), truncate large nested arrays
          and collapse large string fields, preserving structure.
        * Otherwise, if it is very long plain text, keep head + tail with an
          omission marker.
        * Small text is returned unchanged.
        """
        transforms: List[str] = []

        # Try structured (JSON) compression first.
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = _UNSET

        if parsed is not _UNSET:
            crushed, t = _crush_structure(parsed, set())
            compressed = json.dumps(crushed, ensure_ascii=False)
            if t:
                transforms.extend(t)
            if len(compressed) < len(text):
                transforms.append("fallback:json_collapse")
                return compressed, transforms

        # Plain-text path.
        if len(text) > 2000 or text.count("\n") > 100:
            compressed = _collapse_text(text)
            if len(compressed) < len(text):
                transforms.append("fallback:text_headtail")
                return compressed, transforms

        return text, transforms

    # ------------------------------------------------------------------ #
    # Batch: compress a whole conversation
    # ------------------------------------------------------------------ #
    def compress_messages(
        self,
        messages: List[Dict[str, Any]],
        protect_recent: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Batch-compress conversation history (older tool messages).

        Only ``tool`` role messages are touched, and never the most recent
        ``protect_recent`` messages (defaults to the instance value) so the
        model keeps full-fidelity context for the active turn. Returns a new
        list; the input is not mutated.
        """
        if not messages:
            return []

        protect = self.protect_recent if protect_recent is None else protect_recent
        cutoff = max(0, len(messages) - protect)
        out: List[Dict[str, Any]] = []

        for idx, msg in enumerate(messages):
            if idx >= cutoff:
                out.append(msg)
                continue

            role = msg.get("role", "")
            content = msg.get("content", "")
            if role != "tool":
                out.append(msg)
                continue

            tool_name = msg.get("name") or msg.get("tool_name") or "tool"
            result = self.compress_tool_output(tool_name, content)
            if result.tokens_saved > 0:
                new_msg = dict(msg)
                new_msg["content"] = result.compressed_text
                new_msg.setdefault("metadata", {})
                new_msg["metadata"]["_headroom_compressed"] = True
                new_msg["metadata"]["_headroom_tokens_saved"] = result.tokens_saved
                out.append(new_msg)
            else:
                out.append(msg)

        return out


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------
_UNSET = object()


def _as_text(content: Any) -> str:
    """Coerce a tool result (str / dict / list / other) to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)
    return str(content)


def _filter_kwargs(callable_obj: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only kwargs the target constructor accepts (defensive)."""
    try:
        import inspect

        sig = inspect.signature(callable_obj)
        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return dict(kwargs)
        return {k: v for k, v in kwargs.items() if k in params}
    except Exception:  # pragma: no cover - defensive
        return dict(kwargs)


def _extract_compressed(out: Any) -> Optional[str]:
    """Normalise Headroom's varied return shapes to a string."""
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        for key in ("compressed", "compressed_text", "result", "output", "text"):
            val = out.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _crush_structure(node: Any, _seen: set) -> Tuple[Any, List[str]]:
    """Recursively truncate arrays and collapse large string fields.

    Returns ``(crushed_node, transforms)``.
    """
    transforms: List[str] = []
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if isinstance(value, str) and key.lower() in _LARGE_TEXT_FIELDS:
                collapsed = _collapse_text(value) if len(value) > 200 else value
                if collapsed != value:
                    transforms.append(f"collapse:{key}")
                out[key] = collapsed
            elif isinstance(value, (dict, list)):
                sub, t = _crush_structure(value, _seen)
                out[key] = sub
                transforms.extend(t)
            else:
                out[key] = value
        return out, transforms
    if isinstance(node, list):
        if len(node) > _LARGE_ARRAY_LEN:
            head = node[:_ARRAY_HEAD]
            tail = node[-_ARRAY_TAIL:]
            omitted = len(node) - _ARRAY_HEAD - _ARRAY_TAIL
            crushed_head = [_crush_structure(item, _seen)[0] for item in head]
            crushed_tail = [_crush_structure(item, _seen)[0] for item in tail]
            marker = {"_omitted": omitted, "truncated": True}
            transforms.append(f"truncate_array:{len(node)}")
            return crushed_head + [marker] + crushed_tail, transforms
        return [_crush_structure(item, _seen)[0] for item in node], transforms
    return node, transforms


def _collapse_text(text: str) -> str:
    """Keep head + tail of a long string with an omission marker."""
    if len(text) <= 2000 and text.count("\n") <= 100:
        return text
    lines = text.split("\n")
    if len(lines) > 100:
        omitted_lines = len(lines) - 40
        return "\n".join(
            [
                *lines[:20],
                f"... [{omitted_lines} lines omitted] ...",
                *lines[-20:],
            ]
        )
    half = 800
    omitted = len(text) - 2 * half
    return f"{text[:half]}... [{omitted} chars omitted] ...{text[-half:]}"
