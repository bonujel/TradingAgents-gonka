# Gonka 接入 + 吞吐优化笔记

> 分支:`gonka-tradeagents-kimi/v1`
> 范围:TradingAgents 多 agent 框架接入 Gonka,每天分析 S&P 500 入库 + Streamlit 展示。
> 这份文档合并了原 `gonka-integration.md`(实现细节)与 `gonka-throughput-analysis.md`(吞吐分析 + 实测)。

---

## 一、Gonka 提供的两条接入路径

| 维度       | SDK 路径(去中心化)                                | Router 路径(中心化代理)                      |
| ---------- | ------------------------------------------------- | -------------------------------------------- |
| 上游入口   | Gonka 网络节点(由 source URL 动态发现)           | `https://api.gonkascan.com/v1`               |
| 认证       | secp256k1 私钥逐请求 ECDSA 签名                   | `router.gonkascan.com` 发的 `sk-...` bearer  |
| 网关       | 直连节点                                          | 经 Cloudflare(100s 上游响应超时硬限)        |
| 依赖       | `gonka-openai` + `secp256k1` C 扩展               | 纯 `langchain-openai`,无 Gonka 专用 SDK     |
| 凭据       | `GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL`          | `GONKA_API_KEY`                              |
| 适用       | 主网账户、需要绕过 CF、需要多 TA fan-out          | 快速上手、Demo、原型                         |

两条路径**不是同一个 SDK 的两种调用方式**——Router 路径不依赖任何 Gonka 自家代码,本质就是个 OpenAI-兼容 HTTP 客户端。仓库里**唯一真正用到 `gonka-openai` SDK 的地方**是 `GonkaClient._build_sdk_llm()` 那十几行。

---

## 二、本仓库实现:`GonkaClient` 双模式自动调度

主文件:[`tradingagents/llm_clients/gonka_client.py`](../tradingagents/llm_clients/gonka_client.py)

### 2.1 选路规则

| 环境变量                                            | 走的路径   |
| --------------------------------------------------- | ---------- |
| `GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL` 都设了     | SDK        |
| 只设 `GONKA_API_KEY`                                | Router     |
| SDK 两个变量只设了一个                              | 明确报错指出缺哪个 |
| 一个都没设                                          | 报错列出所有可选组合 |

两条路径都返回 `GonkaStreamSafeChatOpenAI` 实例(继承自 `NormalizedChatOpenAI`),下游 LangGraph / 结构化输出 / tool-calling / capability 调度等代码零改动。

### 2.2 构造

- **SDK**:`resolve_and_select_endpoint(source_url=...)` → `(url, address)` → `gonka_http_client(private_key, transfer_address)` → `ChatOpenAI(base_url=url, http_client=<signed>)`。
- **Router**:`ChatOpenAI(base_url="https://api.gonkascan.com/v1", api_key=<bearer>)`,普通 langchain-openai 调用,无任何 Gonka SDK。

### 2.3 默认开 streaming

两条路径 build 时都默认 `streaming=True, stream_usage=True`。原因见第三节。调用方传 `streaming=False` 可关。

### 2.4 vLLM 空内容 fix

`GonkaStreamSafeChatOpenAI._get_request_payload` 出栈前检查 `role=assistant + tool_calls + content.strip() == ""` 的消息,把 content 改成 `None`(JSON `null`)。Streaming chunk aggregator 偶发把 `\n\n\n` 攒进这种 message 的 content,OpenAI 容忍但 vLLM 严格拒绝。

### 2.5 注册点

- `llm_clients/factory.py`:`provider == "gonka"` 单独 dispatch 到 `GonkaClient`(SDK 路径需要注入 `http_client` 且 base_url 动态)。
- `llm_clients/api_key_env.py`:`"gonka": None`(认证方式不是单一 API key env var)。
- `llm_clients/model_catalog.py`:`_GONKA_MODELS` 列出 `moonshotai/Kimi-K2.6` 和 `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`。
- `cli/utils.py`:交互式 provider 下拉里加 "Gonka",base_url 留 None 让客户端自己决定。

---

## 三、Gonka 架构(源码层面)

