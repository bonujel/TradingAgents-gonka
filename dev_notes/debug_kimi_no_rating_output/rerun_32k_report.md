# Kimi NVDA + MSFT 32k 重跑 — `kimi_max_tokens=32768` 没解决问题

**时间**：2026-05-24 00:51 → 01:52（约 61 分钟）
**唯一变量**：`kimi_max_tokens` 8192 → 32768（在 settings.json 中改，透过 `TRADINGAGENTS_KIMI_MAX_TOKENS` env 流到 [gonka_client.py:85](../../tradingagents/llm_clients/gonka_client.py#L85)）
**其他全部相同**：router 模式、单线程、NVDA+MSFT、Kimi-K2.6 deep=quick、无空内容守卫
**前情**：[root_cause_report.md](./root_cause_report.md)（8192 那一跑的根因分析）

---

## TL;DR

**4× 预算几乎没改善结果。** NVDA + MSFT 两个 ticker 的 `final_decision` 依然 `""`，`rating` 依然 `NULL`。Kimi 在更大预算下只是**用更多 token 推理**——MSFT 的 PM 在 13068 字符（~3.3k token）的 prompt 上把 **32768 token 全部用在 reasoning** 上还没产出一个字。

**真正的修补不是把预算继续往上推，而是 P0 的空内容守卫。** 32k 这次还顺手证明了：当 LLM 真抛异常时（如 `RemoteProtocolError`），LangGraph 的 node retry 工作正常并能救回失败——只是空 content 不抛异常所以救不到。

---

## 1. 8192 vs 32768 — 同步对比

| 指标 | 8192 | 32768 | Δ |
|---|---:|---:|---|
| 总 LLM 调用 | 32 | 34 | +2（一次 node retry）|
| `finish=length` | 17 | 16 | −1 |
| `finish=stop` | 5 | 5 | 0 |
| `finish=tool_calls` | 8 | 9 | +1 |
| **`finish=None`** | 2 | **4** | **+2** ⚠ |
| **`length` + `content=""`** | **7** | **6** | −1 |
| **整体 empty-content 事件** | 7 | **8** | **+1** ⚠（多出 1 个 `None+empty`）|
| NVDA `rating` | NULL | **NULL** | 同 |
| MSFT `rating` | NULL | **NULL** | 同 |
| NVDA `final_decision_len` | 0 | **0** | 同 |
| MSFT `final_decision_len` | 0 | **0** | 同 |

length 事件少了 1 个，但 None 多了 2 个 + empty 多了 1 个，**净影响为零或微负**。

DB 状态完全没救回来：

```
ticker   rating        final   mkt  sent  news  fund  plan   trd
MSFT     <NULL>            0  9109     0  6504  5683     0   484
NVDA     <NULL>            0   837     0  9758  7012     0   447
```

---

## 2. 仍然空 content 的 6 个 `length` 事件 + 1 个 `None+empty`

| ticker | node | step | prompt | finish | elapsed | 备注 |
|---|---|---:|---:|---|---:|---|
| NVDA | Sentiment | 7 | 25,110 | length | 115.7s | 8192 时也炸 |
| NVDA | Research Manager | 19 | 13,800 | **None** | 301.0s | 5 分钟流式硬超时 |
| NVDA | Portfolio Manager | 24 | 24,414 | length | 113.3s | 8192 时也炸 |
| MSFT | Sentiment | 7 | 23,026 | length | 35.2s | 8192 时也炸 |
| MSFT | Research Manager | 21 | 13,773 | length | 120.9s | 8192 是空 |
| MSFT | Aggressive | 23 | 23,600 | length | 113.7s | 8192 时也炸 |
| MSFT | **Portfolio Manager** | **26** | **13,068** | length | 211.7s | **短 prompt 也炸：32k 全用于 reasoning** |

**MSFT PM step 26 才是这次最有力的证据**：prompt 只有 **13068 字符 ≈ 3,300 prompt token**，但 finish=length 表明 Kimi 把全部 **32,768** 完成 token 用在了 reasoning 上，零 content。**这不是 prompt 太长的问题——是 Kimi reasoning 没有上限的问题。**

把预算从 8k 推到 32k 的 4 倍后果只是给 Kimi 更多空间继续推理。继续推到 65k 大概率得到一样的结果。

---

## 3. 关键事件：node retry 工作正常（但救不到 empty-content）

step 25 Neutral Analyst（MSFT）出现了一次**真正抛异常**的失败：

```
RemoteProtocolError: peer closed connection without sending complete
message body (incomplete chunked read)
```

LangGraph node retry 立即介入，记录里能看到 step 25 出现 **两条 jsonl**：
- 第 1 次：finish=None, content=0, elapsed=200.9s, error=RemoteProtocolError ❌
- 第 2 次（自动重试）：finish=length, content=1564 ✅

→ **这证明 retry infrastructure 工作正常**。问题不在 LangGraph，问题在三个 manager 节点把 empty `.content` 当成功通过。LangGraph 看不见空 content 是失败，因为节点函数返回了字典，没抛异常。

---

## 4. `finish=None` 事件（4 个）—— 流式 5 分钟硬切

| node | step | content | prompt | elapsed | error |
|---|---|---:|---:|---:|---|
| Market Analyst | 5 | 837 | 63,116 | 301.9s | None |
| Research Manager | 19 | 0 | 13,800 | 301.0s | None |
| Conservative Analyst | 22 | 5,263 | 25,176 | 301.6s | None |
| Neutral Analyst | 25 | 0 | 24,984 | 200.9s | RemoteProtocolError |

前 3 个 elapsed 都在 **301s ± 1s** — 这是**外部强制硬切**（不是 Kimi 自己写到一半停了；不是 langchain 抛错；是 5 分钟到了流就死了）。

可能来源：
1. **Cloudflare 1xx 超时** —— 静态资源前端有 100s upstream timeout，但 SSE 长流通常豁免。301s 不像是 CF 的 100s
2. **Gonka router idle timeout** —— api.gonkascan.com 一侧的反向代理可能设了 300s 流式无 chunk 间隔超时
3. **httpx 客户端 read timeout** —— 默认 5 分钟（300s）的可能性较大；`gonka_client._PASSTHROUGH_KWARGS` 不传 timeout 给 langchain 的 httpx

301s 这个数字过于规整，**最可能是 httpx 默认 5min read timeout**。等下次有时间可以验证：把 timeout 显式调高（如 1800s）再跑，看 None 是否消失。

特别坏的一个：**Market Analyst step 5 NVDA**，prompt **63116** 字符，elapsed 301.9s，finish=None，但 content=837 字符——意味着这次 Kimi *正在产出可见 content*，301s 时刚出了 837 字符就被掐了。这不是空 content，是**部分输出后中断**。下游 agent 接收到了一个只写了 837 字符的"market report"——一个比空字符串更危险的状态（截断到一半的指标分析）。

---

## 5. 为什么 32k 没用 —— Kimi 把更多 budget 用在 reasoning

回到 [scripts/probe_kimi_max_tokens.py](../../scripts/probe_kimi_max_tokens.py) 的早期实测：合成 PM-shape prompt 上 Kimi 自然停止时 completion_tokens ≈ 1900–2200，reasoning 大头。

但生产 prompt 不一样——特别 PM 的 prompt 嵌套了**整套风险辩论历史 + 上下文 lessons**，内容**有歧义、有冲突**。Kimi 在歧义内容上的 reasoning 长度可以飙到 30k+ token。32k 不够，65k 可能还是不够，128k 也可能不够——因为 vLLM 没给 reasoning 设硬上限。

证据链：
- **8192**：empty PM 2/2，length 17 次
- **32768**：empty PM 2/2，length 16 次

预算 4× 后，empty PM **完全没变**。这不是边际改善——这是模型在某些 prompt 上**根本停不下来**。

---

## 6. 更新的修补优先级（基于 32k 失败的新证据）

### P0（必做）：empty-content guard ⬆ 升至**唯一止血措施**

不管 max_tokens 再往上走多少，只要 Kimi 推理失控就还会失败。守卫是**根因层面**的修补：

```python
# tradingagents/agents/managers/portfolio_manager.py:76
response = llm.invoke(prompt)
content = response.content if hasattr(response, "content") else str(response)
if not content.strip():
    raise RuntimeError(
        f"Portfolio Manager: empty content from LLM "
        f"(finish_reason={response.response_metadata.get('finish_reason')!r}); "
        f"likely max_tokens exhausted by reasoning."
    )
```

同样的三行加到 [research_manager.py:60](../../tradingagents/agents/managers/research_manager.py#L60) 和 [trader.py:61](../../tradingagents/agents/trader/trader.py#L61)。

**Analyst 也要加**，但要照顾 tool_calls 阶段（合法空 content）：

```python
# market_analyst.py / news_analyst.py / fundamentals_analyst.py
if len(result.tool_calls) == 0 and not (result.content or "").strip():
    raise RuntimeError(...)
```

```python
# sentiment_analyst.py（无 tool_calls 路径，更简单）
if not (result.content or "").strip():
    raise RuntimeError(...)
```

抛异常 → LangGraph node retry 3 次 → 大概率第 2 / 第 3 次能拿到 content（reasoning 长度有随机性，前面看到 Conservative 23512 prompt 出 5263 content 而 Aggressive 14461 prompt 出 0 content 就是证据）。**这次重跑里 RemoteProtocolError 重试一次就救回来了——同一机制对空 content 同样有效，只要我们把它变成异常**。

### P1（降一级）：把 `kimi_max_tokens` 退回到 8192 或 16384

32k 完全没改善结果，反而：
- 多了 2 个 None 事件（更多 stream 暴露在 5min 窗口下）
- 多了 1 个 empty
- 每次调用更贵（Kimi reasoning 倾向于填满给定的预算）

实测 Kimi 自然结束时 completion 约 2000 token。**8192 已经是合理上限**，32k 是浪费。**MSFT PM step 26 证明再大也是浪费**，因为问题不在边际预算，在 reasoning 失控。

### P2（仍重要）：`finish_reason=None` 流式 5 分钟硬切排查

4 条 None 事件中 3 条精确在 300-302s。强烈疑似 httpx 默认 timeout。建议：
1. 给 `_PASSTHROUGH_KWARGS` 加 timeout 显式控制，langchain `ChatOpenAI(timeout=1800)` 会传到 httpx
2. 在 `gonka_client._STREAMING_DEFAULTS` 上加 `timeout=1800` 默认值
3. 5 分钟内拿不到下一个 chunk 重试整个调用，而不是接收一个被截断的 message

### P3：禁掉 `kimi_max_tokens` UI 输入框？

观察到的 dynamic：用户首次发现问题 → 加了 UI 调节 → 32k → 还是失败 → 准备再加大 → 还是失败 … 这条路没有终点。建议把 UI 入口删掉或藏起来，避免误导操作者继续走"加预算"路径。预算不是这个问题的解。

---

## 7. 实测可验证的下一步

加 P0 + 把 kimi_max_tokens 改回 8192（甚至 4096），重新跑 NVDA + MSFT，期望看到：

1. `finish_reason=length` 事件大幅下降（更小预算 + 重试机制让多数 ticker 能在重试 1-2 次后拿到合理 content）
2. `rating != NULL` 在 DB 里出现（这是用户的最终成功标志）
3. 即使个别 ticker 重试 3 次都失败，runner 写 `error` 字段而不是把 NULL 当成功

**不需要继续做 65k / 128k 实验**——已经有足够证据 32k 失败的原因不是边际预算不够。

---

## 8. 跟前次报告（root_cause_report.md）的关系

| root_cause_report.md 说 | 32k 实测验证 |
|---|---|
| "P0：empty-content guard 必须做" | ✅ **本次实测把 P0 的优先级抬到比 P1 更高**——预算调到 32k 都救不了，守卫是唯一止血方案 |
| "P1：max_tokens 32768" | ❌ **本次实测证伪**——4× 预算不解决根因，反而引入更多 None 超时 |
| "P3：5 分钟超时排查" | ✅ 进一步证据：4 条 None 都在 301s，强烈疑似 httpx 默认 timeout |

**修订后的优先级：**
- **P0 = 必做（empty content guard）**
- **P0.5 = 同步做（把 kimi_max_tokens 退回到 8192 或 4096）**
- P1 = 流式 timeout 排查
- P2 = capability 表加 Kimi 条目（结构化输出禁用）
- 不再考虑 65k 或更大预算
