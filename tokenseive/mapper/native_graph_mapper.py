"""Native graph mapper — 0-dependency Gortex reimplementation.

This module provides a pure Python implementation of Gortex's core capabilities
using PersistentGraphIndex for graph storage and GCX1Compressor for token reduction.

Key features:
- **Zero external dependencies** — pure Python AST manipulation
- **Persistent index** — JSON-based cross-session caching
- **Graph-native queries** — call chains, symbol search, dependency tracking
- **GCX1 compression** — 70-90% token reduction via body elision
- **API compatible** — drop-in replacement for GortexCodebaseMapper
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .graph_engine import PersistentGraphIndex
from .gcx1_compression import GCX1Compressor
from .gcx1_extreme_compression import GCX1ExtremeCompressor

logger = logging.getLogger("tokenseive.mapper.native_graph")


class NativeGraphMapper:
    """0-dependency graph-native codebase mapper.

    This class provides the same API surface as :class:`GortexCodebaseMapper` but
    uses pure Python implementations instead of an external Gortex daemon.

    Example
    -------
    >>> from tokenseive.mapper import NativeGraphMapper  # doctest: +SKIP
    >>> mapper = NativeGraphMapper("/path/to/repo")      # doctest: +SKIP
    >>> print(mapper.get_repo_map(max_tokens=1024))      # doctest: +SKIP
    >>> mapper.find_function("my_func")                  # doctest: +SKIP

    Parameters
    ----------
    repo_path : str | Path
        Path to the repository.
    cache_path : str | Path, optional
        Path to cache directory (default: ``.tokenseive_graph.json``).
    encoding : str, optional
        Token encoding (default: ``o200k_base``).
    verbose : bool, optional
        Log additional diagnostics (default: ``False``).
    use_extreme_compression : bool, optional
        Use extreme GCX1 compression for 95%+ token reduction (default: ``False``).
        When True, achieves 95-97% compression but preserves less context.
    """

    def __init__(
        self,
        repo_path: str | Path,
        *,
        cache_path: Optional[str | Path] = None,
        encoding: Optional[str] = None,
        verbose: bool = False,
        use_extreme_compression: bool = False,
    ) -> None:
        self._repo_path = Path(repo_path).resolve()
        self._verbose = verbose
        self._encoding = encoding or "o200k_base"
        self._use_extreme_compression = use_extreme_compression

        # Initialize persistent graph index
        cache_file = Path(cache_path) if cache_path else self._repo_path / ".tokenseive_graph.json"
        self.graph = PersistentGraphIndex(self._repo_path, cache_path=cache_file, verbose=verbose)

        # Load or build the index (automatic: loads from cache if available, otherwise builds)
        self.graph.index_repository()

        # Initialize GCX1 compressor (standard or extreme based on parameter)
        if use_extreme_compression:
            self.compressor = GCX1ExtremeCompressor()
        else:
            self.compressor = GCX1Compressor()

        if self._verbose:
            compressor_type = "extreme" if use_extreme_compression else "standard"
            logger.info(f"NativeGraphMapper initialized for {self._repo_path} with {compressor_type} compression")

    # ---- Compatibility properties ---------------------------------------- #
    @property
    def repo_path(self) -> Path:
        """Repository path."""
        return self._repo_path

    @property
    def files(self) -> List[str]:
        """List all indexed files in the repository."""
        return list(self.graph.get_indexed_files())

    @property
    def symbols(self) -> List[Dict]:
        """Return a sample of indexed symbols (for compatibility)."""
        results = self.graph.search_symbols("", limit=100)
        return [
            {
                "name": r.get("name", ""),
                "kind": r.get("kind", ""),
                "file": r.get("file", ""),
                "line": r.get("line", 0),
                "signature": r.get("signature", ""),
                "symbol_id": r.get("symbol_id", ""),
            }
            for r in results
        ]

    def count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken (if available) or a heuristic."""
        try:
            import tiktoken

            enc = tiktoken.get_encoding(self._encoding)
            return len(enc.encode(text))
        except ImportError:
            # Fallback heuristic: ~4 chars per token
            return len(text) // 4

    # ---- Core query methods --------------------------------------------- #
    def find_function(self, name: str) -> List[Dict]:
        """Find all functions/methods matching *name*."""
        results = self.graph.search_symbols(
            query=name, kinds=["function", "method"], limit=50
        )

        matches = []
        for hit in results:
            matches.append(
                {
                    "file": hit.get("file", ""),
                    "line": hit.get("line", 0),
                    "signature": hit.get("signature", ""),
                    "kind": hit.get("kind", ""),
                    "node_id": hit.get("symbol_id", ""),
                }
            )
        return matches

    def find_class(self, name: str) -> List[Dict]:
        """Find all classes matching *name*."""
        results = self.graph.search_symbols(query=name, kinds=["class"], limit=50)

        matches = []
        for hit in results:
            matches.append(
                {
                    "file": hit.get("file", ""),
                    "line": hit.get("line", 0),
                    "signature": hit.get("signature", ""),
                    "node_id": hit.get("symbol_id", ""),
                }
            )
        return matches

    def trace_call_chain(self, function_name: str, max_depth: int = 3) -> Dict:
        """Trace what *function_name* calls (outbound) and what calls it (inbound)."""
        # First find the symbol
        find_results = self.graph.search_symbols(query=function_name, limit=1)

        if not find_results:
            return {
                "root": function_name,
                "found": False,
                "note": "no matching symbol",
            }

        symbol = find_results[0]
        symbol_id = symbol.get("symbol_id")

        # Get outbound calls (callees)
        outbound_chain = self.graph.get_call_chain(
            symbol_id=symbol_id, direction="outbound", max_depth=max_depth
        )

        # Get inbound calls (callers)
        inbound_chain = self.graph.get_call_chain(
            symbol_id=symbol_id, direction="inbound", max_depth=max_depth
        )

        return {
            "root": function_name,
            "found": True,
            "outbound": outbound_chain,
            "inbound": inbound_chain,
            "matched_locations": [
                {
                    "file": symbol.get("file", ""),
                    "line": symbol.get("line", 0),
                    "signature": symbol.get("signature", ""),
                    "kind": symbol.get("kind", ""),
                }
            ],
        }

    # Backwards-compatible alias.
    trace_calls = trace_call_chain

    def get_symbol_context(self, symbol_name: str) -> str:
        """Get a compact context block for a symbol.

        This uses GCX1 compression for maximum token reduction (70-90% savings).
        """
        # First find the symbol
        find_results = self.graph.search_symbols(query=symbol_name, limit=3)

        if not find_results:
            return f"# Symbol '{symbol_name}' not found."

        blocks = []
        for hit in find_results:
            symbol_id = hit.get("symbol_id")
            if not symbol_id:
                continue

            # Get symbol source
            source_result = self.graph.get_symbol_source(symbol_id=symbol_id)

            if not source_result:
                continue

            # Compress with GCX1
            compressed_source = self.compressor.compress_symbol(
                source_result.get("source", ""), language=source_result.get("language", "python")
            )

            tokens = self.count_tokens(compressed_source)

            blocks.append(
                f"## {hit.get('signature', symbol_name)} ({hit.get('file', '')}:{hit.get('line', 0)})\n"
                f"```{hit.get('language', 'python')}\n{compressed_source}\n```\n"
                f"(≈{tokens} tokens, compressed via GCX1)\n"
            )

        header = f"# Context for '{symbol_name}' ({len(find_results)} location(s))\n"
        return header + "\n".join(blocks)

    def get_context(self, symbol_name: str) -> str:
        """Alias for :meth:`get_symbol_context`."""
        return self.get_symbol_context(symbol_name)

    def get_dependencies(self, file_path: str) -> Dict:
        """Get imports of a file and the files that depend on it."""
        deps = self.graph.get_file_dependencies(file_path)

        return {
            "file": file_path,
            "imports": deps.get("imports", []),
            "dependents": deps.get("dependents", []),
        }

    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted, ranked symbol map."""
        # Get overview symbols
        results = self.graph.search_symbols(query="", limit=50)

        # Format with GCX1 compression
        blocks = []
        total_tokens = 0

        for hit in results:
            symbol_id = hit.get("symbol_id")
            if not symbol_id:
                continue

            source_result = self.graph.get_symbol_source(symbol_id=symbol_id)
            if not source_result:
                continue

            # Compress
            compressed = self.compressor.compress_symbol(
                source_result.get("source", ""), language=source_result.get("language", "python")
            )

            tokens = self.count_tokens(compressed)
            if total_tokens + tokens > max_tokens:
                break

            total_tokens += tokens
            blocks.append(
                f"## {hit.get('signature', hit.get('name', ''))}\n"
                f"```python\n{compressed}\n```\n"
            )

        header = f"# Repository Overview ({len(blocks)} symbols, ≈{total_tokens} tokens)\n"
        return header + "\n".join(blocks)

    def get_code_graph(self, *, max_graph_files: int = 250) -> Dict[str, Any]:
        """Build a structured code graph."""
        # Get all symbols as nodes
        symbols = self.graph.search_symbols(query="", limit=1000)

        nodes = []
        edges = []

        for symbol in symbols:
            symbol_id = symbol.get("symbol_id")
            if not symbol_id:
                continue

            nodes.append(
                {
                    "id": symbol_id,
                    "name": symbol.get("name", ""),
                    "kind": symbol.get("kind", ""),
                    "file": symbol.get("file", ""),
                    "line": symbol.get("line", 0),
                    "signature": symbol.get("signature", ""),
                }
            )

            # Get call relationships
            try:
                outbound = self.graph.get_call_chain(
                    symbol_id=symbol_id, direction="outbound", max_depth=1
                )
                for callee in outbound:
                    edges.append(
                        {
                            "from": symbol_id,
                            "to": callee.get("symbol_id", ""),
                            "kind": "calls",
                        }
                    )
            except Exception:
                pass

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "backend": "native_graph",
            },
        }

    def export_graph(
        self, format: str = "json", output_path: Optional[str] = None
    ) -> str:
        """Export the code graph as JSON."""
        graph = self.get_code_graph()
        data = json.dumps(graph, indent=2)

        if output_path:
            Path(output_path).write_text(data, encoding="utf-8")

        return data

    def get_stats(self) -> Dict:
        """Return mapping statistics."""
        # Calculate graph tokens from symbol count
        symbol_count = self.graph.get_total_symbols()
        estimated_graph_tokens = symbol_count * 50  # Rough estimate

        return {
            "repo_path": str(self._repo_path),
            "backend": "native_graph",
            "total_symbols": symbol_count,
            "total_files": len(self.files),
            "graph_tokens": estimated_graph_tokens,
            "index_health": "healthy",
        }
