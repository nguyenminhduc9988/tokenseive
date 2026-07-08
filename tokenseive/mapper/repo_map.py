"""Codebase repository mapping — tree-sitter (optional) with regex fallback.

Indexes a source tree into ranked :class:`Symbol` objects and renders a
token-budgeted "repo map" suitable for LLM context. This is Layer 1 of the
mapper: it answers *what exists, ranked by importance*.

Zero-dependency by default: when ``tree-sitter`` is not installed, parsing
falls back to a regex extractor that still finds functions/classes/methods
(and call edges for Python). Install ``tokenseive[mapper]`` for full
tree-sitter parsing across 20 languages.

Generalised from the original Hermes ``codebase_mapper.py``: all
framework-specific paths/config were removed.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import count_tokens, get_encoding

logger = logging.getLogger("tokenseive.mapper")

# --------------------------------------------------------------------------- #
# Optional dependency: tree-sitter (loaded lazily, degrades to regex)
# --------------------------------------------------------------------------- #
_TS_GET_PARSER = None
try:  # pragma: no cover - import-time behaviour depends on environment
    from tree_sitter_language_pack import get_parser as _ts_get_parser  # type: ignore
except Exception:  # pragma: no cover
    _ts_get_parser = None


def tree_sitter_available() -> bool:
    """Return ``True`` if ``tree_sitter_language_pack`` is importable."""
    return _ts_get_parser is not None


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DEFAULT_EXCLUDE_DIRS: Set[str] = {
    "node_modules", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", "__pycache__", ".git", ".hg", ".svn", "build", "dist",
    "target", "site-packages", ".eggs", "htmlcov", "coverage", ".next",
    ".nuxt",
}

SUPPORTED_EXTENSIONS: Dict[str, str] = {
    "py": "python", "js": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "go": "go", "rs": "rust",
    "rb": "ruby", "java": "java", "c": "c", "cpp": "cpp", "cc": "cpp",
    "h": "c", "hpp": "cpp", "php": "php", "scala": "scala",
    "swift": "swift", "kt": "kotlin", "lua": "lua",
}

FUNC_KEYWORDS = {"def", "function", "func", "fn", "fun"}
MAX_FILE_BYTES = 1_500_000  # skip huge generated files

# Language noise that should NOT dominate importance ranking.
_NOISE_NAMES = {
    "self", "cls", "return", "import", "from", "class", "def", "if", "else",
    "elif", "for", "while", "try", "except", "finally", "with", "as", "in",
    "is", "not", "and", "or", "None", "True", "False", "lambda", "yield",
    "global", "nonlocal", "pass", "break", "continue", "raise", "assert",
    "del", "async", "await", "print",
    "get", "set", "name", "value", "data", "item", "items", "key", "keys",
    "type", "id", "len", "list", "dict", "str", "int", "float", "bool",
    "bytes", "range", "open", "input", "format", "map", "filter", "sorted",
    "reversed", "sum", "min", "max", "abs", "round", "any", "all", "zip",
    "enumerate", "super", "property", "staticmethod", "classmethod", "object",
    "Exception", "run", "start", "stop", "init", "main", "handle", "process",
    "update", "add", "remove", "create", "delete", "close", "read", "write",
    "load", "save", "send", "receive", "info", "warning", "error", "debug",
    "args", "kwargs", "options", "config", "context", "result", "results",
    "message", "messages", "request", "response", "status", "count", "index",
    "path", "file", "files", "dir", "url", "token", "tokens", "text",
    "content", "body", "header", "headers", "param", "params", "attr",
}


# --------------------------------------------------------------------------- #
# Data container
# --------------------------------------------------------------------------- #
@dataclass
class Symbol:
    """A code definition extracted from a source file."""

    name: str
    kind: str  # function | class | method
    file: str  # repo-relative path
    line: int  # 1-based
    end_line: int
    signature: str
    class_context: Optional[str] = None
    node_id: str = ""
    refs: int = 0  # reference/breadth count (populated during ranking)
    calls: List[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        """``Class.method`` for methods, else the bare name."""
        return f"{self.class_context}.{self.name}" if self.class_context else self.name


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class RepoMapEngine:
    """Indexes a repository into ranked symbols and renders a repo map.

    Parameters
    ----------
    repo_path:
        Path to the repository to map.
    exclude_dirs:
        Directory basenames to skip (defaults to :data:`DEFAULT_EXCLUDE_DIRS`).
    include_globs:
        Optional list of ``fnmatch`` globs; only matching files are indexed.
    extensions:
        Set of extensions (without dot) to include. Defaults to all supported.
    max_files:
        Cap on the number of files indexed.
    encoding:
        tiktoken encoding name for token budgeting, or ``None`` for the
        heuristic estimator.
    verbose:
        Emit progress logging.
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
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(
                f"Repository path does not exist: {self.repo_path}"
            )
        self.exclude_dirs = exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
        self.include_globs = include_globs or []
        self.extensions = extensions if extensions is not None else set(SUPPORTED_EXTENSIONS)
        self.max_files = max_files
        self.verbose = verbose
        self._encoding = get_encoding(encoding) if encoding else None

        # State
        self.files: List[Path] = []
        self.symbols: List[Symbol] = []
        self._symbols_by_name: Dict[str, List[Symbol]] = defaultdict(list)
        self._word_freq: Counter = Counter()
        self._file_freq: Counter = Counter()
        self._total_loc: int = 0
        self._index_time: float = 0.0
        # Call-graph index (built lazily by queries).
        self._ci_out: Optional[Dict[str, Set[str]]] = None
        self._ci_inc: Optional[Dict[str, Set[str]]] = None

        self._index()

    # ------------------------------------------------------------------ #
    # Token counting
    # ------------------------------------------------------------------ #
    def count_tokens(self, text: str) -> int:
        return count_tokens(text, self._encoding)

    # ------------------------------------------------------------------ #
    # File collection
    # ------------------------------------------------------------------ #
    def _should_exclude(self, parts: Tuple[str, ...]) -> bool:
        return any(p in self.exclude_dirs for p in parts)

    def _collect_files(self) -> List[Path]:
        repo = self.repo_path
        out: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [
                d for d in dirnames
                if d not in self.exclude_dirs and not d.startswith(".")
            ]
            for fn in filenames:
                ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
                if ext not in self.extensions:
                    continue
                fp = Path(dirpath) / fn
                if self._should_exclude(fp.relative_to(repo).parts):
                    continue
                if self.include_globs and not any(
                    fnmatch.fnmatch(fp.name, g)
                    or fnmatch.fnmatch(str(fp.relative_to(repo)), g)
                    for g in self.include_globs
                ):
                    continue
                try:
                    if fp.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                out.append(fp)
        out.sort()
        if self.max_files:
            out = out[: self.max_files]
        return out

    # ------------------------------------------------------------------ #
    # Indexing (tree-sitter + word frequency, with regex fallback)
    # ------------------------------------------------------------------ #
    def _index(self) -> None:
        t0 = time.time()
        self.files = self._collect_files()
        if self.verbose:
            logger.info("Indexing %d source files in %s ...", len(self.files), self.repo_path)

        use_tree_sitter = _ts_get_parser is not None
        parsers: Dict[str, Any] = {}
        if use_tree_sitter:
            for ext, lang in SUPPORTED_EXTENSIONS.items():
                if ext in self.extensions:
                    try:
                        parsers[lang] = _ts_get_parser(lang)  # type: ignore[misc]
                    except Exception as e:  # pragma: no cover
                        if self.verbose:
                            logger.warning(
                                "tree-sitter parser for '%s' unavailable: %s", lang, e
                            )

        for fp in self.files:
            rel = str(fp.relative_to(self.repo_path))
            ext = fp.name.rsplit(".", 1)[-1] if "." in fp.name else ""
            try:
                src = fp.read_bytes()
            except OSError:
                continue
            try:
                text = src.decode("utf-8", errors="ignore")
            except Exception:
                text = ""
            lines = text.splitlines()
            self._total_loc += len(lines)
            words = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text)
            self._word_freq.update(words)
            self._file_freq.update(set(words))

            lang = SUPPORTED_EXTENSIONS.get(ext)
            parser = parsers.get(lang) if lang else None
            if parser is not None:
                syms = self._parse_tree_sitter(parser, src, rel)
            else:
                syms = self._parse_regex(text, rel, ext)

            for s in syms:
                self.symbols.append(s)
                self._symbols_by_name[s.name].append(s)

        # Rank symbols by breadth of usage with language noise suppressed.
        for s in self.symbols:
            if (
                s.name in _NOISE_NAMES
                or (s.name.startswith("__") and s.name.endswith("__"))
            ):
                s.refs = 0
            else:
                s.refs = self._file_freq.get(s.name, 0)

        self._index_time = time.time() - t0
        if self.verbose:
            logger.info(
                "Indexed %d files -> %d symbols in %.1fs",
                len(self.files), len(self.symbols), self._index_time,
            )

    # ---- tree-sitter extraction --------------------------------------- #
    def _parse_tree_sitter(self, parser: Any, src: bytes, rel: str) -> List[Symbol]:
        syms: List[Symbol] = []
        try:
            tree = parser.parse(src)
        except Exception as e:  # pragma: no cover
            logger.debug("tree-sitter parse failed for %s: %s", rel, e)
            return self._parse_regex(src.decode("utf-8", errors="ignore"), rel, rel.rsplit(".", 1)[-1])

        def _name_of(node: Any) -> Optional[str]:
            for ch in node.children:
                if ch.type == "identifier":
                    return src[ch.start_byte: ch.end_byte].decode("utf-8", "ignore")
            return None

        def _params_text(node: Any) -> str:
            for ch in node.children:
                if ch.type in ("parameters", "argument_list", "superclasses", "type_parameters"):
                    return src[ch.start_byte: ch.end_byte].decode("utf-8", "ignore").replace("\n", " ")
            return "()"

        def _collect_calls(node: Any, acc: List[str]) -> None:
            if node.type == "call":
                fn_node = node.child_by_field_name("function") or (
                    node.children[0] if node.children else None
                )
                if fn_node is not None:
                    callee = src[fn_node.start_byte: fn_node.end_byte].decode("utf-8", "ignore")
                    if "." in callee:
                        callee = callee.split(".")[-1]
                    callee = callee.strip()
                    if callee and re.match(r"^[A-Za-z_]\w*$", callee):
                        acc.append(callee)
            for ch in node.children:
                _collect_calls(ch, acc)

        def walk(node: Any, class_ctx: Optional[str]) -> None:
            t = node.type
            if t == "decorated_definition":
                for ch in node.children:
                    walk(ch, class_ctx)
                return
            if t == "class_definition" or (t.endswith("_declaration") and "class" in t):
                name = _name_of(node)
                if name:
                    syms.append(Symbol(
                        name=name, kind="class", file=rel,
                        line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        signature=f"class {name}{_params_text(node)}", class_context=None,
                        node_id=f"{rel}::{name}",
                    ))
                    for ch in node.children:
                        walk(ch, name)
                return
            if t in ("function_definition", "function_declaration", "method_definition", "function_item"):
                name = _name_of(node)
                if name:
                    calls: List[str] = []
                    _collect_calls(node, calls)
                    syms.append(Symbol(
                        name=name,
                        kind="method" if class_ctx else "function",
                        file=rel,
                        line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        signature=f"def {name}{_params_text(node)}",
                        class_context=class_ctx,
                        node_id=f"{rel}::{(class_ctx + '.') if class_ctx else ''}{name}",
                        calls=list(dict.fromkeys(calls)),
                    ))
                return
            for ch in node.children:
                walk(ch, class_ctx)

        walk(tree.root_node, None)
        return syms

    # ---- regex fallback extraction ------------------------------------ #
    _RE_FUNC = re.compile(
        r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)\s*\((.*?)\)\s*(?:->.*?)?\s*:",
        re.MULTILINE | re.DOTALL,
    )
    _RE_CLASS = re.compile(
        r"^[ \t]*class[ \t]+([A-Za-z_]\w*)\s*(\([^)]*\))?\s*:", re.MULTILINE
    )
    _RE_INDENT = re.compile(r"^([ \t]*)")
    # Regex-fallback call extraction (used when tree-sitter is unavailable).
    _RE_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    _CALL_NOISE: set = {
        "def", "class", "if", "elif", "while", "for", "with", "return",
        "yield", "assert", "print", "isinstance", "super", "lambda",
        "except", "global", "nonlocal", "del", "raise", "str", "int",
        "float", "bool", "list", "dict", "set", "tuple", "len", "range",
    }

    def _parse_regex(self, text: str, rel: str, ext: str) -> List[Symbol]:
        syms: List[Symbol] = []
        if ext != "py":
            for m in self._RE_FUNC.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                syms.append(Symbol(m.group(1), "function", rel, line, line, f"def {m.group(1)}({m.group(2)})"))
            return syms

        lines = text.splitlines()
        class_lines: Dict[int, str] = {}
        for m in self._RE_CLASS.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            class_lines[lineno] = m.group(1)
            syms.append(Symbol(
                m.group(1), "class", rel, lineno, lineno,
                f"class {m.group(1)}{m.group(2) or ''}",
                node_id=f"{rel}::{m.group(1)}",
            ))
        for m in self._RE_FUNC.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            raw = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            ind_m = self._RE_INDENT.match(raw)
            indent = len(ind_m.group(1)) if ind_m else 0
            ctx = None
            for cl in class_lines:
                if cl < lineno:
                    craw = lines[cl - 1] if cl - 1 < len(lines) else ""
                    cm = self._RE_INDENT.match(craw)
                    cindent = len(cm.group(1)) if cm else 0
                    if cindent < indent:
                        ctx = class_lines[cl]
                else:
                    break
            params = m.group(2).replace("\n", " ").strip()
            end_line = self._dedent_end_line(lines, lineno, indent)
            calls = self._extract_calls(
                lines[lineno - 1:end_line], skip={m.group(1)}
            )
            syms.append(Symbol(
                m.group(1),
                "method" if ctx else "function",
                rel, lineno, end_line,
                f"def {m.group(1)}({params})",
                class_context=ctx,
                node_id=f"{rel}::{(ctx + '.') if ctx else ''}{m.group(1)}",
                calls=calls,
            ))
        return syms

    def _dedent_end_line(self, lines: List[str], def_lineno: int, indent: int) -> int:
        """Return the (1-based, exclusive) end line of a function body.

        The body runs until the first subsequent non-blank line whose
        indentation is less than or equal to *indent* (a dedent / sibling),
        or EOF.
        """
        n = len(lines)
        for i in range(def_lineno, n):  # 0-based, after the def line
            raw = lines[i]
            if raw.strip() == "":
                continue
            m = self._RE_INDENT.match(raw)
            line_indent = len(m.group(1)) if m else 0
            if line_indent <= indent:
                return i  # numerically equals the exclusive 1-based bound
        return n

    def _extract_calls(self, body_lines: List[str], skip: set = frozenset()) -> List[str]:
        """Best-effort extraction of called names from Python body lines."""
        calls: List[str] = []
        for ln in body_lines:
            for name in self._RE_CALL.findall(ln):
                if name in self._CALL_NOISE or name in skip:
                    continue
                calls.append(name)
        return list(dict.fromkeys(calls))

    # ------------------------------------------------------------------ #
    # Repo map (Layer 1 output)
    # ------------------------------------------------------------------ #
    def get_repo_map(self, max_tokens: int = 1024) -> str:
        """Generate a token-budgeted, ranked symbol map of the repository."""
        by_file: Dict[str, List[Symbol]] = defaultdict(list)
        for s in self.symbols:
            by_file[s.file].append(s)
        for f in by_file:
            by_file[f].sort(key=lambda s: (-s.refs, s.line))
        file_order = sorted(by_file, key=lambda f: -max(s.refs for s in by_file[f]))

        root = self.repo_path.name or str(self.repo_path)
        out_lines: List[str] = []
        used = 0
        shown_files = 0
        shown_symbols = 0
        truncated = False

        def add(chunk: str) -> bool:
            nonlocal used
            cost = self.count_tokens(chunk)
            if used + cost > max_tokens:
                return False
            out_lines.append(chunk)
            used += cost
            return True

        for rel in file_order:
            parent = os.path.dirname(rel)
            depth = parent.count(os.sep) + 1
            file_line = "  " * depth + os.path.basename(rel) + "\n"
            if not add(file_line):
                truncated = True
                break
            shown_files += 1
            base_indent = "  " * (depth + 1)
            file_truncated = False
            for s in by_file[rel]:
                kind_tag = "  (class)" if s.kind == "class" else ""
                sym_line = f"{base_indent}{s.signature} [L{s.line}, {s.refs} refs]{kind_tag}\n"
                if not add(sym_line):
                    file_truncated = True
                    truncated = True
                    break
                shown_symbols += 1
            if file_truncated:
                break

        header = (
            f"# Repo map: {len(self.files)} files, {len(self.symbols)} symbols "
            f"(budget {max_tokens} tok; showing {shown_files} files / {shown_symbols} symbols"
            + (", truncated" if truncated else "")
            + ")\n"
        )
        return header + "".join(out_lines)

    # ------------------------------------------------------------------ #
    # Call-graph index (shared by queries)
    # ------------------------------------------------------------------ #
    def calls_index(self) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
        """Build ``name -> callees`` and ``name -> callers`` maps (cached)."""
        if self._ci_out is None:
            out: Dict[str, Set[str]] = defaultdict(set)
            inc: Dict[str, Set[str]] = defaultdict(set)
            for s in self.symbols:
                for callee in s.calls:
                    out[s.name].add(callee)
                    inc[callee].add(s.name)
            self._ci_out, self._ci_inc = out, inc
        return self._ci_out, self._ci_inc  # type: ignore[return-value]

    def match_symbols(self, name: str, kinds=None) -> List[Symbol]:
        """Find symbols by exact name, qualified name, then substring."""
        kinds_set = set(kinds) if kinds else None
        exact = self._symbols_by_name.get(name, [])
        if "." in name:
            cls, meth = name.split(".", 1)
            exact = [s for s in self.symbols if s.class_context == cls and s.name == meth]
        matches = [s for s in exact if (kinds_set is None or s.kind in kinds_set)]
        if not matches:
            low = name.lower()
            matches = [
                s for s in self.symbols
                if (kinds_set is None or s.kind in kinds_set) and low in s.name.lower()
            ]
        return sorted(matches, key=lambda s: (-s.refs, s.file, s.line))

    def read_source_lines(self, file_rel: str) -> List[str]:
        fp = self.repo_path / file_rel
        try:
            return fp.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []
