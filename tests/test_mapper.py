"""Tests for the mapper layer.

Run with ZERO optional dependencies — tree-sitter/graphify are not installed,
so the regex fallback path is exercised. The fixture repo is created under
``tmp_path`` so nothing on disk is mutated outside the test run.
"""

from __future__ import annotations

import json

import pytest

from tokenseive import CodebaseMapper
from tokenseive.mapper import RepoMapEngine, Symbol


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def test_mapper_indexes_symbols(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    names = {s.name for s in mapper.symbols}
    assert {"process", "helper", "Service", "run"}.issubset(names)


def test_mapper_missing_repo_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CodebaseMapper(tmp_path / "does-not-exist", verbose=False)


def test_mapper_kinds_correct(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    by_name = {s.name: s for s in mapper.symbols}
    assert by_name["Service"].kind == "class"
    assert by_name["process"].kind == "function"
    # ``run`` is a method (defined inside a class) via the regex parser too.
    assert by_name["run"].kind == "method"
    assert by_name["run"].class_context == "Service"


def test_symbol_qualified_name():
    s = Symbol("m", "method", "f.py", 1, 1, "def m()", class_context="C")
    assert s.qualified == "C.m"
    s2 = Symbol("f", "function", "f.py", 1, 1, "def f()")
    assert s2.qualified == "f"


# ---------------------------------------------------------------------------
# Repo map
# ---------------------------------------------------------------------------
def test_repo_map_contains_files_and_symbols(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    repo_map = mapper.get_repo_map(max_tokens=2048)
    assert "core.py" in repo_map
    assert "def process" in repo_map or "process" in repo_map
    assert "class Service" in repo_map


def test_repo_map_respects_budget(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    small = mapper.get_repo_map(max_tokens=10)
    big = mapper.get_repo_map(max_tokens=4096)
    assert mapper.count_tokens(small) <= 40  # small budget keeps it tiny
    assert mapper.count_tokens(big) >= mapper.count_tokens(small)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def test_find_function(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    hits = mapper.find_function("process")
    assert len(hits) >= 1
    assert hits[0]["file"].endswith("core.py")
    assert hits[0]["kind"] == "function"


def test_find_class(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    hits = mapper.find_class("Service")
    assert len(hits) == 1
    assert "run" in hits[0]["methods"]


def test_trace_call_chain(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    trace = mapper.trace_call_chain("process", max_depth=3)
    assert trace["found"] is True
    # process calls helper
    flat = [name for level in trace["outbound"] for name in level]
    assert "helper" in flat


def test_trace_unknown_symbol(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    trace = mapper.trace_call_chain("no_such_thing")
    assert trace["found"] is False


def test_get_symbol_context(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    ctx = mapper.get_symbol_context("helper")
    assert "def helper" in ctx
    assert "tokens" in ctx.lower()


def test_get_symbol_context_missing(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    assert "not found" in mapper.get_symbol_context("ghost").lower()


def test_get_dependencies(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    # core.py imports utils -> core's imports include myapp.utils.
    core_deps = mapper.get_dependencies("myapp/core.py")
    assert "myapp.utils" in core_deps["imports"]
    # utils.py is imported by core -> utils' dependents include core.py.
    util_deps = mapper.get_dependencies("myapp/utils.py")
    assert any(d.endswith("core.py") for d in util_deps["dependents"])


# ---------------------------------------------------------------------------
# Graph + stats
# ---------------------------------------------------------------------------
def test_get_code_graph_synthetic(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    graph = mapper.get_code_graph()
    assert "nodes" in graph and "edges" in graph
    assert len(graph["nodes"]) >= 4
    relations = {e["relation"] for e in graph["edges"]}
    assert "contains" in relations


def test_export_graph_json(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    out = mapper.export_graph(format="json")
    data = json.loads(out)
    assert "nodes" in data
    assert len(data["nodes"]) >= 1


def test_get_stats(tiny_repo):
    mapper = CodebaseMapper(tiny_repo, verbose=False)
    stats = mapper.get_stats()
    assert stats["files_indexed"] >= 2
    assert stats["total_symbols"] >= 4
    assert isinstance(stats["token_reduction_pct"], float)
    assert stats["raw_tokens_all_files"] > 0
    assert stats["repo_map_tokens"] > 0


def test_token_reduction_positive_for_verbose_code(tmp_path):
    # A verbose file (long docstring + comments) should reduce to a far
    # smaller symbol map.
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "big.py").write_text(
        '"""A very large module with a lot of prose to make the point that\n'
        "the repo map is dramatically smaller than reading the whole file.\n"
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do\n"
        "eiusmod tempor incididunt ut labore et dolore magna aliqua.\n"
        '"""\n'
        "import os\n"
        "\n"
        "# This is a long, descriptive comment that pads the file out so that\n"
        "# the token-reduction metric is clearly positive for a repo map.\n"
        "def compute(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    mapper = CodebaseMapper(tmp_path, verbose=False)
    stats = mapper.get_stats()
    assert stats["token_reduction_pct"] > 0.0
