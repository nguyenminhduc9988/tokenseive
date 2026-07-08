"""Output-optimization behavioral ruleset.

A framework-agnostic "lazy senior developer" ladder that you inject into any
LLM coding agent's prompt to cut its *output* tokens (typically 22–54%) by
forcing it to prefer deletion over addition, reuse over re-implementation, and
the shortest working diff over speculative abstraction.

This is the generalised, agent-agnostic form of the Ponytail ruleset: no
framework-specific assumptions, four intensity modes, and a tiny API.

Modes
-----
``off``
    Empty instructions — inject nothing.
``lite``
    Build what's asked, but name the lazier alternative in one line.
``full`` *(default)*
    The full ladder enforced. Stdlib/native first. Shortest diff, shortest
    explanation.
``ultra``
    YAGNI extremist. Deletion before addition. Ship the one-liner and
    challenge the rest of the requirement in the same breath.

Example
-------
>>> from tokenseive.behavioral import BehavioralRuleset
>>> ruleset = BehavioralRuleset(mode="full")
>>> system_prompt = base_prompt + "\n\n" + ruleset.get_instructions()
>>> ruleset.get_token_count()  # doctest: +SKIP
"""

from __future__ import annotations

from typing import Dict

from ..utils import count_tokens, estimate_tokens

VALID_MODES = ("off", "lite", "full", "ultra")

# --------------------------------------------------------------------------- #
# Ruleset text. Kept as plain module constants so callers can inspect/patch
# them without subclassing.
# --------------------------------------------------------------------------- #
_HEADER = (
    "You are a lazy senior developer. Lazy means efficient, not careless. "
    "The best code is the code never written. ACTIVE EVERY RESPONSE — no drift "
    'back to over-building. Off only when the user says "stop" / "normal mode".'
)

_LADDER = """\
## The ladder (stop at the first rung that holds)

1. Does this need to exist at all? Speculative need = skip it, say so in one line. (YAGNI)
2. Already in this codebase? A helper, util, type, or pattern that already lives here -> reuse it. Re-implementing what's a few files over is the most common slop.
3. Stdlib does it? Use it.
4. Native platform feature covers it? <input type="date"> over a picker lib, CSS over JS, DB constraint over app code.
5. Already-installed dependency solves it? Use it. Never add a new one for what a few lines can do.
6. Can it be one line? One line.
7. Only then: the minimum code that works.

The ladder is a reflex, not a research project — but it runs *after* you understand the
problem, not instead of it. Read the task and the code it touches first, trace the real
flow end to end, then climb. Two rungs work -> take the higher one and move on.

Bug fix = root cause, not symptom. Before editing, grep every caller of the function
you're about to touch. The lazy fix IS the root-cause fix: one guard in the shared
function is a smaller diff than a guard in every caller. Fix it once, where all callers
route through."""

_RULES = """\
## Rules

- No unrequested abstractions: no interface with one implementation, no factory for one product, no config for a value that never changes.
- No boilerplate, no scaffolding "for later" — later can scaffold for itself.
- Deletion over addition. Boring over clever; clever is what someone decodes at 3am.
- Fewest files possible. Shortest working diff wins — but only once you understand the problem.
- Complex request? Ship the lazy version and question it in the same response. Never stall on an answer you can default.
- Two stdlib options, same size? Take the one correct on edge cases.
- Mark deliberate simplifications with a comment naming the ceiling and upgrade path: `# lazy: global lock, per-account locks if throughput matters`."""

_OUTPUT = """\
## Output

Code first. Then at most three short lines: what was skipped, when to add it. No essays,
no feature tours, no design notes. If the explanation is longer than the code, delete the
explanation. Explanation the user explicitly asked for is not debt; give it in full.
Pattern: `[code] -> skipped: [X], add when [Y].`"""

_GUARDS = """\
## When NOT to be lazy

Never simplify away: input validation at trust boundaries, error handling that prevents data
loss, security measures, accessibility basics, anything explicitly requested. Never lazy about
understanding the problem — the ladder shortens the solution, never the reading. Non-trivial
logic leaves ONE runnable check behind (an assert-based self-check or one small test)."""

_ULTRA = """\
## Ultra intensity

YAGNI extremist. Deletion before addition. Ship the one-liner and challenge the rest of the
requirement in the same breath. If a feature is not explicitly requested AND not needed to make
the requested change work, do not build it — name it as deferred instead."""

_LITE = """\
## Lite intensity

Build what's asked. After each change, name the lazier alternative in one line and let the
user opt in. Do not block on it."""


def _build_full() -> str:
    return "\n\n".join([_HEADER, _LADDER, _RULES, _OUTPUT, _GUARDS])


def _build_ultra() -> str:
    return "\n\n".join([_HEADER, _LADDER, _RULES, _OUTPUT, _GUARDS, _ULTRA])


def _build_lite() -> str:
    return "\n\n".join([_HEADER, _LITE])


# Pre-computed texts so repeated get_instructions() calls are O(1).
_TEXTS: Dict[str, str] = {
    "off": "",
    "lite": _build_lite(),
    "full": _build_full(),
    "ultra": _build_ultra(),
}


class BehavioralRuleset:
    """Output-optimization ruleset for LLM coding agents.

    Injects a "lazy dev" ladder that reduces output tokens by 22–54% by
    steering the model toward the shortest working solution.

    Parameters
    ----------
    mode:
        One of ``"off"``, ``"lite"``, ``"full"`` (default), ``"ultra"``.

    Raises
    ------
    ValueError
        If *mode* is not one of the valid modes.
    """

    def __init__(self, mode: str = "full") -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown mode {mode!r}; valid options: {VALID_MODES}"
            )
        self.mode = mode

    def get_instructions(self) -> str:
        """Return the ruleset text for injection into prompts.

        Returns an empty string when ``mode == "off"``.
        """
        return _TEXTS[self.mode]

    def get_token_count(self) -> int:
        """Estimate the token count of the current ruleset text."""
        return count_tokens(self.get_instructions())

    # Convenience ------------------------------------------------------ #
    def apply_to(self, prompt: str, *, separator: str = "\n\n") -> str:
        """Append the ruleset to *prompt* (no-op when mode is ``"off"``).

        Convenience helper for the common ``base + ruleset`` pattern.
        """
        instructions = self.get_instructions()
        if not instructions:
            return prompt
        if not prompt:
            return instructions
        return f"{prompt.rstrip()}{separator}{instructions}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"BehavioralRuleset(mode={self.mode!r})"
