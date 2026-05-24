# Kimi Trading-Agents 全链路数据流向

记录每一个 LLM 调用从 prompt → vLLM → response → state → DB 的路径，定位
"Rating 为空 / 部分 analyst 字段为空" 出现在链路哪一段。

参考代码版本：分支 `gonka-tradeagents-kimi/v1-nfrontend-free-text`
                 commit `df85d70`（HEAD，2026-05-23）

## 0. 角色一览

每个 ticker 一次 `propagate()` 调用会跑 **~14-25 次 LLM 调用**，分布在 4 大段：

| 段 | 节点 | LLM | 调用次数 | 出口字段 |
|---|---|---|---|---|
| Analyst（带工具） | Market / Social(=Sentiment) / News / Fundamentals | quick_thinking_llm | 2-N 次/节点（agent 自循环 tool-call → tool_node → agent）| `market_report` / `sentiment_report` / `news_report` / `fundamentals_report` |
| Researcher debate | Bull → Bear ⇄ ... | quick_thinking_llm | `2 × max_debate_rounds`（默认 2 × 1 = 2 次） | `investment_debate_state.history` |
| Decision triple | Research Manager → Trader → Aggressive → Conservative → Neutral → ... → Portfolio Manager | RM/PM = **deep**, Trader/Risk debators = quick | RM 1 + Trader 1 + Risk 3×max_risk_rounds + PM 1 ≈ 5-7 次 | `investment_plan`, `trader_investment_plan`, `final_trade_decision` |
| 反射（次次） | Reflector | quick | 0 或 N（上次 ticker 的 pending 决策） | memory log |

`deep_thinking_llm` / `quick_thinking_llm` 都是同一个 `GonkaStreamSafeChatOpenAI`
实例，只是用 config 里 deep_think_llm / quick_think_llm 两个不同的模型名构造。
本次跑 deep=quick=`moonshotai/Kimi-K2.6`。

## 1. 总链路（一次 ticker 的完整路径）

```
app/runner.py: run_one()
  └─ TradingAgentsGraph.propagate(ticker, date)        ← graph 重置 self.curr_state
       └─ _resolve_pending_entries()                    ← 反射上次 pending（若有）
       │     └─ Reflector.reflect_on_final_decision()   ← 1 次 llm.invoke
       └─ _run_graph()
             └─ graph.invoke(init_state, ...)           ← LangGraph 调度
                   ├─ Market Analyst ──┐
                   │   chain = prompt | llm.bind_tools([get_stock_data, get_indicators])
                   │   result = chain.invoke(state["messages"])
                   │   if result.tool_calls:
                   │       state["messages"].append(result)
                   │       → tools_market (ToolNode 跑工具)
                   │       → 回到 Market Analyst, 再调用 llm（重复直到无 tool_calls）
                   │   else:
                   │       report = result.content
                   │       state["market_report"] = report
                   │       check_not_degenerate(report, "Market Analyst")   ← 默认关闭
                   ├─ Msg Clear Market（清空 messages，避免污染下一 Analyst）
                   ├─ Sentiment Analyst  ──（同上结构，但只取 messages 一次性 prompt）
                   ├─ News Analyst
                   ├─ Fundamentals Analyst
                   │
                   ├─ Bull Researcher                   ← llm.invoke(prompt_with_all_reports)
                   │   response.content → "Bull Analyst: <text>"
                   │   → investment_debate_state.bull_history + history
                   ├─ Bear Researcher                   ← 类似，bear_history
                   │   ↻ should_continue_debate 决定 Bull 还是 Bear（达到 2*N 退出）
                   │
                   ├─ Research Manager                  ← deep LLM
                   │   llm.invoke(prompt_with_debate_history)
                   │   investment_plan = response.content       ← ★ 赤裸读取，无守卫
                   │
                   ├─ Trader                            ← quick LLM
                   │   llm.invoke(messages=[system, user_with_investment_plan])
                   │   trader_investment_plan = response.content ← ★ 赤裸读取
                   │
                   ├─ Aggressive ⇄ Conservative ⇄ Neutral × max_risk_rounds
                   │   每个：llm.invoke(prompt_with_all_reports_and_history)
                   │   → risk_debate_state.<role>_history
                   │
                   └─ Portfolio Manager                 ← deep LLM
                       llm.invoke(prompt_with_research_plan + trader_plan + risk_history + past_context)
                       final_trade_decision = response.content   ← ★ 赤裸读取
       │
       └─ ta.process_signal(final_state["final_trade_decision"])
             └─ SignalProcessor.process_signal()
                  └─ parse_rating(text)                  ← 末位匹配规则提取 5 档评级
                       returns "Buy/Overweight/Hold/Underweight/Sell" 或默认 "Hold"
  └─ rating = parse_rating(final_decision) if final_decision else None  ← ★ 注意：final 为空时 rating=None
  └─ db.upsert_decision(ticker, date, rating, final_decision, reports, ...)
```

