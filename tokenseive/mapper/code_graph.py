"""Code graph layer — graphify (optional) with tree-sitter fallback.

Layer 2 of the mapper: builds a structured ``{nodes, edges}`` graph for
surgical code retrieval. When `graphify <https://pypi.org/project/graphifyy/>`_
is installed (``pip install tokenseive[mapper]``) it is used directly;
otherwise we synthesise a lightweight graph from the tree-sitter/regex symbol
index so downstream queries keep working.

Generalised from the original Hermes ``codebase_mapper.py``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..utils import count_tokens
from .repo_map import RepoMapEngine

logger = logging.getLogger("tokenseive.mapper")

# Optional dependency: graphify (loaded lazily, degrades to synthetic graph).
_GRAPHIFY_EXTRACT = None
try:  # pragma: no cover - import-time behaviour depends on environment
    from graphify.extract import extract as _graphify_extract  # type: ignore
except Exception:  # pragma: no cover
    _graphify_extract = None

_GRAPHIFY_EXPORT: Dict[str, Any] = {}
try:  # pragma: no cover
    from graphify.export import (  # type: ignore
        to_html as _g_to_html,
        to_json as _g_to_json,
        to_svg as _g_to_svg,
    )
    _GRAPHIFY_EXPORT = {"json": _g_to_json, "html": _g_to_html, "svg": _g_to_svg}
except Exception:  # pragma: no cover
    pass


def graphify_available() -> bool:
    """Return ``True`` if ``graphify`` is importable."""
    return _graphify_extract is not None


def _empty_graph(engine: RepoMapEngine) -> Dict[str, Any]:
    """Synthesize a graph from the tree-sitter/regex symbol index.

    This keeps downstream queries functional even without graphify.
    """
    nodes, edges = [], []
    for s in engine.symbols:
        nodes.append({
            "id": s.node_id or f"{s.file}::{s.qualified}",
            "label": s.qualified,
            "file_type": "code",
            "source_file": s.file,
            "source_location": f"L{s.line}",
            "_origin": "treesitter",
        })
    for s in engine.symbols:
        edges.append({
            "source": s.file,
            "target": s.node_id,
            "relation": "contains",
            "confidence": "SYNTHETIC",
            "source_file": s.file,
            "source_location": f"L{s.line}",
            "weight": 1.0,
        })
        for callee in s.calls:
            edges.append({
                "source": s.node_id,
                "target": callee,
                "relation": "calls",
                "confidence": "SYNTHETIC",
                "source_file": s.file,
                "source_location": f"L{s.line}",
                "weight": 1.0,
            })
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "files_analyzed": len(engine.files),
            "graph_tokens": count_tokens(json.dumps(nodes) + json.dumps(edges)),
            "build_seconds": 0.0,
            "fallback": "tree-sitter (graphify unavailable)",
        },
    }


def build_code_graph(engine: RepoMapEngine, *, max_graph_files: int = 250) -> Dict[str, Any]:
    """Build a structured code graph for *engine*.

    Uses graphify when available; otherwise synthesises a graph from the
    engine's symbol index. Returns ``{nodes, edges, stats}``.
    """
    if _graphify_extract is None:
        return _empty_graph(engine)

    paths = [Path(f) for f in engine.files[:max_graph_files]]
    t0 = time.time()
    try:
        result = _graphify_extract(paths=paths, parallel=True)  # type: ignore[misc]
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        in_tok = result.get("input_tokens") or 0
        out_tok = result.get("output_tokens") or 0
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "files_analyzed": len(paths),
                "graph_tokens": out_tok or count_tokens(
                    json.dumps(nodes) + json.dumps(edges)
                ),
                "input_tokens": in_tok,
                "build_seconds": round(time.time() - t0, 2),
            },
        }
    except Exception as e:  # pragma: no cover
        logger.warning("graphify extraction failed (%s) — using tree-sitter-only mode", e)
        graph = _empty_graph(engine)
        graph["stats"]["graphify_error"] = f"{type(e).__name__}: {e}"
        return graph


def export_graph(
    engine: RepoMapEngine,
    format: str = "json",
    output_path: Optional[str] = None,
) -> str:
    """Export the code graph as JSON, HTML, or SVG (graphify-rendered)."""
    graph = build_code_graph(engine)
    nodes, edges = graph["nodes"], graph["edges"]
    fmt = format.lower()
    if fmt == "json" or _GRAPHIFY_EXPORT.get(fmt) is None:
        data = json.dumps(graph, indent=2)
        if output_path:
            Path(output_path).write_text(data, encoding="utf-8")
        return data
    try:
        rendered = _GRAPHIFY_EXPORT[fmt](nodes, edges)
        rendered = rendered if isinstance(rendered, str) else str(rendered)
        if output_path:
            Path(output_path).write_text(rendered, encoding="utf-8")
        return rendered
    except Exception as e:  # pragma: no cover
        logger.warning("graphify export '%s' failed (%s) — falling back to JSON", fmt, e)
        data = json.dumps(graph, indent=2)
        if output_path:
            Path(output_path).write_text(data, encoding="utf-8")
        return data
