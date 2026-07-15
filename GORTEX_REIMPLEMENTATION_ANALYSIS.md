# Gortex Reimplementation Analysis: 0-Dependency Python Port

## Executive Summary

Objective: Reimplement Gortex's core capabilities in pure Python (0 external dependencies) while maintaining the token reduction benefits that make TokenSeive powerful.

**Current Gortex Integration Issues:**
- Requires external Go binary (`gortex daemon`)
- Requires subprocess calls to communicate with daemon
- Creates deployment complexity (external dependency management)
- Not truly "zero-dependency" despite TokenSeive's philosophy

**Target State:**
- Pure Python implementation with zero external dependencies
- Same API surface as current `GortexCodebaseMapper`
- Same token reduction benefits (95-97% via GCX1 compression)
- Same graph query capabilities (persistent index, call chains)

---

## Gortex Features Currently Used

Based on analysis of `tokenseive/mapper/gortex_backend.py`, the following Gortex MCP tools are actively used:

### 1. Core Graph Operations
- **`search_symbols`** — Find functions/classes by name with filtering by kind
  - Parameters: `query`, `kinds`, `limit`, `compress_bodies`
  - Returns: Symbol locations with signatures, file paths, line numbers, symbol IDs

- **`get_symbol_source`** — Retrieve symbol source code with compression
  - Parameters: `symbol_id`, `compress_bodies` (bool), `format` ("gcx" for GCX1)
  - Returns: Compressed source code with token count

- **`get_call_chain`** — Trace inbound/outbound call relationships
  - Parameters: `symbol_id`, `direction` ("inbound"/"outbound"), `max_depth`
  - Returns: Hierarchical call graph with caller/callee relationships

### 2. Repository-Level Operations
- **`get_file_summary`** — Get file-level imports and dependents
  - Parameters: `path`
  - Returns: Import statements and files that depend on this file

- **`smart_context`** — Generate token-budgeted repository overview
  - Parameters: `query`, `max_tokens`, `compress_bodies`
  - Returns: Ranked symbol map compressed to token budget

- **`get_architecture`** — Extract repository architecture as graph
  - Parameters: `resolution` ("symbol"/"file")
  - Returns: Nodes (symbols/files) and edges (relationships)

- **`graph_stats`** — Get index statistics
  - Returns: Total symbols, files, graph tokens, index health

### 3. Daemon Management (via subprocess)
- **`gortex status`** — Check if daemon is running
- **`gortex repos`** — List tracked repositories
- **`gortex track <repo>`** — Add repository to workspace

---

## Go Dependencies → Pure Python Replacements

### 1. **Parsing Engine (Go: tree-sitter bindings)**

**Current Gortex:** Uses Go's tree-sitter bindings for 257 languages

**Pure Python Replacement:**
- **Primary:** Extend existing `tree-sitter` support (already optional in TokenSeive)
- **Fallback:** Python AST for Python code, regex for other languages
- **Advantage:** Already battle-tested in current `CodebaseMapper`

```python
# Current: tokenseive/mapper/repo_map.py already has this
try:
    from tree_sitter_language_pack import get_parser
except ImportError:
    # Regex fallback (zero dependency)
    pass
```

### 2. **Graph Storage (Go: in-memory + on-disk)**

**Current Gortex:** In-memory graph with persistence via Go serialization

**Pure Python Replacement:**
- **In-memory:** NetworkX (optional) or simple adjacency lists
- **Persistence:** JSON or pickle for cross-session index caching
- **Zero-dep version:** Pure Python dict-based graph with JSON persistence

```python
# Proposed structure
{
  "symbols": {
    "symbol_id": {
      "name": "compress",
      "kind": "function",
      "file": "tokenseive/compressors/rule_based.py",
      "line": 42,
      "signature": "def compress(text: str) -> CompressionResult",
      "body": "..."  # Full function body
    }
  },
  "relationships": {
    "calls": [
      {"from": "symbol_id_1", "to": "symbol_id_2"},
      {"from": "symbol_id_1", "to": "symbol_id_3"}
    ],
    "imports": [
      {"from": "file_1", "to": "module_2"}
    ]
  },
  "metadata": {
    "indexed_files": ["path/to/file.py"],
    "last_indexed": "2026-07-15T10:30:00Z",
    "total_symbols": 721
  }
}
```

### 3. **GCX1 Compression (Go: custom wire format)**

**Current Gortex:** Proprietary GCX1 compact wire format with body elision

**Pure Python Replacement:**
- **Technique:** AST-based body elision (remove implementation, keep structure)
- **Algorithm:**
  1. Parse function/class signature
  2. Extract parameter names and return types
  3. Replace body with placeholder comment `# ... (body compressed)`
  4. Preserve docstrings (critical for LLM context)

```python
# Example transformation
def compress(text: str) -> CompressionResult:
    """Compress text using rule-based patterns."""
    patterns = [
        r"\bit is important to note that\b",
        r"\bit should be noted that\b"
    ]
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result)
    return CompressionResult(
        original=text,
        compressed=result,
        tokens_removed=count_tokens(text) - count_tokens(result)
    )

# Becomes (GCX1 compressed):
def compress(text: str) -> CompressionResult:
    """Compress text using rule-based patterns."""
    # ... (body compressed, ~95% token reduction)
```

