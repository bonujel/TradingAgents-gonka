# Kimi NVDA + MSFT — 空 Rating / 空 Analysis 根因报告

**时间**：2026-05-23 23:23 → 2026-05-24 00:20（约 57 分钟）
**配置**：router 模式，deep=quick=`moonshotai/Kimi-K2.6`，`max_workers=1`，2 个 ticker
**数据源**：`~/.tradingagents/app/logs/llm_debug.jsonl`（32 条记录）+ `~/.tradingagents/app/decisions.sqlite3`
**配合阅读**：[data_flow_trace.md](./data_flow_trace.md)、[kimi-empty-content-silent-failure-2026-05-23.md](./kimi-empty-content-silent-failure-2026-05-23.md)

---

## TL;DR

`max_tokens=8192` 对 Kimi-K2.6 在 TradingAgents 的真实 prompt 形态下**远远不够**。32 次 LLM 调用中 **17 次（53%）触发 `finish_reason='length'`**，其中 **7 次** 在还没产出任何可见 content 之前就被截断（reasoning 段独自吃光预算）。两个 ticker 的 Portfolio Manager 都死在这一点上：**`response.content=""` → `final_decision=""` → `rating=NULL`**。

`kimi-empty-content-silent-failure-2026-05-23.md` 的假说**得到完全验证**。

---

## 1. 总览

| 指标 | 数值 |
|---|---|
| 总 LLM 调用次数（NVDA+MSFT） | 32 |
| `finish_reason=length`（max_tokens 用尽） | **17 (53%)** |
| `finish_reason=tool_calls`（analyst 工具循环中间步骤） | 8 |
| `finish_reason=stop`（正常结束） | **5 (16%)** |
| `finish_reason=None`（5 分钟流式超时，无任何 finish） | **2 (6%)** |
| `content==""` 静默失败次数 | **7** |
| DB 行 `rating` 字段 | **NVDA=NULL，MSFT=NULL** |
| DB 行 `final_decision` 字段 | **NVDA=0 字节，MSFT=0 字节** |
| DB 行 `error` 字段 | **空（runner 0 异常）** |

---

## 2. 每条 LLM 调用全表

### NVDA（17 条调用）

| # | step | node | finish | tools | content | prompt | elapsed | 备注 |
|---|---|---|---|---|---:|---:|---:|---|
| 0 | 1 | Market Analyst | tool_calls | Y | 920 | 4,659 | 227.8s | tool 调用 #1 |
| 1 | 3 | Market Analyst | tool_calls | Y | 110 | 12,747 | 22.9s | tool 调用 #2 |
| 2 | 5 | Market Analyst | tool_calls | Y | 110 | 52,066 | 7.5s | tool 调用 #3 |
| 3 | 7 | Market Analyst | **length** | - | 4,645 | 61,741 | 38.3s | 最终 report 被截 |
| 4 | 9 | Social Analyst | **length** | - | **0** | 24,827 | 115.7s | **空 sentiment_report** |
| 5 | 11 | News Analyst | tool_calls | Y | 1 | 1,424 | 4.2s |  |
| 6 | 13 | News Analyst | **length** | - | 1,236 | 20,582 | 114.9s | news_report 截断 |
| 7 | 15 | Fundamentals | tool_calls | Y | 0 | 1,658 | 24.7s |  |
| 8 | 17 | Fundamentals | **length** | - | 6,068 | 17,579 | 34.7s | fund_report 截断 |
| 9 | 19 | Bull Researcher | **length** | - | 2,149 | 13,321 | 111.6s | 辩论被截 |
| 10 | 20 | Bear Researcher | **length** | - | 4,395 | 17,715 | 115.0s | 辩论被截 |
| 11 | 21 | **Research Manager** | stop | - | 1,346 | 8,136 | 140.3s | ✅ |
| 12 | 22 | **Trader** | stop | - | 692 | 2,944 | 38.2s | ✅ |
| 13 | 23 | Aggressive Analyst | **length** | - | **0** | 14,461 | 113.5s | **空** |
| 14 | 24 | Conservative Analyst | stop | - | 5,409 | 14,373 | 69.2s | ✅（与 #13 prompt 长度几乎相同！）|
| 15 | 25 | Neutral Analyst | **length** | - | **0** | 25,133 | 265.4s | **空** |
| 16 | 26 | **Portfolio Manager** | **length** | - | **0** | 18,050 | 114.0s | **空 final_decision** |

### MSFT（15 条调用）

| # | step | node | finish | tools | content | prompt | elapsed | 备注 |
|---|---|---|---|---|---:|---:|---:|---|
| 17 | 1 | Market Analyst | tool_calls | Y | 1 | 4,659 | 13.4s |  |
| 18 | 3 | Market Analyst | tool_calls | Y | 0 | 24,186 | 25.8s |  |
| 19 | 5 | Market Analyst | **length** | - | 9,568 | 53,914 | 114.5s | 截断但 content 非空 |
| 20 | 7 | Social Analyst | **None** | - | **0** | 23,497 | **301.8s** | **5 分钟流超时** |
| 21 | 9 | News Analyst | **None** | - | **0** | 1,424 | **300.4s** | **5 分钟流超时（小 prompt 也炸）** |
| 22 | 11 | Fundamentals | tool_calls | Y | 1 | 1,658 | 15.8s |  |
| 23 | 13 | Fundamentals | **length** | - | 9,821 | 19,599 | 264.3s |  |
| 24 | 15 | Bull Researcher | **length** | - | 5,294 | 20,761 | 124.3s |  |
| 25 | 16 | Bear Researcher | **length** | - | 4,986 | 31,445 | 112.5s |  |
| 26 | 17 | **Research Manager** | **length** | - | **0** | 11,872 | 133.2s | **空 investment_plan** |
| 27 | 18 | **Trader** | stop | - | 449 | 1,598 | 46.9s | ✅（连 RM 空都能跑） |
| 28 | 19 | Aggressive Analyst | **length** | - | 3,858 | 21,658 | 33.1s | 截但非空 |
| 29 | 20 | Conservative Analyst | **length** | - | 2,002 | 29,286 | 118.7s | 截但非空 |
| 30 | 21 | Neutral Analyst | stop | - | 6,725 | 33,232 | 33.9s | ✅ |
| 31 | 22 | **Portfolio Manager** | **length** | - | **0** | 23,289 | 111.5s | **空 final_decision** |

### 关键空字段对应（jsonl record → DB row）

| ticker | DB 字段 | DB 长度 | 来源记录 | 原因 |
|---|---|---:|---|---|
| NVDA | `final_decision` | **0** | rec #16 (PM step 26) | finish=length，content 段从未开始 |
| NVDA | `rating` | **NULL** | runner 看 `final_decision==""` → `parse_rating` 跳过 → `None` | 上一行结果 |
| NVDA | `sentiment_report` | **0** | rec #4 (Sentiment step 9) | finish=length，content 段从未开始 |
| MSFT | `final_decision` | **0** | rec #31 (PM step 22) | finish=length，content 段从未开始 |
| MSFT | `rating` | **NULL** | 同上 |  |
| MSFT | `investment_plan` | **0** | rec #26 (RM step 17) | finish=length，content 段从未开始 |
| MSFT | `sentiment_report` | **0** | rec #20 (Sentiment step 7) | finish=None，5 分钟流超时 |
| MSFT | `news_report` | **0** | rec #21 (News step 9) | finish=None，5 分钟流超时 |

---

## 3. 失败模式分类

### 3.1 模式 A：`finish_reason=length` + `content=""`（推理段独吞预算）

发生 **7 次**（NVDA: 4 次 / MSFT: 3 次）。

- 节点：Sentiment、Aggressive、Neutral、**Portfolio Manager (×2)**、Research Manager
- 共同点：**prompt 都不算短**（11k–25k 字符），但 8192 max_tokens 完全够不上 Kimi 在长 prompt 下的 reasoning 用量
- 链路：HTTP 200 → streaming → 只收到 reasoning chunk（langchain 全部丢弃）→ `finish_reason=length` 来 → 流关闭 → `AIMessage.content=""`
- 进入 LangChain → 无异常 → 进入 LangGraph state → 进入 DB

### 3.2 模式 B：`finish_reason=length` + `content` 非空但被截断

发生 **10 次**。这种 case 写入了**残缺**的 report（中途断在某个句子里）。下游 agent 看到截断的 report 依然继续，效果未知（可能比空更糟，因为 LLM 会"幻觉补洞"）。

### 3.3 模式 C：`finish_reason=None` + 流式 5 分钟超时

发生 **2 次**，均在 MSFT。

- elapsed 301.8s / 300.4s — **正好是 5 分钟**
- `generation_info=None`，`response_metadata={'model_provider': 'openai'}`（连 model_name 都丢了）
- 说明流根本没等到 `finish_reason` chunk，被 5 分钟 hard timeout 切断
- 这个 5 分钟来自哪里？需要排查 — OpenAI SDK 默认 stream read 超时是 600s，langchain-openai 也无显式覆盖；可能在 Gonka router / Cloudflare 一侧。**rec #21（News step 9）prompt 只有 1424 字符**也照样超时，说明不是 Kimi 慢，而是 router 一侧的连接被掐
- 同样是静默失败：langchain 不抛错，content 是 ""

