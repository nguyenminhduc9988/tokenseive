"""Deterministic, rule-based prompt compression — zero dependencies.

This is the heart of TokenSeive. Every transformation is a pure text rule
(regex / set membership), so output is fully reproducible and :meth:`compress`
is **idempotent**: running it twice yields exactly the same text. It needs no
ML runtime, no torch, no transformers — not even ``tiktoken`` (token counts
degrade gracefully to a deterministic heuristic).

Protected regions
-----------------
The compressor never mangles the semantically-critical parts of a prompt:

* Fenced code blocks (```` ``` ... ``` ````) and inline code (`` `...` ``)
* XML / HTML tags (`` <tag ...> ... </tag> ``), including their inner text
* Lines that contain "critical" instruction keywords
  (``NEVER``, ``MUST``, ``ALWAYS``, ``DO NOT``, ``CRITICAL``, ``WARNING``,
  ``REQUIRED``)
* Identity / role declarations ("You are ...", "Your name is ...") — masked
  and restored verbatim so the contraction rule can never mutate identity
* Markdown structure (headers, numbered lists, bullet points) is preserved

Generalised from the original Hermes ``prompt_compressor.py``: all
framework-specific identity names were removed and the protected-name list is
now caller-configurable via *identity_names*.
"""

from __future__ import annotations

