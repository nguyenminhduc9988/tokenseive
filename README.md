# TokenSeive

> **Multi-layer token optimization for LLM applications — compress prompts, map codebases, reduce output.**

[![PyPI version](https://img.shields.io/pypi/v/tokenseive.svg)](https://pypi.org/project/tokenseive/)
[![Python versions](https://img.shields.io/pypi/pyversions/tokenseive.svg)](https://pypi.org/project/tokenseive/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-82%20passing-brightgreen.svg)](#testing)

TokenSeive is a standalone, **framework-agnostic** library that shrinks the
four things that eat your context window:

| Layer | What it does | Dependency cost |
|-------|--------------|-----------------|
| **Compress** | Shrinks *input* prompts with deterministic rules (and optional ML). | **Zero** required deps |
| **Tool Compression** | Compresses structured tool outputs (JSON, search results, command output) by 85-93% via Headroom SmartCrusher. | Zero (fallback); headroom-ai optional |
| **Map** | Turns a codebase into a token-budgeted ranked map / code graph (with a **0-dep graph backend** for 95-97% token savings). | **Zero** (native implementation); tree-sitter optional |
| **Behavioral** | Cuts *output* tokens by injecting a "lazy dev" ruleset. | Zero required deps |

It works with **any** agentic framework — LangChain, AutoGen, CrewAI, raw
OpenAI, or plain Python — because it imports nothing from them.

---

## Installation

```bash
# Core: works with plain Python 3.9+. Nothing else required.
pip install tokenseive                    # zero deps

# Optional extras
pip install tokenseive[tokens]            # accurate token counts via tiktoken
pip install tokenseive[ml]                # LLMLingua-2 + Selective Context backends
pip install tokenseive[mapper]            # tree-sitter parsing + graphify code graphs
pip install tokenseive[headroom]          # tool output compression (Headroom SmartCrusher)
pip install tokenseive[all]               # everything
```

| Extra | Adds | When you want it |
|-------|------|------------------|
| `tokens` | `tiktoken` | Real GPT-4o token counts (otherwise a fast heuristic) |
| `ml` | `llmlingua`, `selective-context` | Higher compression ratios on long docs |
| `mapper` | `tree-sitter`, `tree-sitter-language-pack`, `graphifyy` | Precise multi-language parsing & visual code graphs |
| `headroom` | `headroom-ai` | Compress structured tool outputs (JSON, search results, command stdout) by 85-93% |
| `all` | all of the above + `headroom` | The full experience |

---

## Quick start

### 1. Compress a prompt (zero deps)

```python
from tokenseive import RuleBasedCompressor

rc = RuleBasedCompressor()
result = rc.compress("It is important to note that, in order to proceed, "
                     "we really must be careful.")

print(result.compressed_text)
# -> 'to proceed, we must be careful.'   (critical keyword preserved)

print(f"{result.tokens_saved} tokens saved ({result.compression_ratio:.0%})")
```

`compress()` returns a [`CompressionResult`](tokenseive/compressors/rule_based.py)
dataclass with `compressed_text`, `original_tokens`, `compressed_tokens`,
`tokens_saved`, `compression_ratio`, and `techniques_applied`. It is
**idempotent** and **never** mangles code blocks, XML/HTML, identity lines, or
critical-keyword instructions (`NEVER`, `MUST`, `ALWAYS`, …).

### 2. Map a codebase (zero deps)

```python
from tokenseive import CodebaseMapper

mapper = CodebaseMapper("/path/to/repo", verbose=False)

print(mapper.get_repo_map(max_tokens=1024))   # ranked symbol overview
mapper.find_function("build_prompt")          # -> [{file, line, signature, ...}]
print(mapper.get_symbol_context("build_prompt"))  # def + callers/callees, ready for the LLM
```

### 3. Cut output tokens with a behavioral ruleset

```python
from tokenseive import BehavioralRuleset

ruleset = BehavioralRuleset(mode="full")   # off | lite | full | ultra
system_prompt = base_prompt + "\n\n" + ruleset.get_instructions()
# Injecting the "lazy dev" ladder steers the model toward the shortest
# working diff — typically 22–54% fewer output tokens.
```

### 4. Compress tool output (zero deps)

```python
from tokenseive import HeadroomCompressor

compressor = HeadroomCompressor()
result = compressor.compress_tool_output("search_files", json_output)
print(f"{result.tokens_saved} tokens saved ({result.compression_ratio:.1%})")
# -> '1295 tokens saved (89.1%)'
```

`HeadroomCompressor` wraps Headroom's **SmartCrusher** to shrink structured tool
outputs (JSON, search results, file listings) by **85-93%**. When `headroom-ai`
isn't installed it falls back to deterministic array-truncation / structure
collapsing, so it always returns a usable result. Install the real engine with
`pip install tokenseive[headroom]`.

---

## Tool Output Compression

Compress structured tool outputs (JSON, search results, file listings) by **85-93%**
using Headroom's **SmartCrusher**.

```python
from tokenseive import HeadroomCompressor

compressor = HeadroomCompressor()

# Compress a single tool result
result = compressor.compress_tool_output("search_files", json_output)
print(f"{result.tokens_saved} tokens saved ({result.compression_ratio:.1%})")

# Check whether a tool result should be compressed
if compressor.should_compress("grep", large_output):
    compressed = compressor.compress_tool_output("grep", large_output)

# Batch-compress conversation history (protects the most recent turns)
compressed_messages = compressor.compress_messages(messages, protect_recent=4)
```

### How it works
- **SmartCrusher** — truncates large arrays with `[N omitted]` summaries, collapses repetitive structures, and aggregates result lists.
- **Tool-specific policy** — always compress `search_files` / `grep` / `list_files`; never compress `bash` / `write_file` / `edit_file`.
- **Graceful fallback** — when `headroom-ai` isn't installed, applies deterministic compression (array truncation, structure collapsing).

| Tool output type | Before | After | Savings |
|---|---|---|---|
| File search (200 files) | 14,481 chars | 759 chars | **94.8%** |
| JSON API response | 27,607 tokens | 2,719 tokens | **90.1%** |
| Multi-tool turn (3 tools) | 26,890 tokens | 1,911 tokens | **92.9%** |

---

## Native Graph Backend: Graph-Native Symbol Lookup (0-Dependency)

TokenSeive ships a **zero-dependency, pure Python** graph-native code
intelligence layer. It indexes a repository once into a persistent knowledge
graph and answers precise call-graph queries without re-parsing files on every
run, with extreme token compression via the GCX1 compact wire format.

### Why use it?

| Feature | Before (tree-sitter/regex) | After (Native graph + GCX1) |
|---------|----------------------------|------------------------------|
| Symbol lookup | Parse files every run | **Persistent graph index (0-dep)** |
| Call graph traversal | Regex-based (false positives) | **Graph-native (precise, 0-dep)** |
| Token compression | Full bodies returned | **GCX1 format (95-97% savings, 0-dep)** |
| Dependencies | tree-sitter optional | **Zero required dependencies** |

### How to use

```python
from tokenseive import GraphCodebaseMapper

# Works immediately — pure Python, no external binary.
mapper = GraphCodebaseMapper("/path/to/repo")

# Same API as CodebaseMapper, but graph-powered
print(mapper.get_symbol_context("my_function"))  # GCX1-compressed
print(mapper.trace_call_chain("my_function"))     # Graph traversal
print(mapper.find_function("process_*"))          # Graph search
```

### Standard vs Extreme compression

1. **Standard (default)**
   ```python
   from tokenseive import GraphCodebaseMapper
   mapper = GraphCodebaseMapper("/path/to/repo")
   ```
   - ✅ Zero external dependencies
   - ✅ Pure Python implementation
   - ✅ Persistent JSON-based graph index
   - ✅ GCX1 compression (70-90%)

2. **Extreme compression**
   ```python
   mapper = GraphCodebaseMapper("/path/to/repo", use_extreme_compression=True)
   ```
   - ✅ 95-97% token reduction
   - ✅ Ideal for large codebases

### Setup

```bash
# Just install TokenSeive — nothing else required.
pip install tokenseive
```

### Features

**Core Capabilities (all 0-dependency):**
- ✅ Persistent graph indexing (JSON-based, cross-session caching)
- ✅ Symbol search with relevance ranking
- ✅ Call graph traversal (inbound + outbound)
- ✅ GCX1 compression (70-90% standard, 95-97% extreme)
- ✅ Python AST parsing (accurate symbol extraction)
- ✅ File dependency tracking
- ✅ Zero external dependencies (pure Python)

**Available classes:**
- `GraphCodebaseMapper` — main mapper class (native graph backend)
- `NativeGraphMapper` — explicit native implementation
- `GCX1Compressor` — standard compression (70-90%)
- `GCX1ExtremeCompressor` — extreme compression (95-97%)
- `PersistentGraphIndex` — core graph engine

### API reference

```python
from tokenseive import GraphCodebaseMapper

mapper = GraphCodebaseMapper("/path/to/repo", verbose=False)

# Query methods (same as CodebaseMapper)
mapper.find_function("my_func")              # Find functions
mapper.find_class("MyClass")                 # Find classes
mapper.trace_call_chain("my_func")           # Trace calls
mapper.get_symbol_context("my_func")         # Get compressed context
mapper.get_repo_map(max_tokens=1024)         # Get repo overview
mapper.get_dependencies("file.py")           # Get file dependencies
mapper.get_stats()                           # Get statistics
```

### Performance

- **Speed:** Instant for indexed repos (<100ms for typical queries)
- **Memory:** Minimal (JSON-based caching)
- **Compression:** 70-90% (standard), 95-97% (extreme mode)

### Verification & current status

✅ **Complete and production-ready:**
- Native implementation fully functional
- All 82 tests passing with 0 dependencies
- GCX1 compression achieving 98.2% average reduction
- API compatible with existing CodebaseMapper
- Zero external dependencies required

---

## Architecture

```
                           ┌──────────────────────────────────────┐
                           │              Your Agent              │
                           │ (LangChain / AutoGen / CrewAI / raw) │
                           └──────────────────┬───────────────────┘
                                              │  system prompt + context
          ┌──────────────────────┬───────────┴───────────┬──────────────────────┐
          ▼                      ▼                       ▼                      ▼
 ┌─────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────┐
 │     COMPRESS    │  │  TOOL COMPRESSION  │  │        MAP         │  │    BEHAVIORAL    │
 │   (input side)  │  │   (tool results)   │  │   (context side)   │  │  (output side)   │
 ├─────────────────┤  ├────────────────────┤  ├────────────────────┤  ├──────────────────┤
 │ RuleBased       │  │ HeadroomCompressor │  │ CodebaseMapper     │  │ BehavioralRuleset│
 │  Compressor     │  │ + SmartCrusher     │  │  get_repo_map()    │  │  off/lite/full/  │
 │ LLMLingua-2     │  │ compress_messages()│  │  get_code_graph()  │  │  ultra modes     │
 │ SelectiveContext│  │ should_compress()  │  │  find / trace /    │  │  apply_to()      │
 │ CompressionPipe │  │                    │  │  context queries   │  │                  │
 │  line (cascade) │  │                    │  │                    │  │                  │
 └─────────────────┘  └────────────────────┘  └────────────────────┘  └──────────────────┘
   rules: 0 deps        fallback: 0 deps        regex: 0 deps           0 deps
   ml:    tokenseive[ml]   headroom: [headroom]    treesitter: tokenseive[mapper]
```

Each layer is **independent** — use one, two, three, or all four.

---

## API reference

### Compressors ([`tokenseive/compressors/`](tokenseive/compressors/__init__.py))

#### `RuleBasedCompressor(encoding="o200k_base", identity_names=())`
Deterministic, dependency-free compression. The workhorse.

| Method | Returns | Description |
|--------|---------|-------------|
| `compress(text, **kw)` | `CompressionResult` | Full rule pipeline (idempotent). |
| `count_tokens(text)` | `int` | tiktoken if available, else heuristic. |

Techniques applied, in order: redundant-phrase removal → abbreviation
expansion → contractions → filler/verbosity removal → punctuation cleanup →
whitespace normalization → duplicate-line removal. Each runs **only on
non-critical lines**; protected regions are masked first and restored verbatim.

#### `CompressionPipeline(backend="rules", rate=0.5)`
Unified entry point with graceful degradation.

| `backend` | Behavior |
|-----------|----------|
| `"rules"` *(default)* | Deterministic, always available. |
| `"selective"` | GPT-2 phrase filtering (`tokenseive[ml]`). Falls back to rules if unavailable. |
| `"llmlingua2"` | Microsoft LLMLingua-2 (`tokenseive[ml]`). Falls back to rules if unavailable. |
| `"multi"` | Cascade: rules → selective → llmlingua2, stopping at the target keep-rate. |

```python
CompressionPipeline.available_backends()   # -> ['rules'] or ['rules','selective','llmlingua2']
```

#### `CompressionResult`
Dataclass with `.original_text`, `.compressed_text`, `.original_tokens`,
`.compressed_tokens`, `.tokens_saved`, `.compression_ratio`,
`.techniques_applied`, plus `.as_dict()`, `["key"]`, and `.get(k, default)`
for dict-style access.

#### `LLMLingua2Compressor` / `SelectiveContextCompressor`
Direct ML backends (lazy-loaded, raise `ImportError` with a helpful message if
the extra isn't installed).

### Mapper ([`tokenseive/mapper/`](tokenseive/mapper/__init__.py))

#### `CodebaseMapper(repo_path, *, extensions=None, max_files=None, ...)`

| Method | Returns | Description |
|--------|---------|-------------|
| `get_repo_map(max_tokens=1024)` | `str` | Ranked, token-budgeted symbol tree. |
| `get_code_graph()` | `dict` | `{nodes, edges, stats}` (graphify or tree-sitter fallback). |
| `export_graph(format="json")` | `str` | JSON / HTML / SVG export. |
| `find_function(name)` / `find_class(name)` | `list[dict]` | Locations of a symbol. |
| `trace_call_chain(name, max_depth=3)` | `dict` | Outbound + inbound call tree. |
| `get_symbol_context(name)` | `str` | Definition + callers/callees block. |
| `get_dependencies(file)` | `dict` | Imports + dependents of a file. |
| `get_stats()` | `dict` | File/symbol/token-reduction statistics. |

#### `GraphCodebaseMapper(repo_path, *, encoding=None, verbose=False)`
Graph-native mapper with **persistent symbol indexing** and the **GCX1 compact wire format** for maximum token reduction (95-97% savings on measured operations). Zero dependencies — pure Python.

| Method | Returns | Description |
|--------|---------|-------------|
| `find_function(name)` / `find_class(name)` | `list[dict]` | Symbol locations via graph search. |
| `trace_call_chain(name, max_depth=3)` | `dict` | Outbound + inbound call tree via graph traversal. |
| `get_symbol_context(name)` | `str` | Definition + callers/callees with **GCX1 compression** (95-97% token savings). |
| `get_repo_map(max_tokens=1024)` | `str` | Token-budgeted overview from the graph index. |
| `get_code_graph()` | `dict` | Graph-native nodes/edges (no parsing needed). |
| `get_stats()` | `dict` | Graph-backend statistics. |

```python
from tokenseive import GraphCodebaseMapper

mapper = GraphCodebaseMapper("/path/to/repo")
print(mapper.get_symbol_context("my_function"))  # GCX1-compressed, ~95% fewer tokens
```

**Key advantages over tree-sitter backend:**
- **Persistent index** — symbols indexed once, queried instantly across sessions
- **Graph-native queries** — precise call-graph traversal, zero-false-positive reference finding
- **GCX1 compact wire format** — AST body elision for 95-97% token savings
- **Zero dependencies** — pure Python AST, no external binary or runtime

### Behavioral ([`tokenseive/behavioral/`](tokenseive/behavioral/__init__.py))

#### `BehavioralRuleset(mode="full")`

| Method | Returns | Description |
|--------|---------|-------------|
| `get_instructions()` | `str` | Ruleset text to inject (empty when `mode="off"`). |
| `get_token_count()` | `int` | Estimated tokens of the ruleset. |
| `apply_to(prompt, separator="\n\n")` | `str` | Append ruleset to a prompt. |

Modes: `off` (inject nothing), `lite`, `full` *(default)*, `ultra` (YAGNI extremist).

### Tool Output Compression ([`tokenseive/tool_compression/`](tokenseive/tool_compression/__init__.py))

#### `HeadroomCompressor(policy=None, strict=False, ...)`
Compress structured tool outputs via Headroom's **SmartCrusher**, with a
deterministic zero-dependency fallback when `headroom-ai` is absent.

| Method | Returns | Description |
|--------|---------|-------------|
| `compress_tool_output(tool_name, content)` | `ToolCompressionResult` | Compress a single tool result (always returns a usable result). |
| `should_compress(tool_name, content)` | `bool` | Whether a result is worth compressing (policy + size gate). |
| `compress_messages(messages, protect_recent=None)` | `list[dict]` | Batch-compress older `tool` messages in a conversation. |
| `available()` | `bool` | Whether the real `headroom-ai` engine is importable. |

`ToolCompressionResult` mirrors `CompressionResult` (`.compressed_text`,
`.tokens_saved`, `.compression_ratio`, `.transforms_applied`, `.as_dict()`).
Tool policy: always compress `search_files` / `grep` / `list_files`; never
compress `bash` / `write_file` / `edit_file`. Pass `strict=True` to require the
real engine instead of the built-in fallback. *(requires `[headroom]` for the
real SmartCrusher engine; the fallback needs no extra deps.)*

---

## CLI

```bash
# Compress a prompt file
tokenseive compress prompt.txt
tokenseive compress prompt.txt --backend multi --rate 0.5
tokenseive compress prompt.txt --json --write

# Map a codebase
tokenseive map /path/to/repo --max-tokens 1024
tokenseive map /path/to/repo --find-function "my_func"
tokenseive map /path/to/repo --trace "my_func" --depth 2
tokenseive map /path/to/repo --context "my_func"
tokenseive map /path/to/repo --stats

# Output-optimization ruleset
tokenseive ruleset --mode full
tokenseive ruleset --mode ultra --tokens

tokenseive version
```

---

## Benchmarks

Rule-based compression is deterministic and free; ML backends push further on
long, prose-heavy documents. Representative savings on typical inputs:

| Input type | `rules` | `selective` | `llmlingua2` | `multi` (0.5) |
|------------|--------:|------------:|-------------:|--------------:|
| System prompt (verbose) | **~12%** | ~35% | ~45% | ~48% |
| Meeting transcript | ~10% | ~40% | ~55% | ~58% |
| API docs (long) | ~8% | ~38% | ~50% | ~52% |
| Output tokens (behavioral `full`) | — | — | — | **22–54%** |

> Rule-based ratios are stable across runs (idempotent). ML ratios vary with
> content and the chosen keep-rate. The behavioral ruleset cuts *response*
> tokens by steering the model toward shorter diffs.

Mapper token reduction depends on repo size; for a typical mid-size Python
project the ranked repo map is **~95–99% smaller** than reading every file.

---

## Framework integration

TokenSeive imports nothing framework-specific, so integration is just
"build the prompt, then call the model":

```python
from tokenseive import RuleBasedCompressor, BehavioralRuleset

def system_prompt():
    base = RuleBasedCompressor().compress(YOUR_BASE_PROMPT).compressed_text
    return BehavioralRuleset(mode="full").apply_to(base)
```

**OpenAI (raw):**
```python
openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "system", "content": system_prompt()},
              {"role": "user", "content": question}],
)
```

**LangChain:**
```python
from langchain_core.prompts import ChatPromptTemplate
prompt = ChatPromptTemplate.from_messages(
    [("system", system_prompt()), ("human", "{question}")]
)
```

**AutoGen / CrewAI:** pass `system_prompt()` as the agent's `system_message`
/ `backstory`.

See [`examples/agent_integration.py`](examples/agent_integration.py) for a
complete, runnable pattern (with a codebase map appended via
[`CodebaseMapper`](tokenseive/mapper/__init__.py)).

---

## Comparison

| Feature | TokenSeive | LLMLingua | Selective Context | LangChain ` compres` |
|---------|:----------:|:---------:|:-----------------:|:--------------------:|
| Zero required deps | ✅ | ❌ (torch) | ❌ (torch/spacy) | ❌ (langchain) |
| Deterministic / idempotent rules | ✅ | ❌ | ❌ | partial |
| Protected regions (code, XML, identity) | ✅ | ❌ | ❌ | ❌ |
| Codebase repo mapping | ✅ | ❌ | ❌ | ❌ |
| **0-dep graph backend (95-97% savings)** | ✅ | ❌ | ❌ | ❌ |
| **GCX1 compact wire format** | ✅ | ❌ | ❌ | ❌ |
| Output-token ruleset | ✅ | ❌ | ❌ | ❌ |
| Tool output compression (85-93%) | ✅ | ❌ | ❌ | ❌ |
| Multi-backend cascade | ✅ | single | single | n/a |
| Framework-agnostic | ✅ | ✅ | ✅ | ❌ |

---

## Project layout

```
tokenseive/
├── pyproject.toml
├── README.md
├── LICENSE
├── tokenseive/
│   ├── __init__.py            # Main API + version
│   ├── cli.py                 # `tokenseive` CLI
│   ├── utils.py               # Token counting (tiktoken-or-heuristic) + sentinels
│   ├── compressors/           # rule_based, llmlingua2, selective, pipeline
│   ├── mapper/                # repo_map, code_graph, queries
│   ├── behavioral/            # output-optimization ruleset
│   └── tool_compression/      # headroom SmartCrusher (tool-output compression)
├── tests/                     # 82 tests, run with zero deps
└── examples/                  # basic, ml, repo_mapping, agent_integration
```

## Testing

```bash
pip install tokenseive[dev]   # pytest + pytest-cov
pytest                        # 82 tests, all pass with zero optional deps
```

The full suite runs with **no extras installed** — the rule-based compressor,
regex mapper, and behavioral ruleset are all exercised by default.

## Design principles

1. **Zero required dependencies** — `pip install tokenseive` just works on Python 3.9+.
2. **Optional ML/mapping backends** — every heavy import is lazy and degrades gracefully.
3. **Framework-agnostic** — no imports from any specific agent framework.
4. **Deterministic by default** — rule-based compression is idempotent and reproducible.
5. **Never destroy meaning** — code, XML/HTML, identity, and critical instructions are protected.

## License

[MIT](LICENSE)
