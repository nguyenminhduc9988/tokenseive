"""Tests for the tool-compression layer (HeadroomCompressor).

These run with ZERO optional dependencies — ``headroom-ai`` is NOT installed
in CI, so every test exercises the built-in fallback path and the
strict-import guard. If ``headroom-ai`` happens to be installed in some
environment, the fallback-specific tests skip (and the availability tests
flip accordingly) so the suite stays green everywhere.
"""

from __future__ import annotations

import json

import pytest

from tokenseive.tool_compression import (
    DEFAULT_TOOL_POLICY,
    HeadroomCompressor,
    ToolCompressionResult,
    headroom_available,
)

_NO_HEADROOM = not headroom_available()


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
def test_result_dataclass_fields():
    c = HeadroomCompressor()
    r = c.compress_tool_output("search_files", "short output text")
    assert isinstance(r, ToolCompressionResult)
    for field in (
        "original_text",
        "compressed_text",
        "original_tokens",
        "compressed_tokens",
        "tokens_saved",
        "compression_ratio",
        "transforms_applied",
    ):
        assert hasattr(r, field), f"missing field: {field}"


def test_result_dict_access():
    c = HeadroomCompressor()
    r = c.compress_tool_output("search_files", "x" * 300)
    assert r["compressed_text"] == r.compressed_text
    assert r.as_dict()["tokens_saved"] == r.tokens_saved
    assert r.get("missing_key", "default") == "default"


def test_arithmetic_consistency():
    c = HeadroomCompressor()
    big = json.dumps({"results": list(range(100))})
    r = c.compress_tool_output("search_files", big)
    assert r.original_tokens - r.compressed_tokens == r.tokens_saved
    expected = (r.tokens_saved / r.original_tokens) if r.original_tokens else 0.0
    assert r.compression_ratio == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Headroom availability / strict import guard
# ---------------------------------------------------------------------------
def test_available_reflects_environment():
    assert HeadroomCompressor.available() == headroom_available()


@pytest.mark.skipif(headroom_available(), reason="headroom-ai is installed")
def test_strict_raises_helpful_importerror():
    """HeadroomCompressor(strict=True) raises ImportError with install hint."""
    with pytest.raises(ImportError) as excinfo:
        HeadroomCompressor(strict=True)
    msg = str(excinfo.value).lower()
    assert "headroom" in msg
    assert "pip install" in msg  # actionable install hint


@pytest.mark.skipif(headroom_available(), reason="headroom-ai is installed")
def test_require_headroom_raises_helpful_importerror():
    c = HeadroomCompressor()
    with pytest.raises(ImportError) as excinfo:
        c.require_headroom()
    assert "pip install" in str(excinfo.value).lower()


@pytest.mark.skipif(headroom_available(), reason="headroom-ai is installed")
def test_default_constructor_does_not_raise():
    # Lazy / fallback mode: must succeed even without headroom-ai.
    c = HeadroomCompressor()
    assert c.available() is False


# ---------------------------------------------------------------------------
# should_compress policy
# ---------------------------------------------------------------------------
def test_should_compress_large_json_true():
    c = HeadroomCompressor()
    big = json.dumps({"files": list(range(100))})
    assert c.should_compress("search_files", big) is True


def test_should_compress_small_string_false():
    c = HeadroomCompressor()
    assert c.should_compress("search_files", "tiny output") is False


def test_should_compress_below_token_threshold_false():
    c = HeadroomCompressor()
    # Plenty of chars but very few tokens.
    assert c.should_compress("search_files", "a" * 300) is False


def test_should_compress_bash_and_protected_tools_false():
    c = HeadroomCompressor()
    big = "x " * 3000  # ~3000 chars, plenty of tokens
    for tool in DEFAULT_TOOL_POLICY["never_compress"]:
        assert c.should_compress(tool, big) is False, f"{tool} should never compress"


def test_should_compress_respects_custom_policy():
    c = HeadroomCompressor(
        policy={"never_compress": ["my_tool"], "min_chars": 10, "min_tokens": 1}
    )
    big = "x " * 3000
    assert c.should_compress("my_tool", big) is False
    assert c.should_compress("other_tool", big) is True


def test_default_policy_shape():
    for key in ("always_compress", "never_compress", "min_chars", "min_tokens"):
        assert key in DEFAULT_TOOL_POLICY
    assert "search_files" in DEFAULT_TOOL_POLICY["always_compress"]
    assert "bash" in DEFAULT_TOOL_POLICY["never_compress"]
    assert DEFAULT_TOOL_POLICY["min_chars"] == 200
    assert DEFAULT_TOOL_POLICY["min_tokens"] == 50


# ---------------------------------------------------------------------------
# Fallback compression (headroom-ai NOT installed)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _NO_HEADROOM, reason="headroom-ai installed; fallback not used")
class TestFallbackCompression:
    def test_small_payload_unchanged(self):
        c = HeadroomCompressor()
        small = "not worth compressing"
        r = c.compress_tool_output("search_files", small)
        assert r.compressed_text == small
        assert r.tokens_saved == 0
        assert r.compression_ratio == 0.0
        assert r.transforms_applied == []

    def test_truncates_large_arrays(self):
        c = HeadroomCompressor()
        payload = {"results": [{"id": i, "name": f"item-{i}"} for i in range(100)]}
        r = c.compress_tool_output("search_files", payload)
        assert len(r.compressed_text) < len(json.dumps(payload))
        assert r.tokens_saved > 0
        assert r.compression_ratio > 0.0
        parsed = json.loads(r.compressed_text)
        markers = [
            item for item in parsed["results"]
            if isinstance(item, dict) and item.get("truncated")
        ]
        assert markers, "expected a truncation marker in the array"
        assert markers[0]["_omitted"] == 90
        assert any("fallback" in t for t in r.transforms_applied)

    def test_collapses_long_plain_text(self):
        c = HeadroomCompressor()
        text = "meaningful line of output\n" * 500
        r = c.compress_tool_output("read_file", text)
        assert len(r.compressed_text) < len(text)
        assert "omitted" in r.compressed_text
        assert any("text_headtail" in t for t in r.transforms_applied)

    def test_no_op_when_not_compressible(self):
        c = HeadroomCompressor()
        # Passes the size gate but is neither JSON nor long enough for head/tail.
        text = " ".join(["word"] * 60)
        r = c.compress_tool_output("search_files", text)
        assert r.compressed_text == text
        assert r.tokens_saved == 0

    def test_never_compress_tool_left_untouched(self):
        c = HeadroomCompressor()
        big = "x " * 3000
        r = c.compress_tool_output("bash", big)
        assert r.compressed_text == big
        assert r.tokens_saved == 0
        assert r.transforms_applied == []

    def test_accepts_dict_content_directly(self):
        c = HeadroomCompressor()
        payload = {"files": list(range(100))}
        r = c.compress_tool_output("list_files", payload)
        # original_text should be the JSON serialisation of the dict
        assert json.loads(r.original_text) == payload
        assert r.tokens_saved >= 0

    def test_compression_never_expands_payload(self):
        c = HeadroomCompressor()
        big = json.dumps({"data": list(range(200))})
        r = c.compress_tool_output("code_search", big)
        assert len(r.compressed_text) <= len(r.original_text)


# ---------------------------------------------------------------------------
# Batch compression: compress_messages
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _NO_HEADROOM, reason="headroom-ai installed; fallback not used")
class TestCompressMessages:
    def _big(self):
        return json.dumps({"files": list(range(100))})

    def test_compresses_old_tool_results_only(self):
        c = HeadroomCompressor()
        big = self._big()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "name": "search_files", "content": big},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "again"},
            {"role": "tool", "name": "search_files", "content": big},
        ]
        out = c.compress_messages(msgs, protect_recent=2)
        # Recent tool message (last) is protected.
        assert out[4]["content"] == big
        # Older tool message was compressed.
        assert out[1]["content"] != big
        assert out[1]["metadata"]["_headroom_compressed"] is True
        assert out[1]["metadata"]["_headroom_tokens_saved"] > 0
        # Non-tool messages untouched.
        assert out[0]["content"] == "hello"
        assert out[2]["content"] == "ok"

    def test_does_not_mutate_input(self):
        c = HeadroomCompressor()
        big = self._big()
        msgs = [{"role": "tool", "name": "search_files", "content": big}]
        c.compress_messages(msgs, protect_recent=0)
        assert msgs[0]["content"] == big
        assert "metadata" not in msgs[0]

    def test_empty_list(self):
        assert HeadroomCompressor().compress_messages([]) == []

    def test_all_recent_protected(self):
        c = HeadroomCompressor()
        big = self._big()
        msgs = [
            {"role": "tool", "name": "search_files", "content": big},
            {"role": "tool", "name": "search_files", "content": big},
        ]
        out = c.compress_messages(msgs, protect_recent=4)
        assert all(m["content"] == big for m in out)
