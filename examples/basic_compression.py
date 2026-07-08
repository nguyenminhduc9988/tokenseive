"""Basic rule-based compression — works with zero dependencies.

Run::

    python examples/basic_compression.py
"""

from __future__ import annotations

from tokenseive import RuleBasedCompressor


def main() -> None:
    prompt = (
        "You are Atlas, a helpful coding assistant.\n"
        "\n"
        "It is important to note that, in order to process the data correctly, "
        "you really should be careful.\n"
        "It goes without saying that we cannot skip the preparation step.\n"
        "For example, a large number of helpers are available out of the box.\n"
        "In addition, please note that you must NEVER delete user data.\n"
        "\n"
        "```python\n"
        "def transform(x):\n"
        "    return x * 2\n"
        "```\n"
    )

    compressor = RuleBasedCompressor()
    result = compressor.compress(prompt)

    print("=" * 60)
    print("BASIC (RULE-BASED) COMPRESSION")
    print("=" * 60)
    print(f"Original tokens   : {result.original_tokens}")
    print(f"Compressed tokens : {result.compressed_tokens}")
    print(f"Tokens saved      : {result.tokens_saved} ({result.compression_ratio:.1%})")
    print(f"Techniques        : {', '.join(result.techniques_applied)}")
    print("-" * 60)
    print(result.compressed_text)
    print("=" * 60)

    # Dict-style access still works for callers used to dict results.
    assert result["compressed_text"] == result.compressed_text


if __name__ == "__main__":
    main()
