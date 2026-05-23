"""Trader: turns the Research Manager's investment plan into a free-text transaction proposal.

Emits free-text prose in a strict canonical shape ending with the
``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line that the analyst
stop-signal text references. The structured (json_schema) path was
removed: Gonka's vLLM caps json_schema completions at ~3072 tokens, which
made every structured attempt fail length-limit on Kimi-K2.6 (run 33).
"""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


def create_trader(llm):
    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(company_name)
        investment_plan = state["investment_plan"]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan.\n\n"
                    "**Required Output Format** (use these exact headers, in this order):\n\n"
                    "**Action**: <one of Buy / Hold / Sell>\n\n"
                    "**Reasoning**: <2-4 sentences anchored in the analysts' reports and the research plan>\n\n"
                    "You MAY include the following optional fields after the reasoning when warranted:\n\n"
                    "**Entry Price**: <number in the instrument's quote currency>\n\n"
                    "**Stop Loss**: <number in the instrument's quote currency>\n\n"
                    "**Position Sizing**: <e.g. '5% of portfolio'>\n\n"
                    "Your response MUST end with this exact line, on its own, with the action name in uppercase between the asterisks:\n\n"
                    "FINAL TRANSACTION PROPOSAL: **BUY** (or **SELL** or **HOLD**, matching the Action above)\n\n"
                    "The first line of your response MUST begin with ``**Action**:`` followed by exactly one of Buy / Hold / Sell. Do not preface it with any other text."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        response = llm.invoke(messages)
        trader_plan = response.content if hasattr(response, "content") else str(response)

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
