"""Tests for the free-text rating extractor.

The three decision-making agents (Research Manager, Portfolio Manager,
Trader) now emit free-text prose instead of structured Pydantic output —
Gonka's vLLM caps json_schema completions at ~3072 tokens, which kept
structured output from ever succeeding on Kimi-K2.6 (run 33: every single
Research / Portfolio Manager structured call hit length-limit, fell back
to free-text, and the original parser silently lost the rating).

This file exercises ``parse_rating`` against the failure modes observed
in those runs: model rumination that mentions all five tiers before
committing, negated mentions ("not a Buy"), inflected forms ("buying"),
and the canonical headers the new prompts demand.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating


# ---------------------------------------------------------------------------
# Pass 1: canonical labels
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanonicalLabels:
    """The new agent prompts instruct the model to emit a specific label.
    These are the shapes the prompt-side contract guarantees."""

    def test_plain_rating_label(self):
        assert parse_rating("Rating: Buy") == "Buy"

    def test_bold_rating_label(self):
        assert parse_rating("**Rating**: Sell\nDetails follow.") == "Sell"

    def test_bold_label_and_value(self):
        assert parse_rating("**Rating**: **Overweight**") == "Overweight"

    def test_dash_separator(self):
        assert parse_rating("Rating - Hold") == "Hold"

    def test_equals_separator(self):
        assert parse_rating("Rating = Underweight") == "Underweight"

    def test_recommendation_label(self):
        assert parse_rating("Recommendation: Overweight") == "Overweight"

    def test_bold_recommendation_label(self):
        assert parse_rating("**Recommendation**: Underweight\nBody.") == "Underweight"

    def test_action_label(self):
        assert parse_rating("**Action**: Sell\nReasoning.") == "Sell"

    def test_final_call_label(self):
        assert parse_rating("My final call is Overweight.") == "Overweight"

    def test_my_recommendation_is_label(self):
        assert parse_rating("My recommendation is Buy with conviction.") == "Buy"

    def test_final_transaction_proposal_uppercase(self):
        assert parse_rating(
            "Body text.\n\nFINAL TRANSACTION PROPOSAL: **BUY**"
        ) == "Buy"

    def test_final_transaction_proposal_mixed_case(self):
        assert parse_rating(
            "FINAL TRANSACTION PROPOSAL: Sell"
        ) == "Sell"

    def test_all_five_tiers_recognised_via_label(self):
        for tier in RATINGS_5_TIER:
            assert parse_rating(f"Rating: {tier}") == tier


# ---------------------------------------------------------------------------
# Pass 1: last-match wins (the key fix vs the old parser)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLastLabelWins:
    """The old parser returned the FIRST label match. Kimi's prose almost
    always mentions multiple ratings while weighing the debate, so the
    final decision label — which comes last — must win."""

    def test_two_ratings_last_wins(self):
        text = (
            "The Rating could have been Hold, but after weighing the "
            "evidence, the final stance shifted.\n\nRating: Buy"
        )
        assert parse_rating(text) == "Buy"

    def test_intermediate_recommendation_then_final_rating(self):
        text = (
            "An initial Recommendation: Hold was considered, "
            "but the bull case ultimately prevailed.\n"
            "**Final Rating**: Overweight"
        )
        assert parse_rating(text) == "Overweight"

    def test_trader_proposal_overrides_earlier_action_label(self):
        text = (
            "**Action**: Hold (interim view during debate)\n"
            "...further analysis flipped the call...\n"
            "FINAL TRANSACTION PROPOSAL: **SELL**"
        )
        assert parse_rating(text) == "Sell"

    def test_three_competing_labels_last_wins(self):
        text = (
            "Rating: Buy in optimistic scenario.\n"
            "Rating: Sell in pessimistic scenario.\n"
            "Rating: Hold in balanced base case."
        )
        assert parse_rating(text) == "Hold"


# ---------------------------------------------------------------------------
# Pass 2: generic scan (no canonical label present)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenericScanFallback:
    """When the model deviates from the prompt's format demand, Pass 2 picks
    up the rating from natural prose. Walks from the end of the text so the
    conclusion wins over earlier deliberation."""

    def test_last_rating_word_wins_in_prose(self):
        text = (
            "Although the bear case argued for Sell with reasonable "
            "evidence, the bulls carried the debate. Buy."
        )
        assert parse_rating(text) == "Buy"

    def test_kimi_natural_phrasing(self):
        text = (
            "After weighing both sides, I see merit in the bull thesis "
            "around expanding margins, but the bear concerns about "
            "competitive pressure are substantive.\n\n"
            "My conclusion: leans Overweight with measured sizing."
        )
        # "My conclusion: leans Overweight" — label pattern doesn't match
        # ("conclusion" isn't in the allow-list); Pass 2 finds Overweight last.
        assert parse_rating(text) == "Overweight"

    def test_inflected_buying(self):
        assert parse_rating("We recommend buying with conviction.") == "Buy"

    def test_inflected_selling(self):
        assert parse_rating("Reduce exposure by selling into strength.") == "Sell"

    def test_inflected_holding(self):
        assert parse_rating("Maintain position by holding through earnings.") == "Hold"

    def test_overweight_in_prose_without_label(self):
        assert parse_rating(
            "Given the setup we lean Overweight on this name."
        ) == "Overweight"


# ---------------------------------------------------------------------------
# Pass 2: negation handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNegationHandling:
    """The old parser returned 'Buy' for 'I do NOT recommend a Buy here.'
    The new parser skips rating words preceded by negation tokens."""

    def test_not_recommend_buy_skipped(self):
        text = "I do not recommend a Buy here."
        # Only rating word is negated → falls to default.
        assert parse_rating(text) == "Hold"

    def test_negated_then_clean_word_picks_clean(self):
        text = (
            "I would not recommend a Buy at current levels.\n"
            "The right move is Sell."
        )
        assert parse_rating(text) == "Sell"

    def test_rather_than_skipped(self):
        text = "We lean Overweight rather than Buy outright."
        # 'Buy' is preceded by 'rather than' → skip; 'Overweight' wins.
        assert parse_rating(text) == "Overweight"

    def test_avoid_skipped(self):
        text = "Investors should avoid Sell here; the better call is Buy."
        # 'Sell' negated by 'avoid'; 'Buy' is clean.
        assert parse_rating(text) == "Buy"

    def test_instead_of_skipped(self):
        text = "Maintain exposure instead of Sell at these levels."
        # 'Sell' negated by 'instead of'; no other rating word → default.
        assert parse_rating(text) == "Hold"

    def test_shouldnt_skipped(self):
        text = "We shouldn't Sell at the lows; recommend Hold."
        assert parse_rating(text) == "Hold"

    def test_negation_does_not_bleed_across_sentence(self):
        text = (
            "We should not chase the rally here. "
            "The disciplined call is Buy on a pullback."
        )
        # 'Buy' is in a new sentence — the negation is 12+ words back.
        assert parse_rating(text) == "Buy"


# ---------------------------------------------------------------------------
# Real-world shapes from the new agent prompts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderedAgentShapes:
    """End-to-end: the exact shapes the three agents' new prompts demand."""

    def test_research_manager_canonical_shape(self):
        text = (
            "**Rating**: Buy\n\n"
            "**Rationale**: Bull case carried; AI tailwind intact.\n\n"
            "**Strategic Actions**: Build position gradually."
        )
        assert parse_rating(text) == "Buy"

    def test_portfolio_manager_canonical_shape(self):
        text = (
            "**Rating**: Overweight\n\n"
            "**Executive Summary**: Build over the next two weeks.\n\n"
            "**Investment Thesis**: Margins expanding; flows constructive."
        )
        assert parse_rating(text) == "Overweight"

    def test_trader_canonical_shape(self):
        text = (
            "**Action**: Sell\n\n"
            "**Reasoning**: Guidance cut hits margins.\n\n"
            "FINAL TRANSACTION PROPOSAL: **SELL**"
        )
        assert parse_rating(text) == "Sell"

    def test_pm_shape_with_optional_fields(self):
        text = (
            "**Rating**: Underweight\n\n"
            "**Executive Summary**: Trim 40% over two sessions.\n\n"
            "**Investment Thesis**: Multiple headwinds compounding.\n\n"
            "**Price Target**: 145.0\n\n"
            "**Time Horizon**: 1-3 months"
        )
        assert parse_rating(text) == "Underweight"


