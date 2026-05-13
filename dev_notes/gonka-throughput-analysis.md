# 怎样更充分利用 Gonka 的计算资源 —— 源码分析与优化路径

> 基础参考：[gonka-integration.md](./gonka-integration.md) 已经讲清当前实现。
> 本文回答"换种连接方式会更快吗？怎么更好利用 Gonka 的算力？"

---

## 0. 核心结论（先看这一段）

**会更快**。能堆叠的优化按"性价比/实现难度"排序：

| # | 改动 | 预期效果 | 工作量 | 需要私钥+source URL？ |
| - | --- | --- | --- | --- |
| 1 | **打开 streaming**（`stream=true`） | 直接干掉 Cloudflare 100s 524；Kimi 在 router 上也能跑 | 一行 `ChatOpenAI(streaming=True)` | 否 |
| 2 | **ticker 间并发** | 20 只票从 ~100 min 压到 ~10-15 min | 改 `app/runner.py` 加线程池 | 否 |
| 3 | **多 TA 扇出**（fan-out） | 每个 TA 有自己的 bandwidth limit，K 个 TA ≈ K× 吞吐 | 拓展 `GonkaClient` + 自定义 `endpoint_selection_strategy` | **是** |
| 4 | **直连 TA URL**（绕过 gonkascan 代理层） | 彻底脱离 Cloudflare；首字延迟 -1 跳 | 同 3 | **是** |
| 5 | **LangGraph 节点并行**（4 analyst 同时跑） | 单 ticker 提速 ~3-4× | 改 `trading_graph.py` | 否 |
| 6 | **不同 agent 用不同节点** | 同 3 的延伸：把 LLM 实例池化到 TA 池 | 中 | **是** |

短期最高 ROI：**1+2**（不需要私钥，今天就能做完，端到端能从 5min/只 ≈ 100min/20 只 压到 ≈ 10-15min/20 只）。
长期想"用满 Gonka"：**3+4** 是关键，要拿到 secp256k1 私钥 + Gonka 节点 source URL。

---

## 1. Gonka 的实际架构（源码层面）

### 1.1 请求路径

读 `gonka/decentralized-api/internal/server/public/post_chat_handler.go`：

```
client →  TA(Transfer Agent) → Executor(另一个 mlnode) → vLLM
       POST /v1/chat/completions    forward /v1/chat/completions
```

- 每个 chat 请求**必须先打到某个 TA**。TA 做这些事：签名校验 → 算 prompt token 估算 →
  调 `bandwidthLimiter.CanAcceptRequest(blockHeight, promptTokens, maxTokens)` →
  通过则用 `getExecutorForRequest()` 选一个 Executor →
  原封不动转发请求（POST 到 `executor.Url+"/v1/chat/completions"`），
  返回时 `proxyResponse` 把响应转回客户端。
- TA 也可能选中自己作为 Executor（`if s.configManager.GetApiConfig().PublicUrl == executor.Url`），
  这时本地处理省一跳。
- **关键：每个 TA 都有独立的 `bandwidthLimiter`，超限直接返回 429**：

  > `"Transfer Agent capacity reached. Try another TA from <url>/v1/epochs/current/participants"`
  > —— `post_chat_handler.go:333`

  这是 Gonka 内置的"换 TA 重试"提示。**单 TA = 单瓶颈**。

### 1.2 流式 vs 非流式（决定是否能绕过 Cloudflare 100s）

读 `gonka/decentralized-api/internal/server/public/proxy.go`：

```go
contentType := resp.Header.Get("Content-Type")
if strings.HasPrefix(contentType, "text/event-stream") {
    proxyTextStreamResponse(...)   // 逐行 bufio.Scanner + Fprintln，立刻刷出去
} else {
    proxyJsonResponse(...)         // io.ReadAll 全读完再 Write —— 完全缓冲
}
```

**非流式响应在 TA 这一层是全缓冲的**：TA 要等 Executor 把整段 JSON 读完才返回给客户端。
这就是为什么 Kimi-K2.6 100s 内出不来结果就被 CF 切——TCP 连接全程沉默。

**流式响应是端到端透传**：第一个 token（通常 1-3s 内）就刷到客户端，之后 CF 不会判定空闲超时。