### 3.4 关键反例：prompt 几乎一样大，结果不一样

| node (NVDA) | step | prompt | content | finish |
|---|---|---:|---:|---|
| Aggressive Analyst | 23 | 14,461 | **0** | length |
| Conservative Analyst | 24 | 14,373 | **5,409** | stop |

仅相差 88 字符，结果完全相反。这说明 **8192 是 Kimi 在这个负载下的临界值** — 任何一点点的 reasoning 长度抖动就足以把可见 content 完全挤掉。重新跑同样输入并不能保证结果重复。

---

## 4. 为什么没有任何告警 / 重试触发？

LangGraph 的 retry policy [tradingagents/graph/setup.py](../../tradingagents/graph/setup.py) 注册在每个节点上，但只在节点函数 **抛异常** 时触发。当前三处都是赤裸取 `.content`：

```python
# portfolio_manager.py:76-77
response = llm.invoke(prompt)
final_trade_decision = response.content if hasattr(response, "content") else str(response)
```

```python
# trader.py:61-62
response = llm.invoke(messages)
trader_plan = response.content if hasattr(response, "content") else str(response)
```

```python
# research_manager.py:60-61
response = llm.invoke(prompt)
investment_plan = response.content if hasattr(response, "content") else str(response)
```

**`""` 不抛异常 → node retry 不触发 → 状态被 commit → 流到 DB**。

Analyst 节点稍微复杂一点（市场 / 新闻 / 基本面有 `check_not_degenerate` 调用），但：
- `degeneracy.py` 的检测 **默认关闭**（`TRADINGAGENTS_DETECT_DEGENERATE_OUTPUT` 没设）
- 即使开启，degenerate 检测只关心**长但重复**的文本，对 `""` 完全不触发（`_MIN_CHARS=600` 阈值排除短文本）
- Sentiment Analyst 也调 `check_not_degenerate` —— 但默认关，所以 `""` 直接穿透

---

## 5. 为什么 langchain 看不到 reasoning 内容？

`langchain-openai` 的流式解析只读 `chunk.choices[0].delta.content`；vLLM 把 Kimi 的思考输出放在 `chunk.choices[0].delta.reasoning_content` 字段，**这个字段被丢弃**。

证据：32 条记录中 **没有任何一条** 的 `additional_kwargs` 非空。

后果：
- `AIMessage.content` 只包含可见输出
- 我们看不到 Kimi 实际消耗了多少 reasoning token（除非外侧用 openai SDK 直连，像 `scripts/probe_kimi_max_tokens.py`）
- 但既然 `finish_reason=length` + `content=""` 同时发生，说明 reasoning 已经把整个 8192 budget 吃完，可见输出阶段被切

---

## 6. 与之前的复盘比较

| 文件 | 主要声明 | 本次验证结果 |
|---|---|---|
| `kimi-empty-content-silent-failure-2026-05-23.md` | "Kimi reasoning 撑爆 max_tokens 是空 content 的根因" | ✅ **完全验证**（7 个 modeA 事件直接证实）|
| 同上 | "RM/Trader/PM 赤裸 `.content`、无空内容守卫" | ✅ 与代码当前状态一致 |
| 同上 | "现象是 100% 静默失败模式（NVDA/GOOGL/MSFT 3/3）" | ✅ 本次 PM 也是 **2/2 全空** |
| 同上 | "`max_tokens` 8192 → 65536（部分修补，未 commit）" | 本机仍是 8192；改动只在另一台机器工作树 |
| `kimi-run-23-2026-05-20.md` | "tool-call 兼容错误（vLLM auto tool-choice 未启用）" | ✅ Market Analyst tool_calls 阶段没炸但**最终输出 length 截断** — tool-calling 本身工作，问题在 reasoning 预算 |

---

## 7. 推荐修补（按优先级；本报告不动主代码）

### P0 — 必须做：empty-content guard（最小止血）