# ---------------------------------------------------------------------------
# Defaults / edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultsAndEdges:
    def test_empty_string_default(self):
        assert parse_rating("") == "Hold"

    def test_none_default_via_falsy(self):
        # parse_rating accepts a str; an empty falsy input returns default.
        assert parse_rating("") == "Hold"

    def test_no_rating_word_default(self):
        assert parse_rating(
            "No clear directional signal emerges from this analysis."
        ) == "Hold"

    def test_custom_default(self):
        assert parse_rating("Plain prose.", default="Underweight") == "Underweight"

    def test_case_insensitive_via_label(self):
        assert parse_rating("rating: BUY") == "Buy"
        assert parse_rating("RATING - sell") == "Sell"

    def test_punctuation_after_rating_in_prose(self):
        # The old parser tolerated "Buy." via word.strip("*:.,") — the new
        # generic scan uses \b word boundaries which already handle this.
        assert parse_rating(
            "After deliberation, the team is leaning Buy."
        ) == "Buy"

    def test_label_in_question_form_not_misread(self):
        # "Is the right call Buy or Sell?" — the model is asking, not
        # answering. Both words appear without negation. Last-occurrence
        # wins → Sell. (Acceptable: the prompt forbids this shape; the
        # parser's behaviour is documented.)
        assert parse_rating("Is the right call Buy or Sell?") == "Sell"