## 2. 每一个 LLM 调用的内部链路（同一份代码，对所有 agent 共享）

```
agent_node calls llm.invoke(prompt)
  ↓
GonkaStreamSafeChatOpenAI (= NormalizedChatOpenAI = langchain ChatOpenAI 子类)
  ↓
ChatOpenAI._generate / _stream
  ├─ streaming=True (gonka_client._STREAMING_DEFAULTS)
  ├─ stream_usage=True
  └─ POST api.gonkascan.com/v1/chat/completions
       SSE 流：
         data: {"choices":[{"delta":{"reasoning_content":"...."}}]}      ← 仅 vLLM/Kimi 有
         data: {"choices":[{"delta":{"reasoning_content":"...."}}]}
         ... 大量 reasoning chunks ...
         data: {"choices":[{"delta":{"content":"..."}}]}                  ← 真正 content
         data: {"choices":[{"delta":{"content":"..."}}]}
         data: {"choices":[{"finish_reason":"stop"}]}
         data: {"choices":[],"usage":{...}}                              ← 最后一个 chunk
         data: [DONE]
  ↓
langchain-openai _stream → AIMessageChunk(per delta.content)
  ├─ ★ delta.reasoning_content 直接落地，langchain 不收集 ★
  ├─ AIMessageChunk(content=delta.content or "")
  └─ generation_chunks.append(chunk)
  ↓
generate_from_stream(generation_chunks)
  ├─ message = sum(chunks)  ← 字符串拼接
  ├─ if len(generation_chunks)==0: raise ValueError("No generations found in stream")
  └─ AIMessage(content="<拼接的所有 delta.content>", response_metadata={finish_reason, ...})
  ↓
NormalizedChatOpenAI.invoke 返回 AIMessage
  ↓
agent: response.content  ← 字符串；若所有 delta.content 都是 "" 则为 ""
  ↓
state["xxx_report"] = report 或 trader_investment_plan = "" 等
  ↓
LangGraph commit state → next node
```

## 3. 数据进入 DB 的链路

```
final_state = graph.invoke(...)
  ├─ market_report / sentiment_report / news_report / fundamentals_report  ← Analyst 阶段写入
  ├─ investment_debate_state.judge_decision === investment_plan            ← RM 阶段
  ├─ trader_investment_plan                                                ← Trader 阶段
  └─ final_trade_decision                                                  ← PM 阶段
  ↓
app/runner.run_one():
  final_decision = final_state.get("final_trade_decision") or ""
  rating = parse_rating(final_decision) if final_decision else None         ← ★ "" → rating=None
  ↓
db.upsert_decision(
    rating, final_decision,
    reports={market_report, sentiment_report, news_report,
             fundamentals_report, investment_plan, trader_plan},
    ...
)
  ↓ INSERT OR REPLACE INTO decisions (UNIQUE(ticker, trade_date))
```

## 4. 失败模式定位表（与"空 Rating / 空 Analysis"的对应关系）

| 字段在 DB 为空的可能因 | 出现在链路哪一段 | 排查点 |
|---|---|---|
| `rating=NULL` | runner 看到 `final_trade_decision==""` → `rating=None` | 查 llm_debug.jsonl 里 PM 那条记录的 `content_len` |
| `final_decision=""` | PM `response.content==""` | 同上 — PM 那条记录的 finish_reason / content_len / messages 长度 |
| `trader_plan=""` | Trader `response.content==""` | 查 llm_debug.jsonl 里 Trader 那条记录 |
| `investment_plan=""` | RM `response.content==""` | 查 llm_debug.jsonl 里 Research Manager 那条记录 |
| `market_report=""` | Market Analyst 退出循环时 `result.content==""`（已经 tool-call 完跑完，最后一次纯文本输出为空） | 查 jsonl 里 Market Analyst 的**最后一次** call —— `tool_calls=[]` 那一条 |
| 同样 `sentiment_report=""` / `news_report=""` / `fundamentals_report=""` | 各 analyst 的最后一次 call content="" | 同上 |
| `rating='Hold'` 不像真意 | PM 输出非空但 parser 找不到 label / negation 把所有匹配过滤掉 → 默认 "Hold" | 查 PM 那条 jsonl 的 `content`；本地跑 `parse_rating` 验证 |