在 [research_manager.py:60](../../tradingagents/agents/managers/research_manager.py#L60)、[trader.py:61](../../tradingagents/agents/trader/trader.py#L61)、[portfolio_manager.py:76](../../tradingagents/agents/managers/portfolio_manager.py#L76) 三处统一加：

```python
response = llm.invoke(prompt)
content = response.content if hasattr(response, "content") else str(response)
if not content.strip():
    raise RuntimeError(
        f"<agent_name>: empty content from LLM "
        f"(finish_reason={response.response_metadata.get('finish_reason')!r}); "
        f"likely max_tokens exhausted by reasoning."
    )
```

- 抛 `RuntimeError` → LangGraph node retry policy 触发 → 3 次重试机会
- 至少把 100% 静默失败变成"重试后多数能拿到"
- 如果连续 3 次仍空 → 异常冒到 runner → DB 写 `error` 字段而不是把 `rating=NULL` 当成功

**注意**：4 个 Analyst（market / sentiment / news / fundamentals）也应该加，但条件略不同——它们的 `result.content=""` 是合法的中间状态（tool_calls 阶段）。守卫应当只在 `len(result.tool_calls) == 0` 时检查。

### P1 — 强烈推荐：`max_tokens` 大幅上调

`_GENERATION_DEFAULTS={"max_tokens": 8192}` → 至少 32768。

证据：
- 之前 `scripts/probe_kimi_max_tokens.py` 实测：Kimi 自然结束时 completion_tokens ≈ 1900–2200，reasoning 占 1500–1700。所以 **稳定区间是 ~3000 token**，留足缓冲到 **32k**
- 本次 17 条 length 截断 / 7 条空 content 全部是 8192 上限触发
- 关键观察：rec #14（Conservative）prompt 14,373 + content 5,409 + reasoning 占满剩余，刚好溢出 — 8192 是临界值
- 32k 给 reasoning ~25k 缓冲、content ~7k；Kimi-K2.6 上下文 128k，加上 prompt ~25k 输入仍宽裕

非 reasoning model（Qwen3-Instruct）不会用掉 32k，自然 `stop` 就停了 → 无成本影响。

### P2 — Kimi capability 条目（避免空 prompt 浪费 budget）

[tradingagents/llm_clients/capabilities.py](../../tradingagents/llm_clients/capabilities.py) 加：

```python
_KIMI_K2 = ModelCapabilities(
    supports_tool_choice=True,   # Kimi 支持 tool_choice
    supports_json_mode=False,    # vLLM 上 json_mode 不稳定
    supports_json_schema=False,  # vLLM 上 3072 token 上限
    preferred_structured_method="none",
)
_BY_ID["moonshotai/Kimi-K2.6"] = _KIMI_K2
```

这一步本身不修空 content，但避免未来重新加结构化输出时再次踩 3072 token 上限。

### P3 — 流式 5 分钟超时排查

两条 `finish_reason=None` 事件 elapsed_ms 都 ≈ 300_000ms。来源不明，需要专门排查：
- Gonka router 一侧的 idle timeout？
- Cloudflare upstream timeout？
- httpx 的 stream chunk 间隔超时？

修复方向：在 GonkaClient 加 `read_timeout` 显式控制 + 流停滞重试包装。但这是 follow-up，不在当前止血范围。

### P4 — finish_reason 日志钩子

`gonka_client.py` 加：

```python
fr = response.response_metadata.get("finish_reason")
if fr in ("length", "content_filter"):
    logger.warning(...)
```

让 runner log 直接显示哪次调用撞顶 / 被切。

---

## 8. 不该做的事

- **不要** 把 `degeneracy.py` 检测打开当成解决方案 — 它的阈值是为长重复文本设计的（_MIN_CHARS=600），对 `content=""` 完全不触发
- **不要** 直接给 `final_decision=""` 推断一个默认 rating（如 "Hold"）写库 — 这会把"模型彻底没产出"伪装成"模型决定 Hold"
- **不要** 删掉 stream usage `stream_options={"include_usage": True}` — 我们以后还需要它来精确测 completion_tokens（虽然现在 langchain 没把 usage 传到 response_metadata，但未来可能加）

---

## 9. 立即可做的验证步骤

1. 应用 P0 + P1（空内容守卫 + max_tokens=32768），保留 llm_debug 开启
2. 重新跑 NVDA + MSFT 单线程
3. 期望：node retry 救回 PM/Sentiment 中的 empty 事件；DB 里 NVDA + MSFT 都有非空 `final_decision` + 非 NULL `rating`
4. 在 llm_debug.jsonl 里期望看到 `finish_reason=length` 次数从 17 大幅下降（reasoning 不再吃光预算）

---

## 附：诊断工具

- 数据流向地图：[data_flow_trace.md](./data_flow_trace.md)
- 分析器：`python scripts/analyze_kimi_run.py --trade-date 2026-05-23 --since-ts 2026-05-23T15:23`
- token-capacity probe：`python scripts/probe_kimi_max_tokens.py`（含 cache-buster，外侧直连）
- 后台 poller 脚本：[poller.sh](./poller.sh)
- 阶段快照：[snapshots/](./snapshots/)