读 [`gonka/decentralized-api/internal/server/public/`](https://github.com/gonka-ai/gonka/tree/main/decentralized-api/internal/server/public)。

### 3.1 请求路径

```
client → TA(Transfer Agent) → Executor(另一个 mlnode) → vLLM
       POST /v1/chat/completions    forward /v1/chat/completions
```

TA 做:签名校验 → 估 prompt token → `bandwidthLimiter.CanAcceptRequest()` → 选 Executor → 转发请求 → `proxyResponse` 转回响应。**每个 TA 有独立的 `bandwidthLimiter`,超限直接返回 429**,错误信息直接提示"换一个 TA":

> `Transfer Agent capacity reached. Try another TA from <url>/v1/epochs/current/participants`
> —— `post_chat_handler.go:333`

**这暗示了多 TA fan-out 才是横向扩展的正解**。

### 3.2 流式 vs 非流式

`proxy.go`:

```go
if strings.HasPrefix(contentType, "text/event-stream") {
    proxyTextStreamResponse(...)   // bufio.Scanner + Fprintln,逐行刷
} else {
    proxyJsonResponse(...)         // io.ReadAll 全读完再 Write
}
```

**非流式响应 TA 全缓冲**:TCP 连接 100s 沉默 → Cloudflare 524。
**流式响应端到端透传**:第一 token 1-3 秒就到客户端,CF 全程不超时。

**这是单点最大的优化**:开 stream=true,CF 524 立刻消失,不依赖任何 Gonka 配置变更。

### 3.3 参与者发现

```
GET /v1/participants                   → 所有 participants(InferenceUrl + VotingPower)
GET /v1/epochs/current/participants    → 当前 epoch 的活跃 participants(带 Merkle proof)
```

`gonka-openai` 的 `resolve_endpoints(source_url=...)` 打第二个,默认 `random.choice` 随机挑一个 TA 钉到客户端整个生命周期。要 fan-out 得**多构造几个客户端**,每个钉到不同 TA(或自定义 `endpoint_selection_strategy=`)。

### 3.4 跨仓事实

`gonka/dev_notes/sp500-tradingagents-gonka-plan.md`:

> Gonka 主网当前对外推理模型是 `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`,**Kimi K2.6 还没上线**。

→ Router 上"能选 Kimi"实际是路由到 Gonka 网络**外**的 Moonshot 后端(也解释了为什么 Kimi-on-router 比 Qwen 慢一个量级,且 SDK 路径救不了它)。**真正用 Gonka 算力的 = Qwen3-235B**。

---

## 四、吞吐优化与实测

### 4.1 已落地

| 改动                  | 实现                                                       | 需要私钥? |
| --------------------- | ---------------------------------------------------------- | --------- |
| Streaming             | `GonkaStreamSafeChatOpenAI` 默认 `streaming=True`          | 否        |
| Ticker 间并发         | `app/runner.py` `ThreadPoolExecutor` + `-j N` CLI          | 否        |
| vLLM 空内容兼容       | `_get_request_payload` 出栈前改 `content=None`              | 否        |

### 4.2 实测(router + Qwen3-235B)

**Run A** —— 单 ticker minimal config(NVDA / 1-analyst / 0-debate),只看 streaming 效果:

| 配置             | 调用数  | 524 | 重试 | 端到端  |
| ---------------- | ------- | --- | ---- | ------- |
| 非流式(基线)    | 9 + 1   | 1   | 1    | 4m54s   |
| **流式**         | 7       | 0   | 0    | **2m53s** |

→ 降 41%,524 归零。流式让 CF 始终看到字节在流,完全不触发"上游 100s 没响应"。

**Run B** —— 4 ticker 并行 + 完整默认配置(4 analyst + 1 轮辩论 + 1 轮风控):

```
$ python -m app.runner -j 4 NVDA AAPL MSFT GOOGL
```

| ticker | rating       | per-ticker | 报告字符数(final/market/sent/news/fund/invp/trader) |
| ------ | ------------ | ---------- | --------------------------------------------------- |
| NVDA   | Buy          | ~8m59s     | 1692 / 7186 / 6340 / 6602 / 6745 / 2661 / 593       |
| MSFT   | Buy          | ~9m28s     | 1422 / 9756 / 5727 / 5394 / 6022 / 1898 / 641       |
| GOOGL  | Hold         | ~10m24s    | 2010 / 8168 / 7464 / 6829 / 7767 / 2204 / 582       |
| AAPL   | Underweight  | ~13m56s    | 1697 / 7372 / 7480 / 6502 / 8659 / 2352 / 720       |

总耗时 **836.7s ≈ 13m57s**,4/4 成功,0 个 524,0 次重试。总时间 ≈ 最长单 ticker 任务 → 4 worker 近线性 scale。

**外推**:按 Run B 数据 + AAPL 最长 14 分钟,**20 只 S&P 500 大概 30 分钟跑完**,完全在交易日盘后批量调度窗口内。

### 4.3 待做(优先级递减)

| 改动                       | 预期效果                              | 需要私钥? | 备注 |
| -------------------------- | ------------------------------------- | --------- | ---- |
| SDK 多 TA fan-out          | K 个 TA ≈ K× 吞吐 + 绕开 Cloudflare   | **是**    | 真正"用满 Gonka",见 4.4 |
| LangGraph analyst 并行     | 单 ticker 提速 3-4×                   | 否        | 改 `trading_graph.py` + reducer |
| 直连 TA URL                | 减一跳延迟                            | **是**    | SDK fan-out 顺带 |

### 4.4 SDK fan-out 实现草图

`GonkaClient._build_sdk_llm` 当前每次 random.choice 一个固定 TA。改造:

```python
# 缓存 epoch 内的 TA 列表
self._tas = resolve_endpoints(source_url=...)
# 每次 build llm 取下一个 TA(round-robin)
ta = self._tas[self._counter % len(self._tas)]; self._counter += 1
http_client = gonka_http_client(private_key, transfer_address=ta.address)
return GonkaStreamSafeChatOpenAI(base_url=ta.url, http_client=http_client, ...)
```

跟 ticker-并发天然契合:**4 个 worker × 4 个 graph × 各自钉到不同 TA**。每个 worker 全程稳定一个 TA,既享 round-robin 又不在单请求层切换 client。

---

## 五、配置和运行

### 5.1 环境(推荐 conda)

```bash
curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh
bash /tmp/miniconda.sh -b -p ~/miniconda3
source ~/miniconda3/etc/profile.d/conda.sh
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda create -n tradingagents python=3.11 -y && conda activate tradingagents
pip install -e ".[app]"
```

纯 pip 装法:`apt install libsecp256k1-dev build-essential pkg-config` 后再 `pip install -e ".[app]"`。

### 5.2 `.env`(两条路径二选一)

**路径 A — Router(快上手)**:

```bash
GONKA_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
TRADINGAGENTS_QUICK_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
```

`app/runner.py` 在 `TRADINGAGENTS_LLM_PROVIDER` 没设时默认走这个组合,所以只设 `GONKA_API_KEY` 就能跑。

**路径 B — SDK(去中心化)**:

```bash
GONKA_PRIVATE_KEY=0x<64 hex>
GONKA_SOURCE_URL=https://<gonka-node>
TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
# 可选:GONKA_ENDPOINTS / GONKA_VERIFY_PROOF
```

**应用层可选**:

```bash
TRADINGAGENTS_APP_DB=/path/to/decisions.sqlite3     # 默认 ~/.tradingagents/app/decisions.sqlite3
TRADINGAGENTS_APP_MAX_WORKERS=4                      # 默认 1(串行)
SP500_TICKERS=NVDA,AAPL,MSFT                         # 覆盖默认 top-20
TRADINGAGENTS_APP_CRON_HOUR=16
TRADINGAGENTS_APP_CRON_MINUTE=30
TRADINGAGENTS_APP_TIMEZONE=America/New_York
TRADINGAGENTS_APP_RUN_ON_START=false
```

### 5.3 跑

```bash
python -m app.runner NVDA                  # 单 ticker
python -m app.runner -j 4 NVDA AAPL MSFT GOOGL  # 4 并发
python -m app.runner                       # 默认 top-20
python -m app.scheduler                    # 调度(前台,Ctrl-C 退)
streamlit run app/dashboard.py             # 看板(默认 :8501)
```

---

## 六、已知限制 / 走不通的路径

### 6.1 Kimi-on-router 实测不可用(2026-05-14)

router + `moonshotai/Kimi-K2.6` + streaming,完整 default 配置,`python -m app.runner -j 4 NVDA AAPL MSFT GOOGL`:

| Ticker | 结果         | 单 ticker | 错误                                                  |
| ------ | ------------ | --------- | ----------------------------------------------------- |
| NVDA   | ❌           | ~1m37s    | `RemoteProtocolError: peer closed connection`         |
| MSFT   | ❌           | ~1m37s    | 同上                                                  |
| GOOGL  | ❌           | ~1m37s    | 同上                                                  |
| AAPL   | ✅ Underweight | ~50 min  | 中间过 502,SDK 重试救回                              |

**总耗时 50m6s,1/4 成功**。对比 Qwen3 同配置 13m57s / 4-of-4 → Kimi 慢 3.5×、可靠率 25%。

同一天还试过的几个失败 mode(都集中在 ~90s-12min 范围内随机出一种):

| 错误                                          | 触发条件                                |
| --------------------------------------------- | --------------------------------------- |
| `winner stalled waiting for next chunk after 1m30s` | 干净 streaming(没加 extra_body)        |
| `peer closed connection (incomplete chunked read)`  | 同上,服务端不发错误消息直接 RST          |
| `502 Bad Gateway`                              | 同上,长跑过程中                         |
| `500 do_request_failed` (new-api 错误码)       | 加了 `chat_template_kwargs.enable_thinking=true` |

最后一项确认了 **router 不是薄代理,是 `new-api` 网关**——会规范化请求体,vLLM 私有扩展字段过不去,所以**客户端没有任何字段能开 reasoning streaming**(`enable_thinking` / `include_reasoning` / `reasoning: {...}` 全试不可行)。

跨仓事实(`gonka/dev_notes/sp500-tradingagents-gonka-plan.md` 早提过):**Kimi K2.6 当前根本没上 Gonka 主网**,router 上选 Kimi 实际路由到 Gonka 网络外的某个 Moonshot 后端,加上 new-api / Cloudflare / Gonka API 节点三层中间件,server-side 行为不可控。

**结论**:Kimi-on-router 不能上生产。等 Kimi 真上 Gonka + 拿到 SDK 路径再回来测。生产模型保持 Qwen3-235B。代码侧已经把当初为 Kimi 试的 `extra_body` 注入机制回滚干净。

### 6.2 SDK 直连路径 — 普通用户拿不到白名单

详见 [`gonka-sdk-direct-attempt.md`](./gonka-sdk-direct-attempt.md)。简短版:`GonkaClient._build_sdk_llm` 这条代码路径写得对,但 Gonka 主网的链上 `allowed_transfer_addresses` 只放了 7 个治理批准的 transfer agent 地址,**普通用户从私钥派生的地址不在白名单,节点 `403 Transfer Agent not allowed`**。所以 SDK 路径目前不是"私钥够了就能用",而是需要走治理流程。

`GonkaClient` 双模式调度的代码侧已经验证了:私钥+source URL 设全时确实切到 SDK 路径并完成签名构造、端点发现、HTTP 客户端组装——只是最后一步被链上白名单挡了。

### 6.3 未实现 / 未压测

- **多 TA fan-out**(见 §4.4 草图)——前置依赖 SDK 路径可用,现在卡在 6.2。
- **LangGraph 节点级并行**(单 ticker 内 4 analyst 同跑)——纯客户端改动,不依赖外部状态;ROI 比 fan-out 低。
- **20 只全跑 / `-j 8 / -j 16` 压测**——单 TA bandwidth cap 未压出过 429。
- **Streamlit dashboard** 没做真人验收。
- **没单测**——`GonkaStreamSafeChatOpenAI._get_request_payload` 这种 vLLM 兼容边界应该单测固化。
