"""Zero-dependency persistent code graph engine — pure Python reimplementation of Gortex.

This module provides a complete graph-native code intelligence system without any
external dependencies. It indexes Python code into a persistent knowledge graph
that can be queried across sessions, supporting symbol search, call chains, and
relationship tracking.

Key features:
- **Zero external dependencies** — pure Python, no subprocess calls
- **Persistent indexing** — JSON-based cross-session caching
- **AST-based parsing** — accurate symbol extraction for Python code
- **Graph queries** — symbol search, call chains, relationship tracking
- **Incremental updates** — only reindex changed files

Compatible with the existing CodebaseMapper API for drop-in replacement.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from collections import defaultdict

logger = logging.getLogger("tokenseive.mapper.graph_engine")


# --------------------------------------------------------------------------- #
# Data Structures
# --------------------------------------------------------------------------- #

@dataclass
class Symbol:
    """Represents a code symbol (function, class, method, variable)."""

    id: str  # Unique identifier: file_path:line:kind:name
    name: str
    kind: str  # "function", "class", "method", "variable"
    file_path: str
    line: int
    end_line: int
    signature: str
    docstring: str = ""
    parent_id: str = ""  # For methods, the containing class

    # Relationships
    calls: List[str] = field(default_factory=list)  # symbol_ids this calls
    called_by: List[str] = field(default_factory=list)  # symbol_ids that call this
    imports: List[str] = field(default_factory=list)  # modules/files this imports
    imported_by: List[str] = field(default_factory=list)  # symbol_ids that import this


@dataclass
class FileIndex:
    """Metadata and symbols for a single source file."""

    path: str
    hash: str  # Content hash for change detection
    last_modified: float  # Unix timestamp
    symbols: List[Symbol] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    language: str = "python"


@dataclass
class GraphIndex:
    """Complete code graph for a repository."""

    repo_path: str
    symbols: Dict[str, Symbol] = field(default_factory=dict)
    files: Dict[str, FileIndex] = field(default_factory=dict)

    # Query indexes for fast lookup
    symbols_by_name: Dict[str, List[str]] = field(default_factory=dict)
    symbols_by_kind: Dict[str, List[str]] = field(default_factory=dict)
    symbols_by_file: Dict[str, List[str]] = field(default_factory=dict)

    # Metadata
    last_indexed: float = field(default_factory=time.time)
    total_symbols: int = 0
    total_files: int = 0

    def add_symbol(self, symbol: Symbol) -> None:
        """Add a symbol to the graph and update query indexes."""
        self.symbols[symbol.id] = symbol

        # Update query indexes
        if symbol.name not in self.symbols_by_name:
            self.symbols_by_name[symbol.name] = []
        self.symbols_by_name[symbol.name].append(symbol.id)

        if symbol.kind not in self.symbols_by_kind:
            self.symbols_by_kind[symbol.kind] = []
        self.symbols_by_kind[symbol.kind].append(symbol.id)

        if symbol.file_path not in self.symbols_by_file:
            self.symbols_by_file[symbol.file_path] = []
        self.symbols_by_file[symbol.file_path].append(symbol.id)

        self.total_symbols = len(self.symbols)


# --------------------------------------------------------------------------- #
# Python AST Parser
# --------------------------------------------------------------------------- #

class PythonSymbolExtractor(ast.NodeVisitor):
    """Extract symbols and relationships from Python AST."""

    def __init__(self, file_path: str, source: str):
        self.file_path = file_path
        self.source = source
        self.symbols: List[Symbol] = []
        self.imports: List[str] = []
        self.current_class: Optional[str] = None
        self.current_class_id: Optional[str] = None
        self.symbol_counter = 0

    def generate_symbol_id(self, name: str, kind: str, line: int) -> str:
        """Generate a unique symbol identifier."""
        self.symbol_counter += 1
        return f"{self.file_path}:{line}:{kind}:{name}"

    def extract_docstring(self, node) -> str:
        """Extract docstring from a node if present."""
        if (node.body and
            isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, ast.Constant) and
            isinstance(node.body[0].value.value, str)):
            return node.body[0].value.value
        return ""

    def extract_signature(self, node) -> str:
        """Extract function/method signature as text."""
        try:
            # Reconstruct signature from AST
            if hasattr(node, 'returns') and node.returns:
                returns = ast.unparse(node.returns)
            else:
                returns = "None"

            args = []
            for arg in node.args.args:
                annotation = ast.unparse(arg.annotation) if arg.annotation else "Any"
                args.append(f"{arg.arg}: {annotation}")

            arg_str = ", ".join(args)
            return f"{node.name}({arg_str}) -> {returns}"
        except Exception:
            # Fallback to simple signature
            args = [arg.arg for arg in getattr(node, 'args', [])]
            return f"{node.name}({', '.join(args)})"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Extract function/method symbol."""
        # Determine if this is a method or function
        if self.current_class:
            kind = "method"
            parent_id = self.current_class_id
            qualified_name = f"{self.current_class}.{node.name}"
        else:
            kind = "function"
            parent_id = ""
            qualified_name = node.name

        symbol_id = self.generate_symbol_id(qualified_name, kind, node.lineno)

        symbol = Symbol(
            id=symbol_id,
            name=qualified_name,
            kind=kind,
            file_path=self.file_path,
            line=node.lineno,
            end_line=getattr(node, 'end_lineno', node.lineno),
            signature=self.extract_signature(node),
            docstring=self.extract_docstring(node),
            parent_id=parent_id or "",
            calls=self._extract_calls(node),
            imports=[]
        )

        self.symbols.append(symbol)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Extract async function/method symbol."""
        # Treat same as function for indexing purposes
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Extract class symbol."""
        symbol_id = self.generate_symbol_id(node.name, "class", node.lineno)

        # Save current class context
        old_class = self.current_class
        old_class_id = self.current_class_id

        self.current_class = node.name
        self.current_class_id = symbol_id

        symbol = Symbol(
            id=symbol_id,
            name=node.name,
            kind="class",
            file_path=self.file_path,
            line=node.lineno,
            end_line=getattr(node, 'end_lineno', node.lineno),
            signature=f"class {node.name}",
            docstring=self.extract_docstring(node),
            parent_id="",
            calls=[],
            imports=[]
        )

        self.symbols.append(symbol)

        # Visit class body (methods, etc.)
        self.generic_visit(node)

        # Restore context
        self.current_class = old_class
        self.current_class_id = old_class_id

    def visit_Import(self, node: ast.Import) -> None:
        """Extract import statements."""
        for alias in node.names:
            module_name = alias.name
            self.imports.append(module_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Extract from...import statements."""
        if node.module:
            for alias in node.names:
                imported_name = f"{node.module}.{alias.name}"
                self.imports.append(imported_name)

    def _extract_calls(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
        """Extract function calls from a function body."""
        calls = []

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                # Try to extract the function name being called
                if isinstance(child.func, ast.Name):
                    calls.append(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    # Handle method calls like obj.method()
                    try:
                        calls.append(ast.unparse(child.func))
                    except Exception:
                        pass

        return calls


# --------------------------------------------------------------------------- #
# Graph Engine Core
# --------------------------------------------------------------------------- #

class PersistentGraphIndex:
    """Zero-dependency persistent code graph engine.

    This class provides Gortex-like functionality without any external dependencies:
    - Parses Python code using built-in AST
    - Builds persistent symbol and relationship graphs
    - Stores index as JSON for cross-session caching
    - Supports graph queries (search, call chains, relationships)

    Example
    -------
    >>> graph = PersistentGraphIndex("/path/to/repo")      # doctest: +SKIP
    >>> graph.index_repository()                            # doctest: +SKIP
    >>> results = graph.search_symbols("compress")         # doctest: +SKIP
    >>> chain = graph.get_call_chain("my_function")         # doctest: +SKIP
    """

    def __init__(
        self,
        repo_path: Union[str, Path],
        cache_path: Optional[Union[str, Path]] = None,
        verbose: bool = False
    ):
        self.repo_path = Path(repo_path).resolve()
        self.verbose = verbose

        # Cache file path (default: .tokenseive_graph.json in repo root)
        if cache_path is None:
            self.cache_path = self.repo_path / ".tokenseive_graph.json"
        else:
            self.cache_path = Path(cache_path)

        # Initialize empty graph
        self.graph = GraphIndex(repo_path=str(self.repo_path))

        # Supported file extensions
        self.supported_extensions = {".py"}

        # Directories to exclude
        self.exclude_dirs = {
            "__pycache__", ".git", ".venv", "venv", "env",
            "node_modules", ".pytest_cache", ".tox", "dist", "build"
        }

    # ----------------------------------------------------------------------- #
    # Persistence
    # ----------------------------------------------------------------------- #

    def load(self) -> bool:
        """Load cached index from disk if available.

        Returns
        -------
        bool
            True if cache was loaded successfully, False otherwise.
        """
        if not self.cache_path.exists():
            if self.verbose:
                logger.info(f"No cache found at {self.cache_path}")
            return False

        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Reconstruct GraphIndex from JSON
            self.graph = GraphIndex(
                repo_path=data['repo_path'],
                symbols={k: Symbol(**v) for k, v in data['symbols'].items()},
                files={k: FileIndex(**v) for k, v in data['files'].items()},
                symbols_by_name=data['symbols_by_name'],
                symbols_by_kind=data['symbols_by_kind'],
                symbols_by_file=data['symbols_by_file'],
                last_indexed=data['last_indexed'],
                total_symbols=data['total_symbols'],
                total_files=data['total_files']
            )

            if self.verbose:
                logger.info(f"Loaded cache with {self.graph.total_symbols} symbols "
                          f"from {self.graph.total_files} files")
            return True

        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return False

    def save(self) -> bool:
        """Save current index to disk.

        Returns
        -------
        bool
            True if save was successful, False otherwise.
        """
        try:
            # Convert dataclasses to dicts for JSON serialization
            data = {
                'repo_path': self.graph.repo_path,
                'symbols': {k: asdict(v) for k, v in self.graph.symbols.items()},
                'files': {k: asdict(v) for k, v in self.graph.files.items()},
                'symbols_by_name': self.graph.symbols_by_name,
                'symbols_by_kind': self.graph.symbols_by_kind,
                'symbols_by_file': self.graph.symbols_by_file,
                'last_indexed': self.graph.last_indexed,
                'total_symbols': self.graph.total_symbols,
                'total_files': self.graph.total_files
            }

            # Ensure directory exists
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Write to file
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            if self.verbose:
                logger.info(f"Saved cache with {self.graph.total_symbols} symbols "
                          f"to {self.cache_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
            return False

    # ----------------------------------------------------------------------- #
    # Indexing
    # ----------------------------------------------------------------------- #

    def index_repository(
        self,
        force_reindex: bool = False,
        incremental: bool = True
    ) -> GraphIndex:
        """Index the repository and build the knowledge graph.

        Parameters
        ----------
        force_reindex : bool
            If True, rebuild index from scratch even if cache exists.
        incremental : bool
            If True, only reindex changed files.

        Returns
        -------
        GraphIndex
            The built graph index.
        """
        # Try to load from cache unless forced reindex
        if not force_reindex and self.load():
            if incremental:
                # Update only changed files
                self._update_incremental()
            return self.graph

        # Full reindex
        if self.verbose:
            logger.info(f"Indexing repository: {self.repo_path}")

        # Clear existing index
        self.graph = GraphIndex(repo_path=str(self.repo_path))

        # Find all Python files
        python_files = self._find_python_files()

        if self.verbose:
            logger.info(f"Found {len(python_files)} Python files")

        # Index each file
        for file_path in python_files:
            try:
                self._index_file(file_path)
            except Exception as e:
                logger.warning(f"Failed to index {file_path}: {e}")

        # Build call relationships
        self._build_call_relationships()

        # Save to cache
        self.save()

        if self.verbose:
            logger.info(f"Indexing complete: {self.graph.total_symbols} symbols, "
                       f"{self.graph.total_files} files")

        return self.graph

    def _find_python_files(self) -> List[Path]:
        """Find all Python files in the repository."""
        python_files = []

        for root, dirs, files in os.walk(self.repo_path):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]

            for file in files:
                if file.endswith('.py'):
                    file_path = Path(root) / file
                    python_files.append(file_path)

        return python_files

    def _index_file(self, file_path: Path) -> None:
        """Index a single Python file."""
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()

            # Calculate file hash
            file_hash = hashlib.md5(source.encode()).hexdigest()

            # Parse with AST
            tree = ast.parse(source)

            # Extract symbols
            extractor = PythonSymbolExtractor(str(file_path), source)
            extractor.visit(tree)

            # Create file index
            file_index = FileIndex(
                path=str(file_path),
                hash=file_hash,
                last_modified=os.path.getmtime(file_path),
                symbols=extractor.symbols,
                imports=extractor.imports,
                language="python"
            )

            # Add to graph
            self.graph.files[str(file_path)] = file_index
            self.graph.total_files = len(self.graph.files)

            # Add symbols to graph
            for symbol in extractor.symbols:
                self.graph.add_symbol(symbol)

        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
        except Exception as e:
            logger.warning(f"Failed to index {file_path}: {e}")

    def _update_incremental(self) -> None:
        """Update index incrementally by reindexing only changed files."""
        if self.verbose:
            logger.info("Performing incremental update")

        # Find all Python files
        python_files = self._find_python_files()
        tracked_files = set(self.graph.files.keys())

        # Remove files that no longer exist
        for file_path in tracked_files - {str(f) for f in python_files}:
            if self.verbose:
                logger.info(f"Removing deleted file: {file_path}")
            del self.graph.files[file_path]

        # Reindex changed or new files
        for file_path in python_files:
            file_str = str(file_path)
            current_mtime = os.path.getmtime(file_path)

            # Check if file needs reindexing
            if (file_str not in self.graph.files or
                self.graph.files[file_str].last_modified < current_mtime):
                if self.verbose:
                    logger.info(f"Reindexing: {file_path}")
                self._index_file(file_path)

        # Save updated cache
        self.save()

    def _build_call_relationships(self) -> None:
        """Build caller/callee relationships between symbols."""
        # Reset relationships
        for symbol in self.graph.symbols.values():
            symbol.calls = []
            symbol.called_by = []

        # Build relationships
        for symbol in self.graph.symbols.values():
            for call_name in symbol.calls:
                # Find symbols that match this call name
                matching_ids = self.graph.symbols_by_name.get(call_name, [])
                for target_id in matching_ids:
                    if target_id in self.graph.symbols:
                        symbol.calls.append(target_id)
                        self.graph.symbols[target_id].called_by.append(symbol.id)

    # ----------------------------------------------------------------------- #
    # Query API
    # ----------------------------------------------------------------------- #

    def search_symbols(
        self,
        query: str,
        kinds: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Search for symbols by name.

        Parameters
        ----------
        query : str
            Symbol name to search for (supports partial matching).
        kinds : list of str, optional
            Filter by symbol kinds (e.g., ["function", "class"]).
        limit : int
            Maximum number of results to return.

        Returns
        -------
        list of dict
            Matching symbols with metadata.
        """
        results = []

        # Find symbols matching the query
        query_lower = query.lower()
        for symbol_name, symbol_ids in self.graph.symbols_by_name.items():
            if query_lower in symbol_name.lower():
                for symbol_id in symbol_ids:
                    symbol = self.graph.symbols.get(symbol_id)
                    if symbol is None:
                        continue

                    # Filter by kind if specified
                    if kinds and symbol.kind not in kinds:
                        continue

                    results.append({
                        'id': symbol.id,
                        'name': symbol.name,
                        'kind': symbol.kind,
                        'file': symbol.file_path,
                        'line': symbol.line,
                        'end_line': symbol.end_line,
                        'signature': symbol.signature,
                        'docstring': symbol.docstring
                    })

                    if len(results) >= limit:
                        break

            if len(results) >= limit:
                break

        return results

    def get_symbol(self, symbol_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific symbol.

        Parameters
        ----------
        symbol_id : str
            Unique symbol identifier.

        Returns
        -------
        dict or None
            Symbol details if found, None otherwise.
        """
        symbol = self.graph.symbols.get(symbol_id)
        if symbol is None:
            return None

        return {
            'id': symbol.id,
            'name': symbol.name,
            'kind': symbol.kind,
            'file': symbol.file_path,
            'line': symbol.line,
            'end_line': symbol.end_line,
            'signature': symbol.signature,
            'docstring': symbol.docstring,
            'parent_id': symbol.parent_id,
            'calls': symbol.calls,
            'called_by': symbol.called_by,
            'imports': symbol.imports
        }

    def get_call_chain(
        self,
        symbol_id: str,
        direction: str = "outbound",
        max_depth: int = 3
    ) -> List[Dict[str, Any]]:
        """Trace inbound or outbound call relationships.

        Parameters
        ----------
        symbol_id : str
            Starting symbol identifier.
        direction : str
            "outbound" (what this calls) or "inbound" (what calls this).
        max_depth : int
            Maximum traversal depth.

        Returns
        -------
        list of dict
            Call chain as a hierarchical structure.
        """
        symbol = self.graph.symbols.get(symbol_id)
        if symbol is None:
            return []

        chain = []
        visited = set()

        def traverse(current_id: str, depth: int) -> None:
            if depth > max_depth or current_id in visited:
                return

            visited.add(current_id)
            current_symbol = self.graph.symbols.get(current_id)
            if current_symbol is None:
                return

            # Get relationships based on direction
            if direction == "outbound":
                related_ids = current_symbol.calls
                rel_type = "calls"
            else:
                related_ids = current_symbol.called_by
                rel_type = "called_by"

            # Build chain entry
            entry = {
                'id': current_id,
                'name': current_symbol.name,
                'kind': current_symbol.kind,
                'file': current_symbol.file_path,
                'line': current_symbol.line,
                'depth': depth,
                rel_type: []
            }

            # Traverse children
            for related_id in related_ids:
                if related_id not in visited:
                    entry[rel_type].append(related_id)
                    traverse(related_id, depth + 1)

            chain.append(entry)

        traverse(symbol_id, 0)
        return chain

    def get_file_dependencies(self, file_path: str) -> Dict[str, List[str]]:
        """Get imports and dependents for a file.

        Parameters
        ----------
        file_path : str
            Path to the file.

        Returns
        -------
        dict
            Dictionary with 'imports' and 'dependents' keys.
        """
        file_index = self.graph.files.get(file_path)
        if file_index is None:
            return {'imports': [], 'dependents': []}

        # Find files that import this file
        dependents = []
        for other_file_path, other_index in self.graph.files.items():
            if file_path in other_index.imports:
                dependents.append(other_file_path)

        return {
            'imports': file_index.imports,
            'dependents': dependents
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics.

        Returns
        -------
        dict
            Statistics about the graph index.
        """
        kind_counts = defaultdict(int)
        for symbol in self.graph.symbols.values():
            kind_counts[symbol.kind] += 1

        return {
            'repo_path': str(self.repo_path),
            'total_symbols': self.graph.total_symbols,
            'total_files': self.graph.total_files,
            'symbols_by_kind': dict(kind_counts),
            'last_indexed': self.graph.last_indexed,
            'cache_path': str(self.cache_path),
            'backend': 'native_graph'
        }

    def get_total_symbols(self) -> int:
        """Get total number of symbols in the index.

        Returns
        -------
        int
            Total symbol count.
        """
        return self.graph.total_symbols

    def get_indexed_files(self) -> List[str]:
        """Get list of indexed file paths.

        Returns
        -------
        list of str
            File paths that are indexed.
        """
        return list(self.graph.files.keys())

    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted repository overview.

        Parameters
        ----------
        max_tokens : int
            Maximum tokens for the overview.

        Returns
        -------
        str
            Ranked symbol map as text.
        """
        # Simple implementation: list symbols by importance
        # (could be enhanced with ranking algorithms)

        lines = [f"# Repository Map: {self.repo_path.name}\n"]
        lines.append(f"Total: {self.graph.total_symbols} symbols in {self.graph.total_files} files\n\n")

        # Group by kind
        by_kind = defaultdict(list)
        for symbol in self.graph.symbols.values():
            by_kind[symbol.kind].append(symbol)

        # Output by kind
        for kind in ["class", "function", "method"]:
            if kind not in by_kind:
                continue

            lines.append(f"## {kind.capitalize()}s\n")
            for symbol in sorted(by_kind[kind], key=lambda s: s.name)[:20]:
                lines.append(
                    f"- {symbol.name} ({Path(symbol.file_path).relative_to(self.repo_path)}:{symbol.line})"
                )
            lines.append("")

        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Convenience Functions
# --------------------------------------------------------------------------- #

def build_graph_index(
    repo_path: Union[str, Path],
    force_reindex: bool = False,
    verbose: bool = False
) -> PersistentGraphIndex:
    """Convenience function to build a graph index.

    Parameters
    ----------
    repo_path : str or Path
        Path to the repository.
    force_reindex : bool
            Force rebuild from scratch.
    verbose : bool
            Enable verbose logging.

    Returns
    -------
    PersistentGraphIndex
        The built graph index.
    """
    graph = PersistentGraphIndex(repo_path, verbose=verbose)
    graph.index_repository(force_reindex=force_reindex)
    return graph
