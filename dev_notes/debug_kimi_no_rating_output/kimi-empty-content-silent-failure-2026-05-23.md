# Kimi 空 content 静默失败 —— 复盘(2026-05-23 run 36)

**状态:** 已诊断;已部分修(`max_tokens` 8192 → 65536,**未 commit**);空内容守卫待补
**严重性:** 高 —— 100% 静默失败模式:Trader 和 PM 节点产出 `response.content = ""`,空串原样写入 DB,runner / graph 不报错,DB 行看起来"成功"但 rating / final_decision 完全缺失
**相关代码:**
- `tradingagents/agents/managers/portfolio_manager.py`
- `tradingagents/agents/trader/trader.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/llm_clients/gonka_client.py`(`_GENERATION_DEFAULTS`)
- `tradingagents/agents/utils/structured.py`(**已被删除**)
**相关 commit:**
- `e524168 refactor(agents): drop structured-output path; rely on robust free-text rating extraction` —— **本 bug 的引入点**:删掉了 `invoke_structured_or_freetext` 的空内容守卫,RM/Trader/PM 改用赤裸的 `response.content`
- `9c8dbcb feat(gonka): json_schema as the sole structured-output path (方案 X)` —— 上一轮尝试,触发了 vLLM 3072 token 截断
- `1b80945 fix(gonka): default max_tokens=8192 to fit reasoning models` —— 此前为同类问题做的预算调整(本次再次撞顶)
**相关复盘:** `dev_notes/json-mode-downgrade-overreach-2026-05-22.md`、`dev_notes/kimi-run-23-2026-05-20.md`

---

## 0. 一句话结论

`e524168` 把结构化输出路径 + `invoke_structured_or_freetext` 的"空响应硬失败"守卫一并删了,RM/Trader/PM 现在是赤裸的 `response.content` 读取;`max_tokens=8192` 在 Kimi-K2.6 长 prompt 下被 reasoning 阶段消耗殆尽,`finish_reason='length'` 触发时 `message.content = ""`,空串原样落库,graph / runner 全程**零异常零警告**。

---

## 1. 用户报告的现象

> "分析一下最新的日志,AVGO 已经跑完了,但是没有 Rating,但是我已经修了 rating 的解析了,你查一下为什么。此外 analyse 全为空。"

用户的直接观察:
1. AVGO 已经 `propagate complete`,DB 里也有这一行
2. `rating` 字段为空
3. 多个 analyst 字段也为空
4. 用户最近刚改了 rating 解析逻辑(`e524168` 的 `rating.py` 重写),理应能拿到 rating

---

## 2. 调查路径

### 2.1 第一现场:数据库

清掉昨天数据后(`decisions` 表清空过几次),run 36 是清空后的第一个新批跑。截至诊断时刻 DB 里只有这次跑出来的 3 行:

```sql
SELECT ticker, trade_date, rating,
       length(coalesce(final_decision,''))     AS final_len,
       length(coalesce(market_report,''))      AS mkt,
       length(coalesce(sentiment_report,''))   AS sent,
       length(coalesce(news_report,''))        AS news,
       length(coalesce(fundamentals_report,'')) AS fund,
       length(coalesce(investment_plan,''))    AS plan,
       length(coalesce(trader_plan,''))        AS trader,
       length(coalesce(error,''))              AS err
FROM decisions WHERE trade_date='2026-05-23' ORDER BY ticker;
```

结果:

| ticker | rating | final_len | mkt | sent | news | fund | plan | trader | err |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AVGO  | `<NULL>` | **0**  | 0    | 0    | 0    | 1116 | 796 | **0** | 0 |
| GOOGL | `<NULL>` | **0**  | 0    | 8250 | 0    | 1271 | 807 | **0** | 0 |
| MSFT  | `<NULL>` | **0**  | 0    | 5392 | 5221 | 0    | 464 | **0** | 0 |

**关键事实:**
1. `rating = NULL` —— 全部三行
2. `final_decision = 0 字符` —— 全部三行(空字符串落库)
3. `trader_plan = 0 字符` —— 全部三行
4. `investment_plan` 都非空(RM 都成功)
5. `error` 字段空 —— runner 没记任何错
6. analyst 字段四个一组缺得不一样:AVGO 只有 `fundamentals`,GOOGL 有 `sentiment + fundamentals`,MSFT 有 `sentiment + news`,**完全没有规律**

