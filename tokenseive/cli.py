"""TokenSeive command-line interface.

Examples
--------
::

    tokenseive compress prompt.txt
    tokenseive compress prompt.txt --backend multi --rate 0.5
    tokenseive map /path/to/repo --max-tokens 1024
    tokenseive map /path/to/repo --find-function "my_func"
    tokenseive map /path/to/repo --trace "my_func" --depth 2
    tokenseive ruleset --mode full
    tokenseive ruleset --mode full --tokens
    tokenseive version
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------
def _cmd_compress(args: argparse.Namespace) -> int:
    from .compressors import CompressionPipeline

    if args.filepath == "-":
        text = sys.stdin.read()
    else:
        if not os.path.isfile(args.filepath):
            print(f"error: file not found: {args.filepath}", file=sys.stderr)
            return 2
        try:
            with open(args.filepath, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: could not read {args.filepath}: {exc}", file=sys.stderr)
            return 1

    pipeline = CompressionPipeline(backend=args.backend, rate=args.rate)
    result = pipeline.compress(text, backend=args.backend, rate=args.rate)

    if args.json:
        report = {
            "backend": args.backend,
            "rate": args.rate,
            "compressed_text": result.compressed_text,
            "original_tokens": result.original_tokens,
            "compressed_tokens": result.compressed_tokens,
            "tokens_saved": result.tokens_saved,
            "compression_ratio": result.compression_ratio,
            "techniques_applied": result.techniques_applied,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        ratio = result.compression_ratio
        tech = ", ".join(result.techniques_applied) or "(none)"
        print("=" * 60)
        print("TOKENSEIVE — COMPRESSION REPORT")
        print("=" * 60)
        print(f"Backend            : {args.backend}")
        print(f"Original tokens    : {result.original_tokens}")
        print(f"Compressed tokens  : {result.compressed_tokens}")
        print(f"Tokens saved       : {result.tokens_saved}")
        print(f"Compression ratio  : {ratio:.1%}")
        print(f"Techniques applied : {tech}")
        print("-" * 60)
        print("COMPRESSED OUTPUT:")
        print("-" * 60)
        print(result.compressed_text)
        print("=" * 60)

    if args.write:
        out_path = args.filepath + ".compressed"
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(result.compressed_text)
            if not result.compressed_text.endswith("\n"):
                fh.write("\n")
        print(f"\nWrote compressed output to: {out_path}")
    return 0


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------
def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _cmd_map(args: argparse.Namespace) -> int:
    from .mapper import CodebaseMapper

    extensions = set(args.ext) if args.ext else None
    try:
        mapper = CodebaseMapper(
            args.repo,
            max_files=args.max_files,
            extensions=extensions,
            verbose=not args.quiet,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.find_function:
        _print_json(mapper.find_function(args.find_function))
    elif args.find_class:
        _print_json(mapper.find_class(args.find_class))
    elif args.trace:
        _print_json(mapper.trace_call_chain(args.trace, max_depth=args.depth))
    elif args.context:
        print(mapper.get_symbol_context(args.context))
    elif args.dependencies:
        _print_json(mapper.get_dependencies(args.dependencies))
    elif args.graph:
        out = mapper.export_graph(format=args.format, output_path=args.output)
        stats = mapper.get_stats()["graph"]
        dest = args.output or "(stdout)"
        print(
            f"Graph exported to {dest}: {stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges, {stats['files_analyzed']} files"
        )
        if not args.output:
            print(out[:2000] + ("\n...[truncated]" if len(out) > 2000 else ""))
    elif args.stats:
        _print_json(mapper.get_stats())
    else:
        print(mapper.get_repo_map(max_tokens=args.max_tokens))
    return 0


# ---------------------------------------------------------------------------
# ruleset
# ---------------------------------------------------------------------------
def _cmd_ruleset(args: argparse.Namespace) -> int:
    from .behavioral import BehavioralRuleset

    try:
        ruleset = BehavioralRuleset(mode=args.mode)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.tokens:
        print(f"~{ruleset.get_token_count()} tokens (mode={args.mode})")
    else:
        instructions = ruleset.get_instructions()
        print(instructions)
        if not instructions.endswith("\n"):
            print()
    return 0


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
def _cmd_version(args: argparse.Namespace) -> int:
    print(f"tokenseive {__version__}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokenseive",
        description=(
            "Multi-layer token optimization for LLM applications — compress "
            "prompts, map codebases, reduce output."
        ),
    )
    parser.add_argument(
        "--version", action="store_true", help="Print the version and exit."
    )
    sub = parser.add_subparsers(dest="command")

    # --- compress ---
    p_compress = sub.add_parser("compress", help="Compress a prompt text file.")
    p_compress.add_argument("filepath", help="Path to the prompt file (or '-' for stdin).")
    p_compress.add_argument(
        "--backend",
        default="rules",
        choices=["rules", "selective", "llmlingua2", "multi"],
        help="Compression backend (default: rules).",
    )
    p_compress.add_argument("--rate", type=float, default=0.5, help="Target keep-rate for ML backends (default: 0.5).")
    p_compress.add_argument("--write", action="store_true", help="Also write compressed text to <filepath>.compressed.")
    p_compress.add_argument("--json", action="store_true", help="Emit a JSON report instead of the human-readable one.")
    p_compress.set_defaults(func=_cmd_compress)

    # --- map ---
    p_map = sub.add_parser("map", help="Map a repository for fast LLM retrieval.")
    p_map.add_argument("repo", help="Path to the repository to map.")
    p_map.add_argument("--max-files", type=int, default=None, help="Limit number of files indexed.")
    p_map.add_argument("--ext", nargs="*", default=None, help="File extensions to include, e.g. py ts go.")
    p_map.add_argument("-q", "--quiet", action="store_true", help="Suppress progress logging.")
    g = p_map.add_mutually_exclusive_group()
    g.add_argument("--find-function", metavar="NAME", help="Find functions/methods by name.")
    g.add_argument("--find-class", metavar="NAME", help="Find classes by name.")
    g.add_argument("--trace", metavar="NAME", help="Trace a call chain.")
    g.add_argument("--context", metavar="NAME", help="Compact LLM context for a symbol.")
    g.add_argument("--dependencies", metavar="FILE", help="Imports/dependents of a file.")
    g.add_argument("--graph", action="store_true", help="Build and export the code graph.")
    g.add_argument("--stats", action="store_true", help="Print mapping statistics.")
    p_map.add_argument("--max-tokens", type=int, default=1024, help="Token budget for repo map (default: 1024).")
    p_map.add_argument("--depth", type=int, default=3, help="Depth for call tracing (default: 3).")
    p_map.add_argument("--output", "-o", default=None, help="Output path for --graph export.")
    p_map.add_argument("--format", default="json", choices=["json", "html", "svg"], help="Graph export format.")
    p_map.set_defaults(func=_cmd_map)

    # --- ruleset ---
    p_ruleset = sub.add_parser("ruleset", help="Print the output-optimization ruleset.")
    p_ruleset.add_argument(
        "--mode",
        default="full",
        choices=["off", "lite", "full", "ultra"],
        help="Ruleset intensity (default: full).",
    )
    p_ruleset.add_argument("--tokens", action="store_true", help="Only print the estimated token count.")
    p_ruleset.set_defaults(func=_cmd_ruleset)

    # --- version ---
    p_version = sub.add_parser("version", help="Print the tokenseive version and exit.")
    p_version.set_defaults(func=_cmd_version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        return _cmd_version(args)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
