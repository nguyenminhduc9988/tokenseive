"""Codebase mapping — turn a repo into a token-budgeted map + code graph.

Works with zero dependencies (regex fallback). Install ``tokenseive[mapper]``
for full tree-sitter parsing across 20 languages and graphify visualisations.

Run::

    python examples/repo_mapping.py [path/to/repo]
"""

from __future__ import annotations

import sys

from tokenseive import CodebaseMapper
from tokenseive.mapper import graphify_available, tree_sitter_available


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    mapper = CodebaseMapper(repo, verbose=False)

    print("tree-sitter available :", tree_sitter_available())
    print("graphify available    :", graphify_available())
    print("files indexed         :", len(mapper.files))
    print("symbols               :", len(mapper.symbols))
    print("=" * 60)

    # 1. Ranked, token-budgeted overview of what exists.
    print(mapper.get_repo_map(max_tokens=512))

    # 2. Surgical retrieval: where does a symbol live, and what touches it?
    name = "main"
    hits = mapper.find_function(name)
    if hits:
        top = hits[0]
        print(f"\nFound '{name}' in {top['file']}:{top['line']}")
        trace = mapper.trace_call_chain(name, max_depth=2)
        print("Calls      :", trace["outbound"])
        print("Called by  :", trace["inbound"])

        # Compact context block you'd feed the model instead of the whole file.
        print("\n" + mapper.get_symbol_context(name)[:800])

    # 3. Stats: how much smaller is the map than the raw source?
    stats = mapper.get_stats()
    print(f"\nToken reduction: {stats['token_reduction_pct']}%")
    print(f"Graph nodes    : {stats['graph']['total_nodes']}")


if __name__ == "__main__":
    main()
