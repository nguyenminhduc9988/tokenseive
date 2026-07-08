"""ML-backed compression — requires optional extras.

    pip install tokenseive[ml]

Shows the multi-stage cascade (rules -> selective_context -> llmlingua2) and
graceful degradation: if the ML packages are missing, the pipeline falls back
to the rule-based result instead of raising.

Run::

    python examples/ml_compression.py
"""

from __future__ import annotations

from tokenseive import CompressionPipeline


def main() -> None:
    prompt = (
        "It is important to note that, in order to understand this document, "
        "you should read it carefully. The system provides a number of features "
        "that are designed to help you accomplish your tasks. For example, it "
        "can compress prompts, map codebases, and reduce output verbosity."
    )

    # The cascade stops as soon as the target keep-rate is reached.
    pipeline = CompressionPipeline(backend="multi", rate=0.5)
    print("Available backends:", CompressionPipeline.available_backends())

    result = pipeline.compress(prompt)
    print(f"Original tokens   : {result.original_tokens}")
    print(f"Compressed tokens : {result.compressed_tokens}")
    print(f"Tokens saved      : {result.tokens_saved} ({result.compression_ratio:.1%})")
    print(f"Techniques        : {', '.join(result.techniques_applied)}")
    print("-" * 60)
    print(result.compressed_text)

    # Use a single ML backend directly (raises ImportError if not installed).
    try:
        from tokenseive import LLMLingua2Compressor

        llm = LLMLingua2Compressor()
        r2 = llm.compress(prompt, rate=0.5)
        print("\nLLMLingua-2 ratio:", f"{r2.compression_ratio:.1%}")
    except ImportError as exc:
        print(f"\n(Skipped direct LLMLingua-2: {exc})")


if __name__ == "__main__":
    main()
