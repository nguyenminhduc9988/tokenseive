"""Surgical query helpers over a :class:`RepoMapEngine`.

These answer *where does X live and what does it touch* without making the
caller read whole files — exactly what you feed an LLM instead of dumping an
entire codebase into context.

All functions operate on a :class:`~tokenseive.mapper.repo_map.RepoMapEngine`
instance so they can be reused by :class:`~tokenseive.mapper.CodebaseMapper`
or called directly.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set

from ..utils import count_tokens
from .repo_map import RepoMapEngine, Symbol


def find_function(engine: RepoMapEngine, name: str) -> List[Dict]:
    """Find all functions/methods matching *name*."""
    out: List[Dict] = []
    for s in engine.match_symbols(name, kinds={"function", "method"}):
        out.append({
            "file": s.file,
            "line": s.line,
            "end_line": s.end_line,
            "signature": s.signature,
            "kind": s.kind,
            "class": s.class_context,
            "node_id": s.node_id,
            "refs": s.refs,
        })
    return out


def find_class(engine: RepoMapEngine, name: str) -> List[Dict]:
    """Find all classes matching *name*."""
    out: List[Dict] = []
    for s in engine.match_symbols(name, kinds={"class"}):
        out.append({
            "file": s.file,
            "line": s.line,
            "end_line": s.end_line,
            "signature": s.signature,
            "node_id": s.node_id,
            "refs": s.refs,
            "methods": [m.name for m in engine.symbols if m.class_context == s.name],
        })
    return out


def _bfs(start: str, graph: Dict[str, Set[str]], max_depth: int) -> List[List[str]]:
    seen = {start}
    levels: List[List[str]] = []
    frontier = [start]
    for _ in range(max_depth):
        nxt: List[str] = []
        level: List[str] = []
        for node in frontier:
            for nb in graph.get(node, ()):
                if nb not in seen:
                    seen.add(nb)
                    nxt.append(nb)
                    level.append(nb)
        if not level:
            break
        levels.append(level)
        frontier = nxt
    return levels


def trace_calls(engine: RepoMapEngine, function_name: str, max_depth: int = 3) -> Dict:
    """Trace what *function_name* calls (outbound) and what calls it (inbound).

    Returns ``{root, found, outbound, inbound, matched_locations}``.
    """
    out_map, inc_map = engine.calls_index()
    locations = engine.match_symbols(function_name)
    if not locations:
        return {"root": function_name, "found": False, "note": "no matching symbol"}

    outbound = _bfs(function_name, out_map, max_depth)
    inbound = _bfs(function_name, inc_map, max_depth)
    return {
        "root": function_name,
        "found": True,
        "outbound": outbound,
        "inbound": inbound,
        "matched_locations": [
            {"file": s.file, "line": s.line, "signature": s.signature, "kind": s.kind}
            for s in locations
        ],
    }


def get_dependencies(engine: RepoMapEngine, file_path: str) -> Dict:
    """Get imports of a file and the files that depend on it."""
    target = file_path.replace("\\", "/")
    imports: List[str] = []
    dependents: List[str] = []
    modname = os.path.splitext(os.path.basename(target))[0]
    pkg = re.sub(r"\.py$", "", target.replace("\\", "/").replace("/", ".").lstrip("."))
    for fp in engine.files:
        r = str(fp.relative_to(engine.repo_path))
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if r.replace("\\", "/") == target.replace("\\", "/"):
            for m in re.finditer(
                r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", text, re.MULTILINE
            ):
                imports.append(m.group(1) or m.group(2))
        else:
            if re.search(rf"\b(?:import|from)\s+{re.escape(pkg)}\b", text) or (
                modname != "__init__"
                and re.search(
                    rf"\bfrom\s+[\w\.]+\s+import\s+[^\n]*\b{re.escape(modname)}\b", text
                )
            ):
                dependents.append(r)
    return {
        "file": target,
        "imports": sorted(set(imports)),
        "dependents": sorted(set(dependents)),
    }


def get_symbol_context(engine: RepoMapEngine, symbol_name: str) -> str:
    """Get a compact context block for a symbol.

    Includes the definition plus its immediate callers/callees — what you'd
    feed the LLM instead of reading the whole file.
    """
    syms = engine.match_symbols(symbol_name)
    if not syms:
        return f"# Symbol '{symbol_name}' not found."

    out_map, inc_map = engine.calls_index()
    blocks: List[str] = []
    for s in syms[:3]:  # cap to a few definitions
        lines = engine.read_source_lines(s.file)
        body = "\n".join(lines[s.line - 1: min(s.end_line, len(lines))])
        if not body.strip():
            body = "\n".join(lines[s.line - 1: s.line - 1 + 40])
        callees = sorted(out_map.get(s.name, set()))
        callers = sorted(inc_map.get(s.name, set()))
        blocks.append(
            f"## {s.signature}  ({s.file}:{s.line})\n"
            f"```python\n{body}\n```\n"
            f"- calls: {', '.join(callees) if callees else '(none detected)'}\n"
            f"- called by: {', '.join(callers) if callers else '(none detected)'}\n"
            f"- references across repo: {s.refs}\n"
        )
    header = f"# Context for '{symbol_name}' ({len(syms)} location(s))\n"
    tok = count_tokens(header + "\n".join(blocks))
    return header + "\n".join(blocks) + f"\n(≈{tok} tokens)\n"


def get_stats(engine: RepoMapEngine) -> Dict:
    """Return mapping statistics for *engine*."""
    from .code_graph import build_code_graph

    graph = build_code_graph(engine)
    raw_tokens = 0
    for fp in engine.files:
        try:
            raw_tokens += count_tokens(fp.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    full_map = engine.get_repo_map(max_tokens=10 ** 9)
    map_tokens = count_tokens(full_map)
    graph_tokens = graph["stats"].get("graph_tokens", 0)
    kind_counts: Dict[str, int] = {}
    for s in engine.symbols:
        kind_counts[s.kind] = kind_counts.get(s.kind, 0) + 1
    return {
        "repo_path": str(engine.repo_path),
        "files_indexed": len(engine.files),
        "total_symbols": len(engine.symbols),
        "symbol_kinds": kind_counts,
        "total_loc": engine._total_loc,
        "index_seconds": round(engine._index_time, 2),
        "graph": graph["stats"],
        "graph_source": (
            "graphify"
            if not graph["stats"].get("fallback")
            else graph["stats"].get("fallback")
        ),
        "graphify_error": graph["stats"].get("graphify_error"),
        "raw_tokens_all_files": raw_tokens,
        "repo_map_tokens": map_tokens,
        "token_reduction_pct": round((1 - map_tokens / max(raw_tokens, 1)) * 100, 2),
    }