> 这是单点最大的优化：**打开 stream=true**，CF 524 问题立刻消失，不依赖任何 Gonka 配置变更。

### 1.3 参与者发现 = 真正的并行入口

读 `decentralized-api/internal/server/public/server.go` + `get_participants_handler.go`：

```
GET /v1/participants                          → 所有 participants（含 InferenceUrl 和 VotingPower）
GET /v1/epochs/:epoch/participants            → 指定 epoch 的活跃 participants（带 Merkle proof）
GET /v1/epochs/current/participants           → 当前 epoch 的活跃 participants
```

`gonka-openai` SDK 的 `resolve_endpoints(source_url=...)` 就是去打第三个接口，
拿到 `[Endpoint(url, address), ...]`，然后默认 `random.choice(endpoints)` 随机挑一个：

```python
# gonka_openai/utils.py
def gonka_base_url(endpoints):
    return random.choice(endpoint_list)   # 默认策略
```

注意：**默认策略是每次构造客户端时随机选一个 TA，整个客户端生命周期都钉在这个 TA**——
也就是说一个 `ChatOpenAI` 实例对应一个 TA。要横向扩展，得**多构造几个客户端**，每个钉到不同 TA。

SDK 提供了自定义钩子：

```python
client = GonkaOpenAI(
    api_key="mock", gonka_private_key="0x...", endpoints=[...],
    endpoint_selection_strategy=lambda eps: eps[some_index],
)
```

这就是 fan-out 的接口面。

---

## 2. 三条连接方式的速度模型

设：
- `T_inf` = Executor 单次推理耗时（取决于模型+prompt+max_tokens）
- `T_hop` = 网络 + TA 转发开销（5-50ms 量级）
- `B` = 单 TA 的 bandwidth cap（请求/秒 或 token/秒）
- `K` = 并发请求数

### 方式 A：当前用法（router 非流式 + Qwen3）

- 总时间 ≈ `T_inf + T_hop + Cloudflare buffering`
- 受 CF 100s 上游响应超时硬限制：`T_inf > 100s` 必失败
- 单 TA（gonkascan 后端单点），`K > B` 时收到 429
- **Kimi K2.6 在 router 上几乎跑不通就是因为 `T_inf > 100s`**

### 方式 B：router 流式（最小改动）

- 总时间不变（仍是 `T_inf + T_hop`），但 **CF 不再 524**
- 仍是单 TA → 仍受 `B` 限制，但用 streaming 时 CF 会保持长连接
- 适用：今天就能上，不需要私钥；先解决 Kimi 跑不通的问题

### 方式 C：SDK 多 TA fan-out 流式

- K 路并发，每路绑不同 TA：吞吐 ≈ `min(K, |TA|) × per-TA-throughput`
- 直连 TA URL，无 CF：无 100s 上限
- **这是真正"用满 Gonka 算力"的形态**——大致是把 router 这个单点变成 K 个并行节点
- 实测开销：每次 `resolve_endpoints` 一次拉 TA 列表（一次性，可缓存到 epoch 切换前）

---

## 3. 与本仓库代码的具体对接

### 3.1 改动 1：让 `GonkaClient` 支持 streaming（router & SDK 都受益）

[`tradingagents/llm_clients/gonka_client.py`](../tradingagents/llm_clients/gonka_client.py)
两个 `_build_*_llm()` 里给 `NormalizedChatOpenAI` 加 `streaming=True, stream_usage=True`。

注意 langchain-openai 的 streaming 与 tool-calling 兼容性：v1.x 之后 OK，
但 `Responses API` 路径（仅用于 OpenAI 自家）和 chat completions 流式分开走，
Gonka 上是后者（`use_responses_api=False` 是默认），没问题。

`with_structured_output(method="function_calling")` 在 streaming=True 时
也能正确 aggregate tool_calls（实测要看 Qwen3 / Kimi 在 Gonka 返回的 chunk 是否合规，
建议先开 streaming + 跑一遍现有的 e2e 验证流。

### 3.2 改动 2：runner 里 ticker 间并发

[`app/runner.py:run_daily`](../app/runner.py) 现在是 `for ticker in tickers: run_one(...)`。
关键是**每个 worker 持有独立的 `TradingAgentsGraph` 实例**（共享 graph 没问题，
LangGraph 编译产物是无状态的，但 `propagate` 内部 state 在 `self.curr_state` 上变，
所以并发场景下要每个线程独立 ta）。