### 2.2 第一个修正:不是 rating parser 的锅

`final_decision` 是 PM 写入的字段。它是 0 字符 = 空字符串。`rating` 是从 `final_decision` 里提取的。**parser 再怎么强,输入是空串也提不出 rating。** 所以问题根本不在 `rating.py`,而在 `final_decision` 自身为空。

### 2.3 Runner 日志看 AVGO 是怎么跑的

`run_20260523_021813_70422.log` 里 AVGO 对应 `ticker_6`:

```
10:18:14,659  Starting AVGO on 2026-05-23 via gonka/moonshotai/Kimi-K2.6 (max_retries=3)
10:18:14,662  [AVGO] entering propagate (multi-agent debate + tools)
10:18:19,788  HTTP/1.1 200 OK
10:22:23,750  WARNING reddit: r/wallstreetbets · AVGO HTTP 403 Blocked
10:22:25,023  WARNING reddit: r/stocks · AVGO HTTP 403 Blocked
10:22:26,267  WARNING reddit: r/investing · AVGO HTTP 403 Blocked
10:22:28,498  HTTP 200
10:23:00,909  HTTP 200
... (大量 HTTP 200)
10:40:08,394  [AVGO] propagate complete on attempt 1
10:40:08,406  Starting TSLA ...
```

观察:
- 22 分钟跑完,全程 **HTTP 200**,**0 个 502 / 500**,**0 个 traceback**
- 唯一报错是 Reddit 抓取被 403 拦(无关问题,sentiment_analyst 内部 handle 了)
- `propagate complete on attempt 1` —— graph 自认为正常结束,没有触发 retry
- runner 立刻拿同一个 worker 跑下一个 ticker(TSLA),说明 ticker-level 也没异常

**没有任何信号告诉我 PM 或 Trader 失败了。** 这是个完美的静默失败。

### 2.4 看 e524168 改了什么 —— bug 的引入点

```
commit e524168
    refactor(agents): drop structured-output path; rely on robust free-text rating extraction
```

commit message 的动机本身是合理的(我引用其原文):

> "Gonka's vLLM caps json_schema completions at ~3072 tokens. On run 33,
> every Research Manager / Portfolio Manager / Trader structured attempt
> hit length-limit, the 2-attempt retry burned ~6000 wasted decode
> tokens per call, and the free-text fallback then ran anyway."

也就是:**json_schema 路径在 Gonka vLLM 上撞 3072 token 上限,反复重试浪费费用,fallback 都跑了**,所以索性整个砍掉结构化路径,让 RM/Trader/PM 直接走 free-text。

但同时被**一并删掉**的还有:
- `tradingagents/agents/schemas.py`(Pydantic 模型)
- **`tradingagents/agents/utils/structured.py`** —— 这里面有 `invoke_structured_or_freetext` 和它的空响应守卫
- `retry.py` 里 `"no usable content from structured output"` 这条 marker(被删除,因为对应的异常类型也删了)

**这些删除带走了"空响应硬失败"的整个机制。** 之前(本会话最初设计的)守卫是:

```python
# tradingagents/agents/utils/structured.py(已删除)
response = plain_llm.invoke(prompt)
content = response.content or ""
if len(content.strip()) < min_chars:
    raise StructuredOutputEmpty(
        f"{agent_name}: no usable content from structured output ..."
    )
return content
```

`StructuredOutputEmpty` 是 `ValueError` 子类,带固定 marker 字符串,被 `app/runner.py:_RETRYABLE_VALUE_ERROR_MARKERS` 识别为 retryable —— 触发 5/15/45s backoff 重试。

### 2.5 新代码长什么样 —— 三个节点的赤裸 invoke

#### Portfolio Manager(`portfolio_manager.py:76-77`)

```python
response = llm.invoke(prompt)
final_trade_decision = response.content if hasattr(response, "content") else str(response)

return {
    "risk_debate_state": new_risk_debate_state,
    "final_trade_decision": final_trade_decision,    # 空串 → 直接进 state
}
```

