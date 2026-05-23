"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Emits free-text prose in a strict canonical shape. The previous structured
(json_schema) path was removed: Gonka's vLLM caps json_schema completions
at ~3072 tokens, which made every structured attempt fail length-limit on
Kimi-K2.6 (run 33). The prompt now demands a fixed ``**Rating**: X`` /
``**Executive Summary**:`` / ``**Investment Thesis**:`` shape so the
downstream consumers (memory log, signal processor, runner) can extract
the rating via ``tradingagents.agents.utils.rating.parse_rating``.
"""

from __future__ import annotations

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


def create_portfolio_manager(llm):
    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

**Required Output Format** (use these exact headers, in this order):

**Rating**: <one of Buy / Overweight / Hold / Underweight / Sell>

**Executive Summary**: <2-4 sentences covering entry strategy, sizing, key risk levels, and time horizon>

**Investment Thesis**: <detailed reasoning anchored in specific evidence from the analysts' debate; incorporate prior lessons if any are referenced in the context above>

You MAY include these optional fields below the thesis when warranted:

**Price Target**: <number in the instrument's quote currency, e.g. 215.0>

**Time Horizon**: <e.g. 3-6 months>

The first line of your response MUST begin with ``**Rating**:`` followed by exactly one of the five tier names. Do not preface it with any other text or label. Do not use ``Recommendation:``, ``Final Rating:``, or any other variant — the literal label ``**Rating**:`` is required.

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        response = llm.invoke(prompt)
        final_trade_decision = response.content if hasattr(response, "content") else str(response)

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
