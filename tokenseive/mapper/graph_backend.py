"""Graph-native codebase mapper — token-budgeted symbol lookup with GCX1 compression.

This module provides a drop-in graph-native mapper built on TokenSeive's own
zero-dependency persistent code graph (:class:`NativeGraphMapper`). It indexes a
repository once into a persistent knowledge graph and answers precise
call-graph queries without re-parsing files on every run.

Key advantages over the tree-sitter/regex backend:
- **Persistent index** — symbols indexed once, queried instantly across sessions
- **Graph-native queries** — precise call-graph traversal, zero-false-positive reference finding
- **GCX1 compact wire format** — 70-90% token reduction via body elision
- **Zero dependencies** — pure Python AST; no external daemon, binary, or runtime

This is the default and only backend: a complete native implementation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .native_graph_mapper import NativeGraphMapper

logger = logging.getLogger("tokenseive.mapper.graph_backend")


def graph_backend_available() -> bool:
    """Return whether the graph-native backend is available.

    The backend is pure Python with no external dependencies, so this is
    always ``True``.
    """
    return True


class GraphCodebaseMapper:
    """Graph-native codebase mapper with persistent symbol indexing and GCX1 compression.

    A drop-in alternative to :class:`CodebaseMapper` that answers queries from
    a persistent, zero-dependency code graph instead of re-parsing files on
    every run.

    Example
    -------
    >>> from tokenseive.mapper import GraphCodebaseMapper  # doctest: +SKIP
    >>> mapper = GraphCodebaseMapper("/path/to/repo")        # doctest: +SKIP
    >>> print(mapper.get_repo_map(max_tokens=1024))         # doctest: +SKIP
    >>> mapper.find_function("my_func")                     # doctest: +SKIP

    Parameters
    ----------
    repo_path : str | Path
        Path to the repository.
    encoding : str, optional
        Token encoding (default: ``o200k_base``).
    verbose : bool, optional
        Log additional diagnostics (default: ``False``).
    use_native : bool, optional
        Accepted for API compatibility; the native backend is always used.
    force_external : bool, optional
        Accepted for API compatibility; ignored (no external backend exists).
    use_extreme_compression : bool, optional
        Use extreme GCX1 compression for 95%+ token reduction (default: ``False``).
    """

    def __init__(
        self,
        repo_path,
        *,
        encoding: Optional[str] = None,
        verbose: bool = False,
        use_native: bool = True,
        force_external: bool = False,
        use_extreme_compression: bool = False,
    ) -> None:
        self._repo_path = Path(repo_path).resolve()
        self._verbose = verbose
        self._encoding = encoding or "o200k_base"
        self._backend = "native_graph"

        # ``use_native`` / ``force_external`` are accepted for API compatibility;
        # the zero-dependency native backend is the only backend.
        self._mapper = NativeGraphMapper(
            self._repo_path,
            encoding=self._encoding,
            verbose=self._verbose,
            use_extreme_compression=use_extreme_compression,
        )

        if self._verbose:
            kind = "extreme" if use_extreme_compression else "standard"
            logger.info(
                f"GraphCodebaseMapper initialized for {self._repo_path} ({kind} compression)"
            )

    # ---- Compatibility properties ---------------------------------------- #
    @property
    def repo_path(self) -> Path:
        """Repository path."""
        return self._repo_path

    @property
    def files(self) -> List[str]:
        """List all indexed files in the repository."""
        return self._mapper.files

    @property
    def symbols(self) -> List[Dict]:
        """Return a sample of indexed symbols (for compatibility)."""
        return self._mapper.symbols

    def count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken (if available) or a heuristic."""
        return self._mapper.count_tokens(text)

    # ---- Core query methods --------------------------------------------- #
    def find_function(self, name: str) -> List[Dict]:
        """Find all functions/methods matching *name*."""
        return self._mapper.find_function(name)

    def find_class(self, name: str) -> List[Dict]:
        """Find all classes matching *name*."""
        return self._mapper.find_class(name)

    def trace_call_chain(self, function_name: str, max_depth: int = 3) -> Dict:
        """Trace what *function_name* calls (outbound) and what calls it (inbound)."""
        return self._mapper.trace_call_chain(function_name, max_depth)

    # Backwards-compatible alias.
    trace_calls = trace_call_chain

    def get_symbol_context(self, symbol_name: str) -> str:
        """Get a compact context block for a symbol.

        Uses GCX1 compression for maximum token reduction (70-90% savings).
        """
        return self._mapper.get_symbol_context(symbol_name)

    def get_context(self, symbol_name: str) -> str:
        """Alias for :meth:`get_symbol_context`."""
        return self._mapper.get_symbol_context(symbol_name)

    def get_dependencies(self, file_path: str) -> Dict:
        """Get imports of a file and the files that depend on it."""
        return self._mapper.get_dependencies(file_path)

    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted, ranked symbol map."""
        return self._mapper.get_repo_map(max_tokens)

    def get_code_graph(self, *, max_graph_files: int = 250) -> Dict[str, Any]:
        """Build a structured code graph."""
        return self._mapper.get_code_graph(max_graph_files=max_graph_files)

    def export_graph(
        self, format: str = "json", output_path: Optional[str] = None
    ) -> str:
        """Export the code graph as JSON."""
        return self._mapper.export_graph(format=format, output_path=output_path)

    def get_stats(self) -> Dict:
        """Return mapping statistics."""
        return self._mapper.get_stats()
