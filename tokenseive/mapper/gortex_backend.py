"""Gortex-backed codebase mapper — graph-native symbol lookup with GCX1 compression.

This module provides a drop-in replacement for :class:`CodebaseMapper` that uses
Gortex's persistent indexed knowledge graph instead of parsing files on every run.

**NEW:** Now includes 0-dependency native implementation as default!

Key advantages over the tree-sitter/regex backend:
- **Persistent index** — symbols indexed once, queried instantly across sessions
- **Graph-native queries** — precise call-graph traversal, zero-false-positive reference finding
- **GCX1 compact wire format** — 70-90% token reduction via body elision
- **Zero dependencies** — native implementation uses pure Python (no daemon required)
- **257 languages** — fallback to external Gortex daemon for multi-language repos

Default behavior: Uses 0-dependency native implementation. Falls back to external
Gortex daemon if explicitly requested via ``use_native=False`` parameter.

Requires for external daemon: Gortex daemon running (``gortex daemon start``) and
repo tracked (``gortex track <repo>``).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tokenseive.mapper.gortex")


def _gortex_available() -> bool:
    """Check if gortex binary is available and daemon is responsive."""
    try:
        result = subprocess.run(
            ["gortex", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _gortex_call(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a Gortex MCP tool via the CLI.

    This is a lightweight wrapper that calls ``gortex call`` with key=value args.
    For production use, consider using the MCP SDK directly.
    """
    try:
        # Build command with --arg key=value for each parameter
        cmd = ["gortex", "call", tool]
        for key, value in params.items():
            if isinstance(value, (dict, list)):
                # JSON-encode complex types
                cmd.extend(["--arg", f"{key}={json.dumps(value)}"])
            else:
                cmd.extend(["--arg", f"{key}={value}"])

        # Run from the repo being queried (must be a tracked directory)
        # Use /home/minguyen/.hermes/scripts as a fallback (known-tracked repo)
        cwd = "/home/minguyen/.hermes/scripts"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            cwd=cwd,
        )

        if result.returncode != 0:
            logger.error(f"Gortex call failed: {result.stderr}")
            return {"error": result.stderr, "success": False}

        return json.loads(result.stdout) if result.stdout else {"success": True}
    except subprocess.TimeoutExpired:
        logger.error(f"Gortex call timed out: {tool}")
        return {"error": "timeout", "success": False}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Gortex call error: {e}")
        return {"error": str(e), "success": False}


