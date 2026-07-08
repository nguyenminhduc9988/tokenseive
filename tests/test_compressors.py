"""Tests for the compressor layer (rule-based + pipeline).

These run with ZERO optional dependencies — tiktoken may or may not be
present, so all assertions use relative checks rather than exact token counts.
"""

from __future__ import annotations

import pytest

from tokenseive import (
    CompressionPipeline,
    CompressionResult,
    RuleBasedCompressor,
)


# ---------------------------------------------------------------------------
# CompressionResult
# ---------------------------------------------------------------------------
def test_result_dataclass_fields():
    r = RuleBasedCompressor().compress("hello world")
    assert isinstance(r, CompressionResult)
    for field in (
        "original_text",
        "compressed_text",
        "original_tokens",
        "compressed_tokens",
        "tokens_saved",
        "compression_ratio",
        "techniques_applied",
    ):
        assert hasattr(r, field)


def test_result_dict_access():
    r = RuleBasedCompressor().compress("hello world")
    assert r["compressed_text"] == r.compressed_text
    assert r.as_dict()["tokens_saved"] == r.tokens_saved
    assert r.get("missing", "default") == "default"


def test_arithmetic_consistency():
    r = RuleBasedCompressor().compress("it is important to note that this is needed")
    assert r.original_tokens - r.compressed_tokens == r.tokens_saved
    expected = (r.tokens_saved / r.original_tokens) if r.original_tokens else 0.0
    assert r.compression_ratio == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Core compression behaviour
# ---------------------------------------------------------------------------
def test_redundant_phrase_removed():
    text = "It is important to note that we ship code."
    out = RuleBasedCompressor().compress(text).compressed_text.lower()
    assert "it is important to note that" not in out
    assert "ship code" in out


def test_abbreviation_expansion():
    text = "In order to build the app, for example, we need tools."
    out = RuleBasedCompressor().compress(text).compressed_text.lower()
    assert "in order to" not in out
    assert "e.g." in out


def test_contraction_application():
    # Note: a line containing "do not" is treated as critical and protected,
    # so this text deliberately avoids critical keywords to exercise the
    # contraction stage.
    text = "We cannot proceed because they are busy and will not finish."
    out = RuleBasedCompressor().compress(text).compressed_text.lower()
    assert "can't" in out
    assert "won't" in out
    assert "they're" in out


def test_filler_removal():
    text = "This is very really quite important actually."
    out = RuleBasedCompressor().compress(text).compressed_text.lower()
    assert "very" not in out
    assert "really" not in out
    assert "important" in out


def test_duplicate_line_removal():
    text = "Line one.\nLine one.\nLine two."
    out = RuleBasedCompressor().compress(text).compressed_text
    assert out.count("Line one.") == 1
    assert "Line two." in out


def test_whitespace_normalization():
    text = "a    b\n\n\n\nc"
    out = RuleBasedCompressor().compress(text).compressed_text
    assert "    " not in out  # collapsed
    assert "\n\n\n" not in out  # blank runs capped


# ---------------------------------------------------------------------------
# Protected regions
# ---------------------------------------------------------------------------
def test_code_block_protected(sample_prompt):
    out = RuleBasedCompressor().compress(sample_prompt).compressed_text
    assert "def hello(name):" in out
    assert "print(f'hello {name}')" in out


def test_inline_code_protected():
    text = "Use `print(value)` to show output."
    out = RuleBasedCompressor().compress(text).compressed_text
    assert "`print(value)`" in out


def test_xml_tag_protected(sample_prompt):
    out = RuleBasedCompressor().compress(sample_prompt).compressed_text
    assert "<system>You are the orchestrator</system>" in out


def test_identity_line_protected(sample_prompt):
    out = RuleBasedCompressor().compress(sample_prompt).compressed_text
    # Identity declaration survives byte-for-byte.
    assert "You are Atlas Agent, a senior engineer." in out
    assert "Your name is Atlas." in out


def test_you_are_not_contracted_at_sentence_start():
    text = "You are a senior developer. When you are ready, begin."
    out = RuleBasedCompressor().compress(text).compressed_text
    # Line/sentence-initial "You are" must be preserved.
    assert "You are a senior developer." in out


def test_critical_keyword_lines_protected():
    text = "You must NEVER delete the database.\nNever delete it again."
    out = RuleBasedCompressor().compress(text).compressed_text
    assert "You must NEVER delete the database." in out
    assert "Never delete it again." in out


# ---------------------------------------------------------------------------
# Idempotency & edge cases
# ---------------------------------------------------------------------------
def test_idempotent():
    rc = RuleBasedCompressor()
    text = "It is important to note that, in order to proceed, we really must be careful."
    first = rc.compress(text).compressed_text
    second = rc.compress(first).compressed_text
    assert first == second


def test_empty_input():
    rc = RuleBasedCompressor()
    r = rc.compress("")
    assert r.compressed_text == ""
    assert r.tokens_saved == 0


def test_whitespace_only_input():
    rc = RuleBasedCompressor()
    r = rc.compress("   \n\n  \t  ")
    assert r.compressed_text.strip() == ""


def test_tokens_decrease_or_equal(sample_prompt):
    r = RuleBasedCompressor().compress(sample_prompt)
    assert r.compressed_tokens <= r.original_tokens


def test_count_tokens_positive():
    rc = RuleBasedCompressor()
    assert rc.count_tokens("hello world") > 0
    assert rc.count_tokens("") == 0


def test_identity_names_configurable():
    rc = RuleBasedCompressor(identity_names=("Acme",))
    text = "You are Acme Agent, the system. Please note that."
    out = rc.compress(text).compressed_text
    assert "Acme" in out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def test_pipeline_rules_matches_direct():
    text = "It is important to note that we really must build it."
    direct = RuleBasedCompressor().compress(text).compressed_text
    pipe = CompressionPipeline(backend="rules").compress(text).compressed_text
    assert direct == pipe


def test_pipeline_invalid_backend_raises():
    with pytest.raises(ValueError):
        CompressionPipeline(backend="bogus")


def test_pipeline_invalid_rate_raises():
    with pytest.raises(ValueError):
        CompressionPipeline(backend="rules", rate=0.0)
    with pytest.raises(ValueError):
        CompressionPipeline(backend="rules", rate=1.5)


def test_pipeline_rules_backend_always_available():
    p = CompressionPipeline(backend="rules")
    assert "rules" in CompressionPipeline.available_backends()


def test_pipeline_selective_falls_back_when_unavailable(monkeypatch):
    # Force selective context to look unavailable.
    from tokenseive.compressors import pipeline as pl

    monkeypatch.setattr(pl.SelectiveContextCompressor, "available", lambda: False)
    p = CompressionPipeline(backend="selective")
    r = p.compress("It is important to note that this works.")
    # Should fall back to rules without raising.
    assert any("fallback" in t for t in r.techniques_applied) or r.compressed_text
