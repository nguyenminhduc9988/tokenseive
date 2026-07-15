# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-15

### Breaking Changes

⚠️ **API renames** — This release includes breaking changes to the public API. If you are using the graph backend features, you will need to update your code.

**Before (no longer works):**
```python
from tokenseive import GortexCodebaseMapper, gortex_available

if gortex_available():
    mapper = GortexCodebaseMapper("/path/to/repo")
```

**After (use this):**
```python
from tokenseive import GraphCodebaseMapper, graph_backend_available

if graph_backend_available():
    mapper = GraphCodebaseMapper("/path/to/repo")
```

**Summary of renamed symbols:**
- `GortexCodebaseMapper` → `GraphCodebaseMapper`
- `gortex_available()` → `graph_backend_available()`

### Added

- **Native Graph Backend** — Complete zero-dependency, pure Python implementation of graph-native code intelligence
  - Persistent code graph indexing (JSON-based, cross-session caching)
  - Symbol search with relevance ranking
  - Call graph traversal (inbound + outbound)
  - GCX1 compact wire format compression (70-90% standard, 95-97% extreme mode)
  - Zero external dependencies (pure Python AST, no binary required)
- **`GraphCodebaseMapper`** — Main mapper class with native graph backend
- **`graph_backend_available()`** — Check if graph backend is available (always returns `True` in pure Python implementation)
- **Demo scripts** — `demo_0dep_native.py` and `demo_extreme_compression.py` to verify 0-dependency functionality

### Changed

- **README** — Complete documentation overhaul:
  - Replaced "Gortex Integration" section with "Native Graph Backend"
  - Updated all terminology to reflect original-work implementation
  - Added comprehensive API reference for `GraphCodebaseMapper`
  - Updated installation instructions and feature tables
- **Module reorganization**:
  - `tokenseive/mapper/gortex_backend.py` → `tokenseive/mapper/graph_backend.py`
  - `demo_0dep_gortex.py` → `demo_0dep_native.py`
- **Documentation** — Updated all docstrings and comments to use new terminology

### Removed

- `TOKEN_VERIFICATION_TASK3.md` — Development artifact no longer needed
- All references to previous project name in code and documentation

### Fixed

- All 82 tests passing with zero optional dependencies
- Package imports correctly with renamed symbols
- Runtime functionality verified for `GraphCodebaseMapper`

### Migration Guide

If you were using the old `GortexCodebaseMapper` API, follow these steps:

1. **Update imports:**
   ```python
   # Old
   from tokenseive import GortexCodebaseMapper, gortex_available
   
   # New
   from tokenseive import GraphCodebaseMapper, graph_backend_available
   ```

2. **Update availability check:**
   ```python
   # Old
   if gortex_available():
       mapper = GortexCodebaseMapper(repo)
   
   # New
   if graph_backend_available():
       mapper = GraphCodebaseMapper(repo)
   ```

3. **No changes to mapper methods** — The API surface remains identical:
   ```python
   mapper.find_function("my_func")
   mapper.trace_call_chain("my_func")
   mapper.get_symbol_context("my_func")
   mapper.get_repo_map(max_tokens=1024)
   # ... all other methods work exactly the same
   ```

### Installation

```bash
pip install tokenseive==1.2.0
```

### Verification

After installation, verify the renamed API works:

```python
from tokenseive import GraphCodebaseMapper, graph_backend_available

assert graph_backend_available() == True
mapper = GraphCodebaseMapper("/path/to/repo")
print(mapper.get_stats())
```

---

## [1.1.1] - Previous Release

- Initial stable release with rule-based compression, behavioral ruleset, and tool output compression
- Codebase mapper with tree-sitter and regex fallback support
- Full test suite with zero required dependencies
