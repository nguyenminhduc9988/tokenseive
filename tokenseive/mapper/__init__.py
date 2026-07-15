"""Codebase mapping for fast LLM code retrieval.

Public API
----------
* :class:`CodebaseMapper` — unified mapper combining two layers:

  1. **Repo Map** (tree-sitter/regex + reference ranking) -> ranked symbol overview
  2. **Code Graph** (graphify, optional) -> nodes/edges for surgical retrieval

Zero dependencies by default: when ``tree-sitter`` is missing, parsing falls
back to regex. Install ``tokenseive[mapper]`` for full multi-language parsing
and graphify support.

Example
-------
>>> from tokenseive.mapper import CodebaseMapper
>>> mapper = CodebaseMapper("/path/to/repo", verbose=False)  # doctest: +SKIP
>>> print(mapper.get_repo_map(max_tokens=1024))               # doctest: +SKIP
>>> mapper.find_function("my_func")                           # doctest: +SKIP
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .code_graph import build_code_graph, export_graph, graphify_available
from .gortex_backend import GortexCodebaseMapper, gortex_available
from .native_graph_mapper import NativeGraphMapper
from .gcx1_compression import GCX1Compressor
from .gcx1_extreme_compression import GCX1ExtremeCompressor
from .graph_engine import PersistentGraphIndex
from .queries import (
    find_class,
    find_function,
    get_dependencies,
    get_stats,
    get_symbol_context,
    trace_calls,
)
from .repo_map import (
    DEFAULT_EXCLUDE_DIRS,
    SUPPORTED_EXTENSIONS,
    RepoMapEngine,
    Symbol,
    tree_sitter_available,
)

__all__ = [
    "CodebaseMapper",
    "GortexCodebaseMapper",
    "NativeGraphMapper",
    "GCX1Compressor",
    "GCX1ExtremeCompressor",
    "PersistentGraphIndex",
    "Symbol",
    "RepoMapEngine",
    "DEFAULT_EXCLUDE_DIRS",
    "SUPPORTED_EXTENSIONS",
    "tree_sitter_available",
    "graphify_available",
    "gortex_available",
]


class CodebaseMapper:
    """Map a repository into structured graphs for fast LLM retrieval.

    Two layers:

    1. **Repo Map** (tree-sitter + ranking) — overview of what exists, ranked
       by importance.
    2. **Code Graph** (graphify, with tree-sitter fallback) — nodes/edges for
       surgical code retrieval.

    Parameters mirror :class:`~tokenseive.mapper.repo_map.RepoMapEngine`.
    """

    def __init__(
        self,
        repo_path,
        *,
        exclude_dirs: Optional[Set[str]] = None,
        include_globs: Optional[List[str]] = None,
        extensions: Optional[Set[str]] = None,
        max_files: Optional[int] = None,
        encoding: Optional[str] = "o200k_base",
        verbose: bool = False,
    ) -> None:
        self._engine = RepoMapEngine(
            repo_path,
            exclude_dirs=exclude_dirs,
            include_globs=include_globs,
            extensions=extensions,
            max_files=max_files,
            encoding=encoding,
            verbose=verbose,
        )

    # ---- passthrough state ------------------------------------------- #
    @property
    def engine(self) -> RepoMapEngine:
        """The underlying :class:`RepoMapEngine` (advanced use)."""
        return self._engine

    @property
    def repo_path(self):
        return self._engine.repo_path

    @property
    def files(self) -> List:
        return self._engine.files

    @property
    def symbols(self) -> List[Symbol]:
        return self._engine.symbols

    def count_tokens(self, text: str) -> int:
        return self._engine.count_tokens(text)

    # ---- Layer 1: repo map ------------------------------------------- #
    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted, ranked symbol map."""
        return self._engine.get_repo_map(max_tokens=max_tokens)

    # ---- Layer 2: code graph ----------------------------------------- #
    def get_code_graph(self, *, max_graph_files: int = 250) -> Dict[str, Any]:
        """Build a structured code graph (graphify or tree-sitter fallback)."""
        return build_code_graph(self._engine, max_graph_files=max_graph_files)

    def export_graph(
        self, format: str = "json", output_path: Optional[str] = None
    ) -> str:
        """Export the code graph as JSON, HTML, or SVG."""
        return export_graph(self._engine, format=format, output_path=output_path)

    # ---- Surgical queries -------------------------------------------- #
    def find_function(self, name: str) -> List[Dict]:
        """Find all functions/methods matching *name*."""
        return find_function(self._engine, name)

    def find_class(self, name: str) -> List[Dict]:
        """Find all classes matching *name*."""
        return find_class(self._engine, name)

    def trace_call_chain(self, function_name: str, max_depth: int = 3) -> Dict:
        """Trace what *function_name* calls and what calls it (depth-limited)."""
        return trace_calls(self._engine, function_name, max_depth=max_depth)

    # Backwards-compatible alias.
    trace_calls = trace_call_chain

    def get_symbol_context(self, symbol_name: str) -> str:
        """Compact context block: definition + immediate callers/callees."""
        return get_symbol_context(self._engine, symbol_name)

    def get_context(self, symbol_name: str) -> str:
        """Alias for :meth:`get_symbol_context`."""
        return get_symbol_context(self._engine, symbol_name)

    def get_dependencies(self, file_path: str) -> Dict:
        """Get imports of a file and the files that depend on it."""
        return get_dependencies(self._engine, file_path)

    def get_stats(self) -> Dict:
        """Return mapping statistics."""
        return get_stats(self._engine)