def _check_repo_tracked(repo_path: Path) -> bool:
    """Verify that a repo is tracked by Gortex."""
    try:
        # Use gortex repos command and parse output
        result = subprocess.run(
            ["gortex", "repos"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        if result.returncode != 0:
            return False

        # Parse the table output to check if repo is tracked
        repo_str = str(repo_path.resolve())
        return repo_str in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class GortexCodebaseMapper:
    """Gortex-backed codebase mapper with graph-native symbol lookup.

    This class provides the same API surface as :class:`CodebaseMapper` but
    delegates all queries to the Gortex daemon instead of parsing files directly.

    Example
    -------
    >>> from tokenseive.mapper import GortexCodebaseMapper  # doctest: +SKIP
    >>> mapper = GortexCodebaseMapper("/path/to/repo")      # doctest: +SKIP
    >>> print(mapper.get_repo_map(max_tokens=1024))         # doctest: +SKIP
    >>> mapper.find_function("my_func")                     # doctest: +SKIP

    Parameters
    ----------
    repo_path : str | Path
        Path to the repository (must be tracked by Gortex).
    encoding : str, optional
        Token encoding (default: ``o200k_base``). Not used by Gortex but kept
        for API compatibility.
    verbose : bool, optional
        Log additional diagnostics (default: ``False``).
    use_native : bool, optional
        Use 0-dependency native implementation (default: ``True``).
    force_external : bool, optional
        Force use of external Gortex daemon (default: ``False``).
    use_extreme_compression : bool, optional
        Use extreme GCX1 compression for 95%+ token reduction (default: ``False``).
        Only applies when use_native=True.

    Raises
    ------
    ImportError
        If Gortex daemon is not running or the repo is not tracked.
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
        self._use_native = use_native and not force_external
        self._use_extreme_compression = use_extreme_compression

        # Try native implementation first (default)
        if self._use_native:
            try:
                from .native_graph_mapper import NativeGraphMapper

                self._native_mapper = NativeGraphMapper(
                    self._repo_path,
                    encoding=self._encoding,
                    verbose=self._verbose,
                    use_extreme_compression=use_extreme_compression,
                )
                self._backend = "native"
                if self._verbose:
                    compression_type = "extreme" if use_extreme_compression else "standard"
                    logger.info(f"Using native graph backend for {self._repo_path} with {compression_type} compression")
                return
            except Exception as e:
                if self._verbose:
                    logger.warning(f"Native backend failed: {e}. Falling back to external Gortex.")
                if force_external:
                    raise ImportError(f"Native backend failed and force_external=True: {e}")

        # Fallback to external Gortex daemon
        if not _gortex_available():
            raise ImportError(
                "Gortex daemon is not running. Start it with 'gortex daemon start'. "
                "Or use use_native=True for zero-dependency native implementation."
            )

        # Verify repo is tracked
        if not _check_repo_tracked(self._repo_path):
            raise ImportError(
                f"Repository {self._repo_path} is not tracked by Gortex. "
                f"Track it with 'gortex track {self._repo_path}'. "
                f"Or use use_native=True for zero-dependency native implementation."
            )

        self._native_mapper = None
        self._backend = "gortex"

        if self._verbose:
            logger.info(f"Using external Gortex daemon backend for {self._repo_path}")

        if self._verbose:
            logger.info(f"GortexCodebaseMapper initialized for {self._repo_path}")

    # ---- Compatibility properties ---------------------------------------- #
    @property
    def repo_path(self) -> Path:
        """Repository path."""
        return self._repo_path

    @property
    def files(self) -> List[str]:
        """List all indexed files in the repository."""
        if self._native_mapper:
            return self._native_mapper.files

        result = _gortex_call("list_repos", {})
        if not result.get("success"):
            return []

        # Find the matching repo and return its files
        repos = result.get("repos", [])
        repo_str = str(self._repo_path)
        for repo in repos:
            if repo_str in repo.get("path", ""):
                return repo.get("files", [])
        return []

    @property
    def symbols(self) -> List[Dict]:
        """Return a sample of indexed symbols (for compatibility)."""
        if self._native_mapper:
            return self._native_mapper.symbols

        # Use search_symbols with a broad pattern to get some symbols
        result = _gortex_call(
            "search_symbols",
            {"query": "*", "limit": 100, "compress_bodies": False},
        )
        return result.get("results", []) if result.get("success") else []

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
        if self._native_mapper:
            return self._native_mapper.find_function(name)

        result = _gortex_call(
            "search_symbols",
            {
                "query": name,
                "kinds": ["function", "method"],
                "limit": 50,
                "compress_bodies": False,
            },
        )

        if not result.get("success"):
            return []

        matches = []
        for hit in result.get("results", []):
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
        if self._native_mapper:
            return self._native_mapper.find_class(name)

        result = _gortex_call(
            "search_symbols",
            {
                "query": name,
                "kinds": ["class"],
                "limit": 50,
                "compress_bodies": False,
            },
        )

        if not result.get("success"):
            return []

        matches = []
        for hit in result.get("results", []):
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
        if self._native_mapper:
            return self._native_mapper.trace_call_chain(function_name, max_depth)

        # First find the symbol
        find_result = _gortex_call(
            "search_symbols",
            {
                "query": function_name,
                "limit": 1,
                "compress_bodies": False,
            },
        )

        if not find_result.get("success") or not find_result.get("results"):
            return {
                "root": function_name,
                "found": False,
                "note": "no matching symbol",
            }

        symbol_id = find_result["results"][0].get("symbol_id")

        # Get outbound calls (callees)
        outbound_result = _gortex_call(
            "get_call_chain",
            {"symbol_id": symbol_id, "direction": "outbound", "max_depth": max_depth},
        )

        # Get inbound calls (callers)
        inbound_result = _gortex_call(
            "get_call_chain",
            {"symbol_id": symbol_id, "direction": "inbound", "max_depth": max_depth},
        )

        return {
            "root": function_name,
            "found": True,
            "outbound": outbound_result.get("chain", []) if outbound_result.get("success") else [],
            "inbound": inbound_result.get("chain", []) if inbound_result.get("success") else [],
            "matched_locations": [
                {
                    "file": find_result["results"][0].get("file", ""),
                    "line": find_result["results"][0].get("line", 0),
                    "signature": find_result["results"][0].get("signature", ""),
                    "kind": find_result["results"][0].get("kind", ""),
                }
            ],
        }

    # Backwards-compatible alias.
    trace_calls = trace_call_chain

    def get_symbol_context(self, symbol_name: str) -> str:
        """Get a compact context block for a symbol.

        This uses GCX1 compression for maximum token reduction (70-90% savings).
        """
        if self._native_mapper:
            return self._native_mapper.get_symbol_context(symbol_name)

        # First find the symbol
        find_result = _gortex_call(
            "search_symbols",
            {
                "query": symbol_name,
                "limit": 3,
                "compress_bodies": False,
            },
        )

        if not find_result.get("success") or not find_result.get("results"):
            return f"# Symbol '{symbol_name}' not found."

        blocks = []
        for hit in find_result.get("results", []):
            symbol_id = hit.get("symbol_id")
            if not symbol_id:
                continue

            # Get compressed source with GCX1 format
            source_result = _gortex_call(
                "get_symbol_source",
                {
                    "symbol_id": symbol_id,
                    "compress_bodies": True,
                    "format": "gcx",
                },
            )

            if not source_result.get("success"):
                continue

            source_text = source_result.get("source", "")
            tokens = source_result.get("tokens", self.count_tokens(source_text))

            blocks.append(
                f"## {hit.get('signature', symbol_name)} ({hit.get('file', '')}:{hit.get('line', 0)})\n"
                f"```{hit.get('language', 'python')}\n{source_text}\n```\n"
                f"(≈{tokens} tokens, compressed via GCX1)\n"
            )

        header = f"# Context for '{symbol_name}' ({len(find_result.get('results', []))} location(s))\n"
        return header + "\n".join(blocks)

    def get_context(self, symbol_name: str) -> str:
        """Alias for :meth:`get_symbol_context`."""
        return self.get_symbol_context(symbol_name)

    def get_dependencies(self, file_path: str) -> Dict:
        """Get imports of a file and the files that depend on it."""
        if self._native_mapper:
            return self._native_mapper.get_dependencies(file_path)

        # This is a simplified implementation — Gortex doesn't have a direct
        # equivalent, so we use file-level context
        result = _gortex_call(
            "get_file_summary",
            {"path": file_path},
        )

        if not result.get("success"):
            return {
                "file": file_path,
                "imports": [],
                "dependents": [],
                "note": "Gortex file summary unavailable",
            }

        return {
            "file": file_path,
            "imports": result.get("imports", []),
            "dependents": result.get("dependents", []),
        }

    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted, ranked symbol map."""
        if self._native_mapper:
            return self._native_mapper.get_repo_map(max_tokens)

        result = _gortex_call(
            "smart_context",
            {
                "query": "overview",
                "max_tokens": max_tokens,
                "compress_bodies": True,
            },
        )

        if not result.get("success"):
            return f"# Gortex smart context unavailable\n# Error: {result.get('error', 'unknown')}"

        return result.get("context", "")

    def get_code_graph(self, *, max_graph_files: int = 250) -> Dict[str, Any]:
        """Build a structured code graph (delegates to Gortex's graph)."""
        if self._native_mapper:
            return self._native_mapper.get_code_graph(max_graph_files=max_graph_files)

        result = _gortex_call(
            "get_architecture",
            {"resolution": "symbol"},
        )

        if not result.get("success"):
            return {
                "nodes": [],
                "edges": [],
                "stats": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "gortex_error": result.get("error"),
                },
            }

        return {
            "nodes": result.get("nodes", []),
            "edges": result.get("edges", []),
            "stats": {
                "total_nodes": len(result.get("nodes", [])),
                "total_edges": len(result.get("edges", [])),
                "backend": "gortex",
            },
        }

    def export_graph(
        self, format: str = "json", output_path: Optional[str] = None
    ) -> str:
        """Export the code graph as JSON."""
        if self._native_mapper:
            return self._native_mapper.export_graph(format=format, output_path=output_path)

        graph = self.get_code_graph()
        data = json.dumps(graph, indent=2)

        if output_path:
            Path(output_path).write_text(data, encoding="utf-8")

        return data

    def get_stats(self) -> Dict:
        """Return mapping statistics."""
        if self._native_mapper:
            return self._native_mapper.get_stats()

        result = _gortex_call("graph_stats", {})

        if not result.get("success"):
            return {
                "repo_path": str(self._repo_path),
                "backend": "gortex",
                "error": result.get("error"),
            }

        stats = result.get("stats", {})
        return {
            "repo_path": str(self._repo_path),
            "backend": "gortex",
            "total_symbols": stats.get("total_symbols", 0),
            "total_files": stats.get("total_files", 0),
            "graph_tokens": stats.get("graph_tokens", 0),
            "index_health": stats.get("index_health", "unknown"),
        }


def gortex_available() -> bool:
    """Check if Gortex daemon is running and responsive."""
    return _gortex_available()