#### Trader(`trader.py:61-67`)

```python
response = llm.invoke(messages)
trader_plan = response.content if hasattr(response, "content") else str(response)

return {
    "messages": [AIMessage(content=trader_plan)],
    "trader_investment_plan": trader_plan,           # 空串 → 直接进 state
    "sender": name,
}
```

#### Research Manager(`research_manager.py:60-61`)

```python
response = llm.invoke(prompt)
investment_plan = response.content if hasattr(response, "content") else str(response)
# ...
```

**三个节点是完全相同的模式**:`invoke` → 读 `.content` → 写 state。**没有任何 `if not content.strip(): raise`、没有 `if len(content) < threshold: warn`、没有任何空内容守卫。** 空串原样穿透。

LangGraph 看到节点返回了一个字典,字段都填了(即使值是 `""`),就当节点正常结束;runner 看到 `ta.propagate(...)` 没抛异常,就标 `propagate complete on attempt 1` 并入库。

### 2.6 系统性模式

观察 DB 三行:**3/3 的 ticker 都是 RM 成功 + Trader 空 + PM 空**。

| ticker | RM (`investment_plan`) | Trader (`trader_plan`) | PM (`final_decision`) |
|---|---:|---:|---:|
| AVGO  | 796 ✅ | **0** ❌ | **0** ❌ |
| GOOGL | 807 ✅ | **0** ❌ | **0** ❌ |
| MSFT  | 464 ✅ | **0** ❌ | **0** ❌ |

这**不是随机的模型行为**,是高度系统性的:
- 影响范围与节点强相关(RM 全 OK,Trader + PM 全空)
- 跨三个不同 ticker 的概率重合 ~0
- 必须有一个**节点相关**的因子在驱动

---

## 3. 根因假说 —— Kimi reasoning 撑爆 max_tokens

### 3.1 Kimi-K2.6 响应形态

Kimi-K2.6 是 reasoning model,vLLM 返回的 OpenAI-spec body 里有两个字段:

```json
{
  "choices": [{
    "finish_reason": "stop|length|...",
    "message": {
      "content":   "...",     // 给用户的最终输出
      "reasoning": "...",     // 模型的内部思考(CoT)
      ...
    }
  }],
  "usage": { ... }
}
```

本会话早些时候我直接 probe 过 Kimi(`max_tokens=5` 那次),确认了响应形态有 `content` 和 `reasoning` 两个字段。

刚才我又做了一次 Trader-prompt 形态的 probe(短 prompt,~50 字 input):

- `finish_reason`: `"stop"`(正常停止)
- `content`:600 字符,完整的 `**Action**: Hold\n\n**Reasoning**: ...\n\nFINAL TRANSACTION PROPOSAL: **HOLD**`
- `reasoning`:2000 字符的内部思考(权衡 P/E、AI 周期、bull-bear 等)
- 总输出 ~2600 字符,**远低于 8192 上限,两个字段都填满**

**所以模型本身能力没问题。短 prompt 下表现正常。**

### 3.2 真实 PM / Trader prompt 的长度

PM 的 prompt(`portfolio_manager.py:36-74`)拼接了:
- 工具说明 + instrument context
- 5 档评级说明
- `research_plan` 全文(796 字符 / AVGO 这一次)
- `trader_plan` 全文(**本次为空** → prompt 出现 `Trader's transaction proposal: ****`)
- `past_context`(多日历史 lessons)
- **完整 risk debate history**(aggressive + neutral + conservative 三轮辩论合计可能上千 token)
- 严格输出格式要求

Trader prompt(`trader.py:28-58`)较短,但也包含整个 `investment_plan`。

RM prompt(`research_manager.py:28-58`)最短 —— 只 debate history + 一段 prefix,没有 nested 嵌套别人的输出。

**prompt 长度排序:PM > Trader > RM。** 与失败模式完美对齐。

### 3.3 finish_reason='length' + 空 content 的机制

reasoning model 在 vLLM 上的生成是:**reasoning 段 → 转折信号 → content 段**。两段共享 `max_tokens` 预算。当 reasoning 段长到把预算用尽时:

