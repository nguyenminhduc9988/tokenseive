"""Tests for the behavioral output-optimization ruleset."""

from __future__ import annotations

import pytest

from tokenseive import BehavioralRuleset
from tokenseive.behavioral import VALID_MODES


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def test_valid_modes():
    assert VALID_MODES == ("off", "lite", "full", "ultra")


def test_off_is_empty():
    assert BehavioralRuleset(mode="off").get_instructions() == ""


@pytest.mark.parametrize("mode", ["lite", "full", "ultra"])
def test_non_empty_modes_have_content(mode):
    rs = BehavioralRuleset(mode=mode)
    assert len(rs.get_instructions()) > 50


def test_full_contains_ladder():
    text = BehavioralRuleset(mode="full").get_instructions()
    assert "ladder" in text.lower()
    assert "YAGNI" in text
    assert "lazy" in text.lower()


def test_ultra_more_verbose_than_full():
    full = BehavioralRuleset(mode="full").get_token_count()
    ultra = BehavioralRuleset(mode="ultra").get_token_count()
    assert ultra > full


def test_lite_shorter_than_full():
    lite = BehavioralRuleset(mode="lite").get_token_count()
    full = BehavioralRuleset(mode="full").get_token_count()
    assert lite < full


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        BehavioralRuleset(mode="aggressive")


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------
def test_token_count_positive():
    assert BehavioralRuleset(mode="full").get_token_count() > 0


def test_off_token_count_zero():
    assert BehavioralRuleset(mode="off").get_token_count() == 0


# ---------------------------------------------------------------------------
# apply_to helper
# ---------------------------------------------------------------------------
def test_apply_to_appends():
    rs = BehavioralRuleset(mode="full")
    combined = rs.apply_to("BASE PROMPT")
    assert combined.startswith("BASE PROMPT")
    assert "ladder" in combined.lower()


def test_apply_to_off_is_noop():
    rs = BehavioralRuleset(mode="off")
    assert rs.apply_to("BASE PROMPT") == "BASE PROMPT"


def test_apply_to_empty_prompt():
    rs = BehavioralRuleset(mode="lite")
    out = rs.apply_to("")
    assert out.strip() == rs.get_instructions().strip()


def test_apply_to_custom_separator():
    rs = BehavioralRuleset(mode="lite")
    out = rs.apply_to("BASE", separator="\n---\n")
    assert "\n---\n" in out