骨架：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
def run_daily(tickers=None, *, trade_date=None, top_n=20, max_workers=4):
    ...
    def worker(t):
        # 每个线程构造自己的 graph，避免 self.curr_state 竞争
        local_graph = TradingAgentsGraph(debug=False, config=config)
        return run_one(t, trade_date, graph=local_graph)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(worker, t): t for t in tickers}
        for fut in as_completed(futs):
            result = fut.result()
            ...
```

`max_workers` 上限取决于：
- Gonka 单 TA 的 bandwidth cap（不知道具体值，建议从 4 开始）
- yfinance 自身限速（连续高频拉数据会被限）
- 本地 CPU/内存（每个 LangGraph 实例若干 MB）

### 3.3 改动 3：多 TA 客户端池（SDK 路径）

`GonkaClient._build_sdk_llm()` 当前每次构造一个固定 TA 的客户端。改造点：

```python
# 在 GonkaClient 里持有一个 epoch-cached TA 列表
self._tas = resolve_endpoints(source_url=source_url)   # 缓存到下一个 epoch

# 每次 get_llm() 用 round-robin 选一个 TA
ta = self._tas[self._counter % len(self._tas)]; self._counter += 1
http_client = gonka_http_client(private_key=pk, transfer_address=ta.address)
return NormalizedChatOpenAI(base_url=ta.url, http_client=http_client, ...)
```

注意 `TradingAgentsGraph.__init__` 时调一次 `create_llm_client(...)` 然后**整个生命周期复用**
（`self.deep_thinking_llm` / `self.quick_thinking_llm`）。要每次 LLM 调用换 TA，要么：

- **方案 a**：构造 K 个不同 TA 的 `TradingAgentsGraph`，ticker 级 round-robin（最简单，
  和改动 2 自然契合）。
- **方案 b**：在 `NormalizedChatOpenAI` 层做 monkey-patch，让 `invoke()` 之前选 TA、
  把 `http_client` 切到对应的签名客户端。比较侵入。

方案 a 是更工程化的选择，和 ticker-并发天然对齐：4 个 worker，4 个 graph，4 个 TA。

### 3.4 改动 4：单 ticker 内的 analyst 并行（LangGraph 重构）

`tradingagents/graph/setup.py` 把四个 analyst 串成线性流水线。理论上 market /
sentiment / news / fundamentals 之间没数据依赖，可以做成 LangGraph 的并行分支
（`graph.add_edge("start", "market")` + `graph.add_edge("start", "sentiment")` …
最后汇到 `research`），LangGraph 并行执行后用 reducer 合并 state。

这是比较深的改动，可能要改 `AgentState` 的 reducer 定义。**ROI 比 ticker-并发低**
（单 ticker 4× vs 多 ticker K×），但叠加上去能再降一倍 walltime。**建议排在后面**。

---

## 4. 现实约束：跑这套需要什么

| 改动 | 前置 |
| --- | --- |
| 1（streaming） | 无 |
| 2（ticker 并发） | 无 |
| 3（多 TA fan-out） | `GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL`（Gonka 主网节点 URL） |
| 4（LangGraph 并行）| 无 |
| 5（直连 TA URL）  | 同 3（SDK 路径自动绕开 router） |

注意一个跨仓的事实，是 `gonka/dev_notes/sp500-tradingagents-gonka-plan.md` 里提到的：

> Gonka 主网当前对外推理模型是 `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`，
> **Kimi K2.6 在 Gonka 上还没上线**。

这意味着 router 上"能选 Kimi"其实是把请求路由到了 Gonka 网络**外**的某个 Moonshot 后端
（这也解释了为什么 Kimi-on-router 比 Qwen-on-router 慢一个数量级，且 SDK 路径基本帮不上忙）。
**真正落在 Gonka 算力上的模型目前只有 Qwen3-235B**。所以本文的"用满 Gonka 算力"
讨论都默认是 Qwen3-235B 这条路。

---

## 5. 建议的落地顺序

1. **本周**：实现 1（streaming）+ 2（ticker 并发），用 router + Qwen3 跑通 20 只票，
   全程目标 < 20 分钟。这一步**不需要私钥**。
2. **拿到私钥 + Gonka 节点 URL 之后**：实现 3 + 5（多 TA fan-out + 直连 TA），
   测真实主网下 K=4/8/16 的扩展曲线，找出 sweet spot。
3. **之后**：考虑 4（LangGraph analyst 并行），单 ticker 延迟再降一倍。

---

## 6. 实测结果（router + Qwen3-235B，streaming + ticker 并发已落地）

### 6.1 Run A — 单 ticker minimal config，仅看 streaming 效果

同样的最小配置（NVDA / 1-analyst / 0-debate / news_article_limit=3）：

| 配置                    | 调用数 | 524 | 重试 | 端到端耗时 |
| ----------------------- | ------ | --- | ---- | ---------- |
| 非流式（前一次基线）    | 9 + 1  | 1   | 1    | **4m54s**  |
| **流式（本次改动）**    | 7      | 0   | 0    | **2m53s**  |

降幅 **~41%**，且**没有任何 524**。原因：流式响应让 CF 始终看到字节在流，
完全不会触发"上游 100s 没响应"判定，所以单次卡顿不会再被切断重试。

### 6.2 Run B — 4 ticker 并行 + 完整 default 配置

`python -m app.runner -j 4 NVDA AAPL MSFT GOOGL`，**默认 4 个 analyst + 1 轮辩论 + 1 轮风控**：

```
ticker  rating       per-ticker time     full / market / sentiment / news / fund / invp / trader
NVDA    Buy          ~8m59s              1692 / 7186 / 6340 / 6602 / 6745 / 2661 / 593
MSFT    Buy          ~9m28s              1422 / 9756 / 5727 / 5394 / 6022 / 1898 / 641
GOOGL   Hold         ~10m24s             2010 / 8168 / 7464 / 6829 / 7767 / 2204 / 582
AAPL    Underweight  ~13m56s             1697 / 7372 / 7480 / 6502 / 8659 / 2352 / 720
```

`run_log`:

```
started_at  : 2026-05-13T10:58:39 (UTC)
finished_at : 2026-05-13T11:12:36
elapsed     : 836.7s ≈ 13m57s
success     : 4 / failure: 0
```

**等价的串行参考**：上面单 ticker minimal config 跑 2m53s，但 Run B 是完整配置
（4 analyst × ~3-5 调用 + 1 轮 bull/bear + research manager + trader + 3 个风控 +
PM），每 ticker 比 minimal 重 ~3-4×。如果 4 只票串行跑同样配置，估计需要 ~40 min。

→ 4-worker 并行 + streaming，全 default 配置下 **20 只 S&P 500 大致能在 ~30 min 跑完**
（按 AAPL 14 分钟最长任务的 5× 估算上限）。完全可放进交易日盘后批量调度的时间窗口。

### 6.3 副产品 fix:vLLM 空内容兼容性

打开 streaming 后第一次 e2e 失败在 `messages[4].content: must not be empty`。
根因是 LangChain 的 streaming chunk 聚合：当 assistant turn 只发 tool_calls 没文本，
偶尔会把若干 `\n` chunk 攒进 `content`，于是序列化成 `content="\n\n\n"`。
OpenAI 自家 API 容忍这个，Gonka 后端的 vLLM 严格要求"非空或 null"。

修复在 [`gonka_client.py`](../tradingagents/llm_clients/gonka_client.py)
的 `GonkaStreamSafeChatOpenAI._get_request_payload`：
出栈前检查 assistant + tool_calls + `content.strip() == ""` 的消息，
把 content 改成 `None`（openai SDK 序列化成 JSON `null`）。这个修复在
两种传输模式（router / SDK）都会生效，因为两条路径都用同一个
`NormalizedChatOpenAI` 壳。

### 6.4 仍未解决

- **Kimi-K2.6 没再测**——按 1.4 节的结论它在 Gonka 主网根本没上，router 上跑得通也只是绕到 Moonshot
  外部后端。等 Kimi 真上 Gonka 再补一次数据。
- **更高并发**（`-j 8` / `-j 16`）没测——单 TA 的 bandwidth cap 不知道实际数值，目前 `-j 4` 没
  压出来过 429。等扩到 20 只票 + `-j 8` 时再观察。
- **SDK 多 TA fan-out** 还没实现，等私钥到位。