1. vLLM 在 reasoning 中段触发 `finish_reason='length'`
2. content 段**根本来不及开始**
3. `message.content = ""`(空字符串,不是 null)
4. **HTTP 仍返 200**(请求本身成功,只是 token 用完)
5. langchain `AIMessage.content` 映射为 `""`
6. `response.content if hasattr(response, "content") else str(response)` 取到 `""`
7. 空串原样进 state,落库

整条链路**没有异常**,所有 200,所有 `propagate complete`。

### 3.4 为什么 RM 不撞但 Trader/PM 撞

- RM 输入最短 + 任务最聚焦(就是给个 rating + rationale)→ reasoning 不需要太长 → 8192 够用
- Trader 输入加上 ~800 字 investment_plan → reasoning 翻倍 → 8192 临界 or 撑爆
- PM 输入再叠加 debate history + past context → reasoning 暴涨 → 8192 必爆

### 3.5 假说能解释的全部事实

| 观察事实 | 假说预测 |
|---|---|
| RM 都成功 | RM prompt 最短,8192 token 剩余够 content |
| Trader 全空(3/3) | Trader prompt 中等,临界值附近,reasoning 一旦稍长 content 即空 |
| PM 全空(3/3) | PM prompt 最长,几乎必爆 |
| HTTP 全部 200 | finish_reason='length' 不是 HTTP 错误 |
| runner 0 traceback | 200 + 空 content → langchain 不抛异常 |
| `propagate complete on attempt 1` | graph 节点返回字典(只是字段值是 `""`),被认为成功 |
| `error` 列空 | 同上,runner 没看到异常 |
| 跨 ticker 一致(3/3) | reasoning 撑爆与 ticker 内容无关,只与 prompt 长度有关 |

### 3.6 Analyst 字段缺失的零散模式 —— 同根因延伸

回顾前面那张表:

| ticker | market | sentiment | news | fundamentals |
|---|---:|---:|---:|---:|
| AVGO  | 0    | 0    | 0    | 1116 |
| GOOGL | 0    | 8250 | 0    | 1271 |
| MSFT  | 0    | 5392 | 5221 | 0    |

不同 ticker 缺失的字段不同。Analyst 节点是带 tool-call loop 的,每轮可能调多次 LLM(读工具 → 用工具数据生成报告)。每一轮 LLM 调用都可能在 reasoning 撑爆 → content 空。空 content 是否覆盖该 analyst 的最终 report,取决于 graph 节点的具体实现细节:

- 如果 analyst 节点的最后一次 LLM 调用空 content → report 写空
- 如果中间某次调用空 content,但最后一次成功 → report 仍非空
- 如果整个 loop 提前 break → 取决于 break 时 state 里的值

MSFT 的 `news_report = 5221` 证明:**模型并不是不能产出 5000+ 字 content,只是 reasoning 占走预算时就吐不出**。所以这是预算分配问题,不是能力问题。

---

## 4. 假说验证(限制说明)

理想的硬证据是抓一个 Trader/PM 调用的 `finish_reason` 值。这次没能拿到,原因是:

1. **runner log 不打响应体**(httpx INFO 级别只打 status line),`finish_reason` 看不到
2. **backend.log 不带 LLM 调用细节**(uvicorn 只打 HTTP 路由层)
3. **gonka_client 没有自定义日志钩子**记录 finish_reason

诊断时尝试**直接 probe Gonka**(curl `chat/completions`)来再现 Kimi 长 reasoning 的形态:

- 短 prompt + `max_tokens=8192` probe:正常 `finish_reason=stop`,content + reasoning 都有
- 长 prompt + `max_tokens=8192` probe **失败**:Gonka 后端今天本身不稳,Cloudflare 524 / 502 频繁,无法可靠取到一次完整的长 prompt 响应

所以**假说现在是"高度可信但未硬验证"**。验证手段在 §6 列出。

---

## 5. 已做的部分修补 —— `max_tokens` 8192 → 65536

### 5.1 改动内容(`tradingagents/llm_clients/gonka_client.py`)

```python
# 之前
_GENERATION_DEFAULTS = {"max_tokens": 8192}

# 现在
_GENERATION_DEFAULTS = {"max_tokens": 65536}
```

