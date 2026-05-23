"""Research Manager: turns the bull/bear debate into a free-text investment plan for the trader.

The agent used to take a structured (json_schema) path that yielded a
typed ``ResearchPlan`` Pydantic instance; that path was removed because
Gonka's vLLM caps json_schema completions at ~3072 tokens, which made
every structured attempt fail length-limit on Kimi-K2.6 and silently
collapse to free text — at which point the rating was easy to lose
downstream. The agent now produces free-text prose in a strict canonical
shape that :func:`tradingagents.agents.utils.rating.parse_rating` can
parse reliably.
"""

from __future__ import annotations

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


def create_research_manager(llm):
    def research_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

**Required Output Format** (use these exact headers, in this order):

**Rating**: <one of Buy / Overweight / Hold / Underweight / Sell>

**Rationale**: <2-4 sentences summarising which arguments carried the debate>

**Strategic Actions**: <concrete steps for the trader, including position sizing consistent with the rating>

The first line of your response MUST begin with ``**Rating**:`` followed by exactly one of the five tier names. Do not preface it with any other text. Do not use any other label for the rating (no ``Recommendation:``, no ``Final Rating:``, no prose introduction).

---

**Debate History:**
{history}""" + get_language_instruction()

        response = llm.invoke(prompt)
        investment_plan = response.content if hasattr(response, "content") else str(response)

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node