## 5. 已知的失败"无痛"传播路径

链路中有两处会让"空 content"无声穿透到 DB：

### A. 任意 agent 节点：空 content + finish_reason=stop（或 tool_calls）

```python
response = llm.invoke(...)
report = response.content  ← "" 时不抛
state["market_report"] = ""  ← LangGraph 接受空字符串字段
```

**LangGraph 不视空字符串为失败。** Node retry policy 只在 *异常* 时触发；
没异常就提交 state。

### B. PM 空 content → "" → rating=None

```python
final_decision = final_state.get("final_trade_decision") or ""  ← 既保护 None 也保护 ""
rating = parse_rating(final_decision) if final_decision else None
```

`if final_decision` 在空字符串时是 False，所以 `rating=None`。
这是为什么 DB 里看到 `rating=NULL` 而 `error=NULL` —— 这是设计行为。

### C. degeneracy 检测当前关闭

```python
# tradingagents/agents/utils/degeneracy.py
def _detection_enabled() -> bool:
    return os.environ.get("TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT", "0") ...
```

默认 `0`，所以 `check_not_degenerate` 在所有 analyst 里是 no-op。即使内容是
token salad，也直接写库（degenerate 不会触发，更不会触发 node retry）。

## 6. 关键观察：langchain 不收集 `reasoning_content`

`GonkaStreamSafeChatOpenAI` 继承 `NormalizedChatOpenAI` 继承
`langchain_openai.ChatOpenAI`。langchain-openai 的 streaming 实现只读取
`chunk.choices[0].delta.content`；**`delta.reasoning_content` 不被收集**。
结果：

- `AIMessage.content` 永远只包含 `delta.content` 拼接
- `AIMessage.additional_kwargs` 不会有 `reasoning_content`
- 因此 **debug log 里看不到 Kimi 推理段的内容**——只能看到可见输出
- 也因此，无法从 jsonl 直接判断"reasoning 是否吞掉了所有预算"

**直接证据需要走原始 OpenAI SDK 流式抓 reasoning_content（如
`scripts/probe_kimi_max_tokens.py` 做的那样）。**

## 7. 当前 jsonl 能验证的事

对每一条 jsonl 记录，我们能读到：

| 字段 | 含义 | 怎么用 |
|---|---|---|
| `metadata.langgraph_node` | 这次调用是哪个 agent 节点 | 区分 Market / News / Sentiment / Fundamentals / Research Manager / Trader / Aggressive / Conservative / Neutral / Portfolio Manager / Reflector |
| `metadata.langgraph_step` | LangGraph 第几步 | 同 ticker 内的时间排序 |
| `init_kwargs.max_tokens` | 8192 | 应该恒等 |
| `invocation_params.tools` | 是否有 tool 绑定 | True 仅 4 个 analyst |
| `messages` | 完整 prompt（角色 + content） | 长度 + 内容 → 估算 prompt 体量 |
| `response.generations[0].content` | 最终 AIMessage.content | **若为 "" 即空内容** |
| `response.generations[0].generation_info.finish_reason` | length / stop / tool_calls | length 是 max_tokens 用尽信号 |
| `response.generations[0].tool_calls` | 工具调用 | analyst 循环判断 |
| `elapsed_ms` | 端到端耗时 | 异常慢 → Cloudflare/路由问题 |
| `error` | langchain 报的异常 | 非空 = node retry 触发 |

## 8. 后续分析 SOP

1. 跑完后，用 `scripts/analyze_kimi_run.py`（下一步写）把 jsonl 按 ticker
   分组、按 langgraph_step 排序、joined 上 DB 同 (ticker, date) 行。
2. 重点圈定：
   - 哪些条目 `content_len == 0`（empty silent failure）
   - 哪些条目 `finish_reason == "length"`（max_tokens 撞顶）
   - 哪些条目 `finish_reason == "stop"` 却 `content_len < 200`（疑似 reasoning 吞光预算的尾段截断）
3. 把上述事件按节点分布画一张表，对应到 DB 行的空字段。
4. 给出每一处空字段的 root cause（用 jsonl 里那条记录的 finish_reason +
   content_len + prompt_len 锚定）。