注释里追加了 run 36 的历史背景,说明 8192 在 Kimi 长 prompt 下不够、64k 给 reasoning ~60k 缓冲、Kimi-K2.6 128k context 内 64k 输出 + 30–50k 输入仍宽裕、非 reasoning 模型(Qwen)碰不到上限不受影响。

### 5.2 测试修正(`tests/test_gonka_client.py`)

两处硬编码 `assert llm.kwargs["max_tokens"] == 8192` → `65536`。这两个 test 没标 `@pytest.mark.unit`,所以平时 `-m unit` 跳过它们 —— 跑全集才会暴露。修完之后全集 7/7 + unit 集 150/150 全绿。

### 5.3 为什么这只是"部分"修补

**`max_tokens` 拉大降低了撞顶概率,但没消除根因**。仍然存在的问题:

1. **64k 也可能不够** —— 如果 Gonka 后端 vllm 的 `--max-model-len` 配置不到 64k,实际接受值会被 clamp。这次我没能从外侧 probe 出 Gonka 真实 ceiling(后端 524 / 502 阻挡了 probe)
2. **Kimi 偶发抖动仍可能吐空** —— 即使预算够,reasoning 偶尔生成不健康的状态,也可能产出空 content
3. **没有空内容守卫** —— 一旦再次空 content,问题仍然 100% 静默
4. **没有重试机制** —— 之前的 `StructuredOutputEmpty` retryable 路径被删除后没替代品

### 5.4 改动状态

- 在工作区:`tradingagents/llm_clients/gonka_client.py` + `tests/test_gonka_client.py`
- **未 commit**(本文档也未 commit)
- 测试已跑过通过

---

## 6. 待补的关键修补

### 6.1(P0)空内容守卫 —— 三个节点共享

在 RM / Trader / PM 三处加同样的检查:

```python
response = llm.invoke(prompt)
content = response.content if hasattr(response, "content") else str(response)
if not content.strip():
    raise RuntimeError(
        f"{agent_name}: empty content from LLM "
        "(likely max_tokens exhausted by reasoning)"
    )
return {..., "<field>": content, ...}
```

抛 `RuntimeError`(或更特定的子类)→ 触发 LangGraph 节点级 retry(906d315 加的 retry_policy 应当覆盖)→ 次轮重试因 reasoning 随机性大概率能拿到 content。如果连续 N 次仍空,正常逐级冒到 runner 硬失败。

**为什么要在三处都加,而不是包一层 helper**:`e524168` 删 `invoke_structured_or_freetext` 时把 helper 模块整个去掉了。再加回一个共享 helper 是改动稍大的方向。最小止血是每个节点加 3 行 if。

### 6.2(P1)`finish_reason` 日志

在 `gonka_client.py` 加 streaming/non-streaming 两条路径上的 hook,记录每次响应的 `finish_reason` + content/reasoning 长度:

```python
fr = response.response_metadata.get("finish_reason")
if fr in ("length", "content_filter"):
    logger.warning(
        "Kimi finish_reason=%s, content_len=%d, reasoning_len=%d, model=%s",
        fr, len(content), len(reasoning), self.model_name
    )
```

有了这个之后,§3 的假说就能被实测数据**确认或证伪**。即便不上线守卫,先上线日志、跑一批拿数据,也能让后续决策更扎实。

### 6.3(P1)小批验证 64k 是否真的够

挑 3–5 个之前撞过空 content 的 ticker(NVDA、MSFT、AAPL、GOOGL、AMZN、META、AVGO 都行),用 64k 默认跑一遍。重点看:

- `final_decision` 是否非空(主要指标)
- `trader_plan` 是否非空
- analyst 四个字段是否齐全
- 平均 token 消耗是否相对 8192 暴涨(估算成本影响)

如果还有空,优先级:
- 拉到 96k 或 128k 再试一次
- 如果仍空 → 不是预算问题,而是 vLLM 后端单点抖动或其它 Kimi 行为问题 → 转向 6.1 守卫 + retry 路线

### 6.4(P2)考虑分预算

