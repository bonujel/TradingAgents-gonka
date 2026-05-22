"""Tests for the degenerate-output detector.

Covers the two failure shapes observed on Gonka with Qwen — a repetition
loop and CJK/token salad — plus guards that healthy reports never trip.
"""

import pytest

from tradingagents.agents.utils.degeneracy import (
    DegenerateOutputError,
    check_not_degenerate,
    is_degenerate_text,
)


# A long, varied report — genuine prose, so vocabulary diversity,
# top-word, top-char and top-bigram frequencies all sit in the healthy
# band well clear of every threshold.
_HEALTHY_REPORT = (
    "Nvidia closed the week at a fresh record, extending a rally that "
    "began after blowout datacenter guidance. The fifty-day moving "
    "average now sits comfortably below price, while the longer two "
    "hundred-day line confirms an intact primary uptrend. Momentum tells "
    "a more nuanced story: the relative strength index has pushed into "
    "overbought territory three times this month, yet each pullback was "
    "shallow and quickly absorbed by dip buyers. MACD remains positive "
    "but its histogram is flattening, an early hint that the pace of "
    "gains may cool into earnings. Volatility, measured by average true "
    "range, has compressed sharply, which historically precedes a "
    "decisive directional move rather than continued drift. Volume "
    "during the latest leg higher was lighter than the breakout itself, "
    "a mild divergence worth monitoring. Options positioning skews "
    "bullish, though dealers are now better hedged than a month ago. On "
    "balance the technical picture favors patient accumulation on "
    "weakness toward support near the rising twenty-day basis, with a "
    "protective stop below the most recent swing low. Traders chasing "
    "strength here accept poor reward-to-risk; disciplined entries fare "
    "better. Fundamental tailwinds remain firmly intact, but valuation "
    "leaves little room for disappointment, so position sizing should "
    "stay measured until guidance clears the unusually high bar that "
    "consensus has set for the upcoming quarterly release."
)


@pytest.mark.unit
class TestIsDegenerateText:
    def test_empty_and_none_are_not_degenerate(self):
        assert is_degenerate_text(None) is False
        assert is_degenerate_text("") is False
        assert is_degenerate_text("   ") is False

    def test_short_text_is_left_alone(self):
        """Below the length floor the detector abstains — terse output is
        the structured-output min-length guard's concern, not this one."""
        assert is_degenerate_text("Hold. Fundamentals are stable.") is False

    def test_healthy_long_report_is_not_degenerate(self):
        assert len(_HEALTHY_REPORT) > 600
        assert is_degenerate_text(_HEALTHY_REPORT) is False

    def test_repetition_loop_is_degenerate(self):
        """The News-analyst failure shape: one short phrase looped until
        it fills the token budget."""
        assert is_degenerate_text("you are a user, " * 400) is True

    def test_cjk_char_salad_is_degenerate(self):
        """The Market-analyst failure shape: one character drowns the
        output. Word-splitting on whitespace cannot see this — the
        character-level signal must."""
        assert is_degenerate_text("的" * 900) is True

    def test_token_salad_with_repeated_bigram_is_degenerate(self):
        """Broad-ish vocabulary but a hammering bigram — survives the
        vocabulary-collapse signal, caught by the bigram signal."""
        salad = ("the the are) are) of) of) " + "filler%d " % 0) * 80
        assert is_degenerate_text(salad) is True


@pytest.mark.unit
class TestCheckNotDegenerate:
    def test_healthy_text_does_not_raise(self):
        check_not_degenerate(_HEALTHY_REPORT, "Market Analyst")  # no raise

    def test_degenerate_text_raises_with_agent_name(self):
        with pytest.raises(DegenerateOutputError, match="News Analyst"):
            check_not_degenerate("you are a user, " * 400, "News Analyst")