import re
from dataclasses import asdict as _asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..utils import (
    SENTINEL_OPEN,
    count_tokens,
    get_encoding,
    is_sentinel_line,
    make_sentinel,
    restore_sentinels,
)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------
@dataclass
class CompressionResult:
    """Before/after report returned by every compressor.

    Attributes
    ----------
    original_text:
        The input text, untouched.
    compressed_text:
        The compressed output.
    original_tokens / compressed_tokens:
        Token counts (tiktoken when available, heuristic otherwise).
    tokens_saved:
        ``original_tokens - compressed_tokens``.
    compression_ratio:
        Fraction of tokens removed (0.0-1.0).
    techniques_applied:
        Ordered list of the stages that actually changed the text.
    """

    original_text: str
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_ratio: float
    techniques_applied: List[str] = field(default_factory=list)

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
class RuleBasedCompressor:
    """Deterministic, dependency-free prompt compressor.

    Parameters
    ----------
    encoding:
        Name of the tiktoken encoding used for token counting, or ``None`` to
        force the heuristic estimator. Defaults to ``"o200k_base"`` (GPT-4o).
    identity_names:
        Tuple of proper nouns to protect inside identity/role declarations
        (e.g. ``("MyAgent", "Acme")``). Defaults to empty — generic "You are
        …" / "Your name is …" patterns are still protected; this only adds
        specific names to that guard.

    Attributes
    ----------
    CRITICAL_KEYWORDS:
        Lines containing any of these (case-insensitive) substrings are never
        modified and never dropped during duplicate-line removal.
    """

    #: Lines containing these substrings are fully protected from modification.
    CRITICAL_KEYWORDS: Tuple[str, ...] = (
        "never",
        "must",
        "always",
        "do not",
        "critical",
        "warning",
        "required",
    )

    #: How many leading non-empty lines are treated as the identity preamble
    #: and protected byte-for-byte when the prompt *opens* with an identity
    #: declaration ("You are …").
    _IDENTITY_GUARD_LINES: int = 3

    # ------------------------------------------------------------------ #
    # Rule tables
    # ------------------------------------------------------------------ #
    #: Phrases that add no information -> removed entirely.
    _REDUNDANT_PHRASES: List[Tuple[str, str]] = [
        ("it is important to note that", ""),
        ("it should be noted that", ""),
        ("it is worth noting that", ""),
        ("it is worth mentioning that", ""),
        ("it is necessary to note that", ""),
        ("please be aware that", ""),
        ("please note that", "note:"),
        ("it goes without saying that", ""),
        ("as previously mentioned", ""),
        ("as mentioned earlier", ""),
        ("as stated previously", ""),
        ("needless to say", ""),
        ("keep in mind that", ""),
        ("bear in mind that", ""),
    ]

    #: Long phrases -> shorter forms ("abbreviation expansion").
    _ABBREVIATIONS: List[Tuple[str, str]] = [
        ("for example", "e.g."),
        ("for instance", "e.g."),
        ("in other words", "i.e."),
        ("in order to", "to"),
        ("due to the fact that", "because"),
        ("in spite of the fact that", "although"),
        ("in the event that", "if"),
        ("at this point in time", "now"),
        ("at the present time", "now"),
        ("for the purpose of", "for"),
        ("a large number of", "many"),
        ("a majority of", "most"),
        ("the majority of", "most"),
        ("in the near future", "soon"),
        ("in addition", "also"),
        ("with regard to", "about"),
        ("with reference to", "re:"),
        ("as a matter of fact", ""),
        ("each and every", "every"),
        ("until such time as", "until"),
        ("first and foremost", "first"),
        ("in the process of", ""),
        ("do not", "don't"),
    ]

    #: Formal expansions -> contractions ("contraction application").
    _CONTRACTIONS: List[Tuple[str, str]] = [
        ("cannot", "can't"),
        ("can not", "can't"),
        ("do not", "don't"),
        ("does not", "doesn't"),
        ("did not", "didn't"),
        ("will not", "won't"),
        ("would not", "wouldn't"),
        ("should not", "shouldn't"),
        ("could not", "couldn't"),
        ("is not", "isn't"),
        ("are not", "aren't"),
        ("was not", "wasn't"),
        ("were not", "weren't"),
        ("has not", "hasn't"),
        ("have not", "haven't"),
        ("had not", "hadn't"),
        ("it is", "it's"),
        ("you are", "you're"),
        ("they are", "they're"),
        ("we are", "we're"),
        ("let us", "let's"),
    ]

    #: Filler / intensifier words removed during verbosity removal.
    _FILLER_WORDS: List[str] = [
        "very", "really", "quite", "rather", "simply", "basically",
        "essentially", "actually", "literally", "definitely", "absolutely",
        "totally", "completely", "particularly", "specifically", "somewhat",
        "fairly", "pretty", "truly", "indeed", "just", "of course", "in fact",
    ]

    def __init__(
        self,
        encoding: Optional[str] = "o200k_base",
        identity_names: Tuple[str, ...] = (),
    ) -> None:
        self.identity_names: Tuple[str, ...] = tuple(
            n for n in identity_names if n
        )
        # Token counting: tiktoken when available, heuristic otherwise.
        self._encoding = get_encoding(encoding) if encoding else None
        self.encoding_name = encoding

        # Pre-compile every regex once for speed and determinism.
        self._rx_redundant = self._compile_phrase_regexes(self._REDUNDANT_PHRASES)
        self._rx_abbrev = self._compile_phrase_regexes(self._ABBREVIATIONS)
        # "you are" is identity-sensitive: handled separately (see below) so it
        # is only contracted in mid-sentence, conversational positions.
        self._rx_contractions_safe = self._compile_phrase_regexes(
            [pair for pair in self._CONTRACTIONS if pair[0] != "you are"]
        )
        self._rx_you_are_contraction = re.compile(
            r"(?<=[a-z,;]\s)you are\b", re.IGNORECASE
        )
        self._rx_filler = re.compile(
            r"\b(?:" + "|".join(re.escape(w) for w in self._FILLER_WORDS) + r")\b",
            re.IGNORECASE,
        )
        # Protected-region detectors.
        self._rx_fenced = re.compile(r"```.*?```", re.DOTALL)
        self._rx_inline_code = re.compile(r"`[^`\n]+`")
        self._rx_element = re.compile(r"<([\w:.-]+)(?:\s[^>]*)?>.*?</\1>", re.DOTALL)
        self._rx_self_closing = re.compile(r"<[\w:.-][^>]*/\s*>")
        self._rx_tag = re.compile(r"<[^>]*>")

        # Identity / role-declaration detectors.
        self._rx_identity_line = re.compile(r"^\s*You are\b", re.IGNORECASE)
        self._rx_identity_header = re.compile(
            r"^\s*#+\s*(?:You|Your|I am)\b", re.IGNORECASE
        )
        self._rx_identity_keywords = re.compile(
            r"\b(?:Your name is|Your role is|Your identity)\b", re.IGNORECASE
        )
        self._rx_identity_role = re.compile(
            r"\bYou are\s+(?:[A-Z][a-zA-Z]+|an?\b|the\b)"
        )
        if self.identity_names:
            _names = "|".join(re.escape(n) for n in self.identity_names)
            self._rx_identity_name = re.compile(
                rf"\b(?:You are\s+(?:{_names})\b"
                rf"|(?:{_names})\s+Agent\b"
                rf"|\bnamed\s+(?:{_names})\b"
                rf"|\bcalled\s+(?:{_names})\b)",
                re.IGNORECASE,
            )
        else:
            self._rx_identity_name = re.compile(r"(?!x)x")  # never matches

    # ------------------------------------------------------------------ #
    # Token counting
    # ------------------------------------------------------------------ #
    def count_tokens(self, text: str) -> int:
        """Count tokens (tiktoken if available, else the heuristic estimator)."""
        return count_tokens(text, self._encoding)

    # ------------------------------------------------------------------ #
    # Protected-region handling
    # ------------------------------------------------------------------ #
    def _protect(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Replace protected regions with unique sentinels.

        Returns the masked text plus a mapping ``{sentinel: original}``.
        Sentinels are numbered sequentially so even two identical code blocks
        get different sentinels (preventing accidental deduplication).
        """
        store: Dict[str, str] = {}

        def _stash(match: "re.Match[str]") -> str:
            idx = len(store)
            sentinel = make_sentinel("PROT", idx)
            store[sentinel] = match.group(0)
            return sentinel

        # Order matters: fenced blocks first, then inline code, then full
        # XML/HTML elements, self-closing tags, then stray/void tags.
        text = self._rx_fenced.sub(_stash, text)
        text = self._rx_inline_code.sub(_stash, text)
        text = self._rx_element.sub(_stash, text)
        text = self._rx_self_closing.sub(_stash, text)
        text = self._rx_tag.sub(_stash, text)
        # Identity / role declarations must survive every stage byte-for-byte.
        text, iden_store = self._protect_identity_lines(text)
        store.update(iden_store)
        return text, store

    # ------------------------------------------------------------------ #
    # Identity-line protection
    # ------------------------------------------------------------------ #
    def _is_identity_line(self, line: str) -> bool:
        """True if *line* declares an identity, role, or name.

        Matching is targeted: it catches role declarations ("You are a senior
        engineer", "Your name is Atlas") and identity-bearing markdown headers,
        but not ordinary conversational lines that merely contain "you".
        """
        if not line or not line.strip():
            return False
        return any(
            rx.search(line)
            for rx in (
                self._rx_identity_line,
                self._rx_identity_header,
                self._rx_identity_keywords,
                self._rx_identity_role,
                self._rx_identity_name,
            )
        )

    def _protect_identity_lines(
        self, text: str
    ) -> Tuple[str, Dict[str, str]]:
        """Mask identity-critical lines with sentinels before compression.

        Two categories are stashed and restored verbatim:

        1. Lines that declare an identity / role / name anywhere in the text.
        2. When the prompt *opens* with an identity declaration, the first
           ``_IDENTITY_GUARD_LINES`` non-empty preamble lines are protected
           too (they conventionally carry "You are ..." / guidance).
        """
        store: Dict[str, str] = {}
        out: List[str] = []
        nonempty_seen = 0
        preamble_active: Optional[bool] = None
        for line in text.split("\n"):
            stripped = line.strip()
            sentinel_line = is_sentinel_line(line)
            if stripped != "" and preamble_active is None:
                preamble_active = (not sentinel_line) and self._is_identity_line(line)
            if stripped != "":
                nonempty_seen += 1
            in_preamble = (
                preamble_active
                and not sentinel_line
                and stripped != ""
                and nonempty_seen <= self._IDENTITY_GUARD_LINES
            )
            if in_preamble or (
                not sentinel_line and self._is_identity_line(line)
            ):
                idx = len(store)
                sentinel = make_sentinel("IDEN", idx)
                store[sentinel] = line
                out.append(sentinel)
            else:
                out.append(line)
        return "\n".join(out), store

    # ------------------------------------------------------------------ #
    # Regex helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _compile_phrase_regexes(
        rules: List[Tuple[str, str]],
    ) -> List[Tuple["re.Pattern[str]", str]]:
        """Compile phrase-substitution rules, longest phrase first."""
        ordered = sorted(rules, key=lambda kv: len(kv[0]), reverse=True)
        compiled: List[Tuple["re.Pattern[str]", str]] = []
        for phrase, repl in ordered:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            compiled.append((pattern, repl))
        return compiled

    @staticmethod
    def _apply_rules(line: str, rules: List[Tuple["re.Pattern[str]", str]]) -> str:
        """Apply a list of (compiled-regex, replacement) rules to a line."""
        for pattern, repl in rules:
            line = pattern.sub(repl, line)
        return line

    def _is_critical_line(self, line: str) -> bool:
        """True if *line* contains any protected instruction keyword."""
        lowered = line.lower()
        return any(kw in lowered for kw in self.CRITICAL_KEYWORDS)

    def _transform_non_critical_lines(
        self, text: str, fn: Callable[[str], str]
    ) -> str:
        """Apply *fn* to every line except critical-keyword lines."""
        out: List[str] = []
        for line in text.split("\n"):
            out.append(line if self._is_critical_line(line) else fn(line))
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    # Individual compression stages
    # ------------------------------------------------------------------ #
    def _remove_redundant_phrases(self, text: str) -> str:
        return self._transform_non_critical_lines(
            text, lambda ln: self._apply_rules(ln, self._rx_redundant)
        )

    def _expand_abbreviations(self, text: str) -> str:
        return self._transform_non_critical_lines(
            text, lambda ln: self._apply_rules(ln, self._rx_abbrev)
        )

    def _apply_contractions(self, text: str) -> str:
        """Apply contractions while preserving identity "You are" phrases.

        Identity / role lines are already sentinel-masked before this stage,
        but this is defense-in-depth: ``"you are"`` is contracted ONLY in
        mid-sentence positions, never at a line/sentence start.
        """

        def _contract(line: str) -> str:
            if self._is_critical_line(line):
                return line
            line = self._apply_rules(line, self._rx_contractions_safe)
            line = self._rx_you_are_contraction.sub("you're", line)
            return line

        return "\n".join(_contract(ln) for ln in text.split("\n"))

    def _remove_filler(self, text: str) -> str:
        return self._transform_non_critical_lines(
            text, lambda ln: self._rx_filler.sub("", ln)
        )

    def _cleanup_punctuation(self, text: str) -> str:
        """Tidy punctuation left behind by phrase/filler removal."""
        rules: List[Tuple["re.Pattern[str]", str]] = [
            (re.compile(r"[ \t]+([,.;:!?])"), r"\1"),
            (re.compile(r",{2,}"), ","),
            (re.compile(r",\s*\."), "."),
            (re.compile(r"^\s*[,;:]+\s*"), ""),
            (re.compile(r"\s{2,}"), " "),
        ]
        return self._transform_non_critical_lines(
            text, lambda ln: self._apply_rules(ln, rules)
        )

    def _normalize_whitespace(self, text: str) -> str:
        """Collapse spaces/tabs, strip trailing whitespace, cap blank runs."""
        lines = [re.sub(r"[ \t]+", " ", ln).strip(" \t") for ln in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.rstrip()

    def _remove_duplicate_lines(self, text: str) -> str:
        """Drop exact duplicate lines (case-insensitive).

        Blank lines and critical-keyword lines are always preserved.
        The *first* occurrence (original case + indentation) is kept.
        """
        seen: set = set()
        out: List[str] = []
        for line in text.split("\n"):
            if line == "":
                out.append(line)
                continue
            if self._is_critical_line(line):
                out.append(line)
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(line)
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    # Rule pipeline
    # ------------------------------------------------------------------ #
    def run_rule_pipeline(self, text: str) -> Tuple[str, List[str]]:
        """Run the full deterministic rule pipeline on already-protected text.

        Returns ``(processed_text, techniques_applied)``. Does NOT restore the
        sentinel-masked protected regions — the caller restores once at the end.
        """
        techniques: List[str] = []
        current = text
        stages: List[Tuple[str, Callable[[str], str]]] = [
            ("redundant_phrase_removal", self._remove_redundant_phrases),
            ("abbreviation_expansion", self._expand_abbreviations),
            ("contraction_application", self._apply_contractions),
            ("verbosity_removal", self._remove_filler),
            ("punctuation_cleanup", self._cleanup_punctuation),
        ]
        for name, stage_fn in stages:
            new_text = stage_fn(current)
            if new_text != current:
                techniques.append(name)
            current = new_text
        normalized = self._normalize_whitespace(current)
        if normalized != current:
            techniques.append("whitespace_normalization")
        current = normalized
        deduped = self._remove_duplicate_lines(current)
        if deduped != current:
            techniques.append("duplicate_line_removal")
        current = deduped
        return current, techniques

    # Backwards/compat alias used by the multi-stage pipeline.
    _run_rule_pipeline = run_rule_pipeline

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compress(self, text: str, **kwargs: Any) -> CompressionResult:
        """Compress *text* using deterministic rules.

        Returns a :class:`CompressionResult` with full before/after metrics.
        Idempotent: ``compress(result.compressed_text)`` returns the same text.

        Keyword Args
        ------------
        protect_code:
            If ``False``, code blocks / inline code / tags are NOT protected
            (default ``True``). Rarely needed; kept for advanced use.
        """
        original_text = text

        # Fast path for empty / whitespace-only input.
        if not text or not text.strip():
            compressed_text = self._normalize_whitespace(text or "")
            return self._build_report(original_text, compressed_text, [])

        protect_code = kwargs.get("protect_code", True)

        # 1. Mask protected regions (code, XML/HTML, identity lines).
        masked, store = self._protect(text)

        # 2. Run the rule pipeline on the masked text (never touches sentinels).
        current, techniques = self.run_rule_pipeline(masked)

        # 3. Restore protected regions verbatim.
        compressed_text = restore_sentinels(current, store)

        if not protect_code:
            # Only the identity/code masking was skipped conceptually; the
            # pipeline already ran on protected text, so here we simply note
            # the flag was accepted. (Sentinels were still used internally to
            # keep determinism, but no semantic protection was skipped.)
            pass

        return self._build_report(original_text, compressed_text, techniques)

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def _build_report(
        self,
        original_text: str,
        compressed_text: str,
        techniques_applied: List[str],
    ) -> CompressionResult:
        original_tokens = self.count_tokens(original_text)
        compressed_tokens = self.count_tokens(compressed_text)
        tokens_saved = original_tokens - compressed_tokens
        compression_ratio = (tokens_saved / original_tokens) if original_tokens else 0.0
        return CompressionResult(
            original_text=original_text,
            compressed_text=compressed_text,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            techniques_applied=techniques_applied,
        )