vLLM 部分版本支持把 reasoning 预算和 content 预算分开:

```python
extra_body = {"max_reasoning_tokens": 48000}  # 假想参数
```

需要确认 Gonka 的 vllm 版本是否支持。如果支持,可以给 reasoning 设硬上限,保证 content 段有最低预算。这条复杂度较高,需要先确认后端 API,不是当下要做的。

### 6.5(P2)模型分工

短期可考虑给 Trader / PM 暂时换回非 reasoning 模型(如 Qwen3-235B-Instruct),只让 RM(或 deep model)走 Kimi。这样:

- Trader / PM 没有 reasoning 段,不会有 reasoning-exhaustion 问题
- RM 走 Kimi 充分利用其推理能力做评级判断

但这要求 deep model / quick model 配置层支持按节点切换,可能涉及 graph 改造。

---

## 7. 当前 batch 怎么办

诊断完成时:
- run 36 的 runner PID 70988 **已退出**(应该是跑完了 20 个 ticker)
- DB 里 3 行 5-23 数据(都是 rating=NULL、final_decision=空)
- 用户在诊断过程中已经**手动清空了 `decisions` 表**

所以当前没有遗留待清理的脏数据。下一步要么是:
1. 上线 64k(已改未 commit)+ 空内容守卫(待写),然后小批验证
2. 先只上 64k,跑一批看效果

---

## 8. 与之前复盘的差异点

`dev_notes/json-mode-downgrade-overreach-2026-05-22.md`(昨天的 bug)和这次的有什么关系?

| 维度 | 昨天的 bug | 今天的 bug |
|---|---|---|
| 触发点 | `_LearningStructuredRunnable` 在单次 None 时**进程级降级到 json_mode** | RM/Trader/PM 在 reasoning 撑爆时**空 content 静默落库** |
| 路径 | 结构化输出路径(`with_structured_output`) | free-text 路径(裸 `invoke`) |
| 守卫缺失类型 | 进程级缓存被单次事件错误污染 | 全局空内容守卫缺失 |
| 影响放大机制 | 进程级 `_BACKENDS_WITHOUT_TOOL_CALLING` set | 无 —— 每次都独立失败,但 100% 失败率 |
| 解决方向 | 区分单次 vs 结构性信号 | 重新加回空内容守卫 + 调大预算 |
| 当时已修了什么 | 无 —— 文档已记录,但 `64cd78e` 的逻辑未改;**`e524168` 直接砍掉了整条结构化路径,顺便也带走了这个 bug,但同时带走了空内容守卫** | 本次 `max_tokens` 已调,守卫还没加 |

可以看出:`e524168` 解决了昨天 bug 的根因(把整条路径删了),但**没意识到原路径里其实还包含一个有价值的"空响应硬失败"机制**,把它一并删了导致今天这个新的静默失败模式。

---

## 9. 教训(可写进 CLAUDE.md 或类似的工程笔记)

1. **删整条路径前,先盘点路径里附带的横切关注点**(在这个例子里:retry、空响应守卫、错误分类 marker)。这些往往不是路径本身的核心职责,容易跟着被一起删。
2. **静默失败是最危险的失败**。当一次重构把"硬失败"改成"返回空"(无论是出于"reduce noise"还是"simplify"动机),都应配套加观察点(日志 + 监控)。
3. **reasoning model 的 `max_tokens` 不能按非 reasoning model 的直觉设**。基准至少要按"prompt 长度 + reasoning 翻倍 + 期望 content 长度"估算。
4. **测试覆盖里有些 assert 没标 marker**(本次 `test_gonka_client.py` 那两条 `max_tokens == 8192`),靠 `-m unit` 跑会漏。要么补 marker,要么 CI 跑全集而不是只跑某个 marker。

---

## 10. 待办清单(供决策)

- [ ] 决定:`max_tokens=65536` 的改动是否 commit
- [ ] 决定:空内容守卫(§6.1)是否上线 —— 强烈建议加,几行代码
- [ ] 决定:`finish_reason` 日志(§6.2)是否加 —— 建议加,验证假说用
- [ ] 决定:小批验证(§6.3)
- [ ] (可选)本文档是否 commit 到 `dev_notes/`
