"""Framework-agnostic agent integration.

TokenSeive imports nothing from LangChain, AutoGen, CrewAI, OpenAI, or any
specific agent framework — so the same three layers plug into all of them.
This example shows the pattern with a plain function-call "agent" and notes
for adapting it to each framework.

Run::

    python examples/agent_integration.py
"""

from __future__ import annotations

from tokenseive import BehavioralRuleset, CodebaseMapper, RuleBasedCompressor


def build_system_prompt(*, repo_path: str | None = None, lazy: bool = True) -> str:
    """Assemble an optimised system prompt from all three layers."""
    base = "You are a senior engineer. Answer coding questions precisely."

    # Layer 1: compress the base prompt (deterministic, zero-dep).
    compressor = RuleBasedCompressor()
    prompt = compressor.compress(base).compressed_text

    # Layer 2: append a token-budgeted repo map instead of whole files.
    if repo_path:
        mapper = CodebaseMapper(repo_path, verbose=False)
        prompt += "\n\n" + mapper.get_repo_map(max_tokens=1024)

    # Layer 3: inject the output-optimisation ruleset to cut response tokens.
    if lazy:
        ruleset = BehavioralRuleset(mode="full")
        prompt = ruleset.apply_to(prompt)

    return prompt


# ---- A minimal "agent": any function that takes a prompt -> returns text ----
def fake_llm(prompt: str) -> str:
    """Stand-in for any provider (OpenAI, Anthropic, local model, ...)."""
    return f"[reply using a {len(prompt)}-token prompt]"


def main() -> None:
    # No repo here, but the pattern is identical with one.
    prompt = build_system_prompt(repo_path=None, lazy=True)
    print("=== Optimised system prompt (head) ===")
    print(prompt[:400], "...\n")

    reply = fake_llm(prompt)
    print("Agent reply:", reply)

    # ---- Adapting to real frameworks ----------------------------------- #
    #
    # LangChain:
    #   from langchain_core.prompts import ChatPromptTemplate
    #   prompt = ChatPromptTemplate.from_messages([("system", build_system_prompt()),
    #                                              ("human", "{question}")])
    #
    # OpenAI (raw):
    #   import openai
    #   openai.chat.completions.create(model="gpt-4o",
    #       messages=[{"role": "system", "content": build_system_prompt()},
    #                 {"role": "user", "content": question}])
    #
    # AutoGen / CrewAI: pass build_system_prompt() as the agent's
    # `system_message` / `backstory`.


if __name__ == "__main__":
    main()
