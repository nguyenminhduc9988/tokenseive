"""Behavioral output-optimization layer for TokenSeive.

Public API
----------
* :class:`BehavioralRuleset` — injectable "lazy dev" ruleset (off / lite /
  full / ultra modes).
"""

from __future__ import annotations

from .ruleset import VALID_MODES, BehavioralRuleset

__all__ = ["BehavioralRuleset", "VALID_MODES"]