**Estimated savings:** 90-97% (matches Gortex's claims)

### 4. **Call Graph Traversal (Go: graph algorithms)**

**Current Gortex:** Native Go graph traversal algorithms

**Pure Python Replacement:**
- **Algorithm:** Recursive depth-first search with cycle detection
- **Implementation:** Pure Python using adjacency lists
- **Performance:** O(V+E) complexity, acceptable for repos <10k symbols

```python
def trace_call_chain(symbol_id: str, direction: str, max_depth: int, graph: Dict):
    visited = set()
    
    def dfs(current_id: str, depth: int) -> List[Dict]:
        if depth > max_depth or current_id in visited:
            return []
        visited.add(current_id)
        
        neighbors = get_neighbors(current_id, direction, graph)
        results = []
        for neighbor in neighbors:
            results.extend(dfs(neighbor, depth + 1))
        return results
    
    return dfs(symbol_id, 0)
```

### 5. **Symbol Search (Go: indexed search engine)**

**Current Gortex:** Indexed search with ranking

**Pure Python Replacement:**
- **Technique:** String matching with kind filtering
- **Optimization:** Pre-index symbols by name/kind for O(1) lookup
- **Fuzzy matching:** Optional difflib similarity scoring

```python
symbols_index = {
    "function": {
        "compress": ["symbol_id_1", "symbol_id_2"],
        "analyze": ["symbol_id_3"]
    },
    "class": {
        "CompressionResult": ["symbol_id_4"]
    }
}
```

---

## Technical Architecture for 0-Dependency Implementation

### Phase 1: Core Graph Engine

**New Module:** `tokenseive/mapper/graph_engine.py`

```python
class PersistentGraphIndex:
    """Zero-dependency persistent code graph."""
    
    def __init__(self, repo_path: Path, cache_path: Optional[Path] = None):
        self.repo_path = repo_path
        self.cache_path = cache_path or repo_path / ".tokenseive_graph.json"
        self.graph = self._load_or_build()
    
    def _load_or_build(self) -> Dict:
        """Load cached index or build from source."""
        if self.cache_path.exists():
            return json.loads(self.cache_path.read_text())
        return self._build_fresh_index()
    
    def _build_fresh_index(self) -> Dict:
        """Parse all files and extract symbols/relationships."""
        # Use existing tree-sitter or regex parser from repo_map.py
        pass
    
    def save(self) -> None:
        """Persist graph to disk."""
        self.cache_path.write_text(json.dumps(self.graph, indent=2))
```

### Phase 2: GCX1 Compression

**New Module:** `tokenseive/mapper/gcx1_compression.py`

```python
class GCX1Compressor:
    """Pure Python implementation of Gortex's GCX1 compression."""
    
    def compress_symbol(self, source_code: str, language: str = "python") -> str:
        """Compress symbol body while preserving signature and structure."""
        # Parse with AST (Python) or heuristics (other languages)
        # Extract signature + docstring
        # Replace body with compressed placeholder
        pass
    
    def calculate_compression_ratio(self, original: str, compressed: str) -> float:
        """Calculate token reduction percentage."""
        orig_tokens = count_tokens(original)
        comp_tokens = count_tokens(compressed)
        return (orig_tokens - comp_tokens) / orig_tokens
```

### Phase 3: Graph Query API

**New Module:** `tokenseive/mapper/native_graph_mapper.py`

```python
class NativeGraphMapper:
    """0-dependency graph-native codebase mapper."""
    
    def __init__(self, repo_path: Path):
        self.graph = PersistentGraphIndex(repo_path)
        self.compressor = GCX1Compressor()
    
    def search_symbols(self, query: str, kinds: List[str], limit: int) -> List[Dict]:
        """Search symbols by name and kind."""
        pass
    
    def get_symbol_source(self, symbol_id: str, compress: bool, format: str) -> Dict:
        """Get symbol source with optional GCX1 compression."""
        pass
    
    def get_call_chain(self, symbol_id: str, direction: str, max_depth: int) -> List[Dict]:
        """Trace inbound/outbound call relationships."""
        pass
```

### Phase 4: Drop-In Replacement

**Updated Module:** `tokenseive/mapper/gortex_backend.py`

```python
class GortexCodebaseMapper:
    """Unified mapper that uses native graph or falls back to tree-sitter."""
    
    def __new__(cls, repo_path: Path, use_native: bool = True):
        if use_native:
            # Use 0-dependency native implementation
            from .native_graph_mapper import NativeGraphMapper
            return NativeGraphMapper(repo_path)
        else:
            # Use external Gortex daemon (legacy)
            return _ExternalGortexMapper(repo_path)
```

---

## Implementation Complexity Matrix

| Feature | Gortex (Go) | Pure Python | Complexity | Notes |
|---------|-------------|-------------|------------|-------|
| Symbol parsing | Native tree-sitter | tree-sitter/AST/regex | **Low** (already exists) | Extend current `repo_map.py` |
| Graph storage | In-memory structs | JSON + dict | **Low** | Straightforward serialization |
| GCX1 compression | Custom wire format | AST-based elision | **Medium** | Need parser for each language |
| Call graph traversal | Graph algorithms | DFS/BFS in Python | **Medium** | Cycle detection needed |
| Symbol search | Indexed search | Dict lookup | **Low** | Pre-build name/kind indexes |
| Persistence | Binary serialization | JSON/pickle | **Low** | File I/O only |
| Cross-session cache | Daemon process | JSON cache file | **Low** | No process management |

**Overall Complexity:** **Medium** — Most features are straightforward, GCX1 compression requires the most work.

---

## Dependency Elimination Strategy

### External Dependencies to Remove

1. **`gortex` binary** — Replace with pure Python graph engine
2. **Subprocess calls** — Eliminate all `subprocess.run()` invocations
3. **Daemon process management** — Replace with file-based caching

### Optional Dependencies (Keep as-is)

1. **`tree-sitter-language-pack`** — Already optional, degrades gracefully
2. **`tiktoken`** — Used for token counting, has heuristic fallback
3. **`networkx`** (optional) — For advanced graph algorithms, not required

### Zero-Dependency Guarantee

```python
# Proposed import structure
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

try:
    from tree_sitter_language_pack import get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Core functionality works with both = False
assert TIKTOKEN_AVAILABLE == False or TREE_SITTER_AVAILABLE == False
```

---

## Performance Considerations

### Indexed Performance Comparison

| Operation | Gortex (Go) | Pure Python | Ratio |
|-----------|-------------|-------------|-------|
| Index 100 files | ~2s | ~5s | 2.5x slower |
| Search symbols | ~10ms | ~50ms | 5x slower |
| Call chain (depth=3) | ~50ms | ~200ms | 4x slower |
| GCX1 compression | ~5ms | ~20ms | 4x slower |

**Acceptable tradeoff:** 2-5x slower but still <1s for most operations, and eliminates external dependency entirely.

### Optimization Strategies

1. **Lazy indexing** — Only parse files when first accessed
2. **Incremental updates** — Only reindex changed files
3. **Background indexing** — Optional async index building
4. **Memoization** — Cache expensive queries

---

## Testing Strategy

### Unit Tests

```python
def test_graph_persistence():
    """Test that graph saves and loads correctly."""
    graph = PersistentGraphIndex(test_repo)
    graph.save()
    loaded = PersistentGraphIndex(test_repo)
    assert loaded.graph == graph.graph

def test_gcx1_compression():
    """Test that GCX1 compression achieves 90%+ reduction."""
    compressor = GCX1Compressor()
    compressed = compressor.compress_symbol(sample_function)
    ratio = compressor.calculate_compression_ratio(sample_function, compressed)
    assert ratio >= 0.90
```

### Integration Tests

```python
def test_api_compatibility():
    """Test that NativeGraphMapper matches GortexCodebaseMapper API."""
    native = NativeGraphMapper(test_repo)
    # Ensure all methods exist and match signatures
    assert hasattr(native, 'search_symbols')
    assert hasattr(native, 'get_symbol_source')
    assert hasattr(native, 'get_call_chain')
```

---

## Migration Path

### Phase 1: Foundation (Week 1)
1. Create `PersistentGraphIndex` class
2. Implement JSON-based graph storage
3. Add symbol extraction from existing parser

### Phase 2: Compression (Week 2)
1. Implement `GCX1Compressor` for Python
2. Add heuristic compression for other languages
3. Verify 90%+ compression ratios

### Phase 3: Query API (Week 3)
1. Implement graph search methods
2. Add call graph traversal
3. Test against existing Gortex API

### Phase 4: Integration (Week 4)
1. Update `GortexCodebaseMapper` to use native backend
2. Add fallback logic
3. Update tests and documentation

---

## Success Criteria

1. **Zero External Dependencies** — No subprocess calls to `gortex` binary
2. **API Compatibility** — Drop-in replacement for current `GortexCodebaseMapper`
3. **Performance** — <1s for common operations on repos <1000 files
4. **Compression** — ≥90% token reduction on test cases
5. **Persistence** — Cross-session index caching via JSON

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| GCX1 compression quality lower than Gortex | High | Extensive testing against real codebases |
| Performance degradation for large repos | Medium | Add optional indexing progress indicators |
| Language support limited (vs 257 in Gortex) | Low | Focus on Python first, expand gradually |
| Graph complexity increases memory usage | Medium | Add optional graph pruning for large repos |

---

## Next Steps

1. ✅ **Complete this analysis** — Define technical requirements
2. **Implement `PersistentGraphIndex`** — Core graph engine
3. **Implement `GCX1Compressor`** — Compression algorithm
4. **Create `NativeGraphMapper`** — Query API
5. **Update `GortexCodebaseMapper`** — Drop-in replacement
6. **Test and validate** — Ensure compatibility and performance

---

**Document Status:** ✅ Complete  
**Date:** 2026-07-15  
**Repository:** /home/minguyen/tokenseive  
**Next Phase:** Implementation of `PersistentGraphIndex` (task_2)
