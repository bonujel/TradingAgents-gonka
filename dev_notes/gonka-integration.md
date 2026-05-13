# Gonka 接入实现笔记

> 分支：`gonka-tradeagents-kimi/v1`
> 范围：把 TradingAgents 多 agent 框架接到 Gonka 去中心化 LLM 平台，
> 每日跑 S&P 500 入库 + Streamlit 展示。

---

## 一、Gonka 提供的两条路径

Gonka 同时暴露**两套接入方式**，凭据、协议、运维特性都不一样：

### 1. 官方 SDK（去中心化路径）

仓库：`gonka-ai/gonka-openai`，PyPI：`gonka-openai`。

- 客户端用 ECDSA secp256k1 私钥**对每个请求体签名**，签名+地址放
  `Authorization` / `X-Requester-Address` / `X-Timestamp` 三个 header。
- 上游 endpoint 不是写死的，而是从一个 `source_url`（Gonka 网络节点）
  动态发现（`resolve_and_select_endpoint(source_url=...)`）。
- 直连节点，不经过 Cloudflare。
- 需要的依赖含 C 扩展 `secp256k1`，conda 环境下有预编译 wheel；
  纯 pip 在 Debian/Ubuntu 上得先 `apt install libsecp256k1-dev`。

### 2. Router（centralised path）

`https://api.gonkascan.com/v1`，由 `router.gonkascan.com` 这个
dashboard 签发的 `sk-...` bearer token 认证。

- 接口完全是 OpenAI-compatible chat completions：直接拿 `openai` Python
  SDK 或者 `langchain_openai.ChatOpenAI` 指向这个 base_url 就能用。
- 后面是 Cloudflare → Gonka 路由层 → 网络节点。Cloudflare 帮你把
  ECDSA 签名做了，所以客户端只要带 bearer token。
- **重要约束**：Cloudflare 默认 100s 上游响应超时（HTTP 524）。
  推理慢的模型 + 长 prompt 会被切。

> 这两条路径**不是同一个 SDK 的两种调用方式**——它们是两个完全不同
> 的传输层。Router 不需要任何 Gonka SDK，理论上你拿 `curl` 也能用。
> SDK 只用在去中心化路径上。

---

## 二、本仓库的实现：`GonkaClient` 双模式自动调度

接入文件：[`tradingagents/llm_clients/gonka_client.py`](../tradingagents/llm_clients/gonka_client.py)

`GonkaClient.get_llm()` 根据环境变量自动选路：

| 环境变量组合                                       | 走的路径   |
| -------------------------------------------------- | ---------- |
| `GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL` 都设了    | SDK 路径   |
| 只设了 `GONKA_API_KEY`                             | Router 路径 |
| SDK 两个变量只设了一个                             | 明确报错（提示缺哪个） |
| 一个都没设                                         | 报错列出所有可选组合 |

两条路径最终都返回 `NormalizedChatOpenAI` 实例（与其他 OpenAI-兼容
provider 共用同一个壳），所以 LangGraph / 结构化输出 / tool-calling /
capability 调度等下游代码**零改动**。

- **SDK 模式构造**：调 `resolve_and_select_endpoint(source_url=...)`
  拿到 `(url, address)`，用 `gonka_http_client(private_key, transfer_address)`
  生成签名 http client，把这个 client 通过 `http_client=` 注入
  `ChatOpenAI`。
- **Router 模式构造**：`ChatOpenAI(base_url="https://api.gonkascan.com/v1",
  api_key=<bearer>)`。这条路径**没用任何 Gonka 自己的 SDK**——
  完全是标准的 langchain-openai 调用，只是 base_url 指向了 Gonka 的
  router。

> 用户初次提问的 "router 手写的 SDK" 这个说法不太准确——router
> 路径既没有手搓 HTTP，也不依赖 `gonka-openai`，就是个普通的
> OpenAI-compatible HTTP 客户端。整个仓库里**唯一真正用到 gonka-openai
> SDK 的地方**就是 `GonkaClient._build_sdk_llm()` 那十几行。

### 注册过的地方

- `tradingagents/llm_clients/factory.py`：`provider == "gonka"` 单独
  dispatch 到 `GonkaClient`（不和 `_OPENAI_COMPATIBLE` 那批共用，
  因为 SDK 路径要注入 `http_client`，base_url 也是动态的）。
- `tradingagents/llm_clients/api_key_env.py`：`"gonka": None`（gonka 不
  通过 "给一个 API key 环境变量" 的模型来认证；私钥/token 都是
  `GonkaClient` 自己读环境）。
- `tradingagents/llm_clients/model_catalog.py`：加了 `_GONKA_MODELS`
  字典，列出 `moonshotai/Kimi-K2.6` 和
  `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8` 两个 ID（取自
  `GET /v1/models` 的返回）。
- `cli/utils.py`：交互式 provider 下拉里加了 "Gonka"，`base_url`
  传 `None` 让 `GonkaClient` 自己决定。

---

## 三、第一次 E2E 测试：Kimi vs Qwen3 调用耗时

测试条件：1 个 analyst（market）、`max_debate_rounds=0`、
`max_risk_discuss_rounds=0`、`news_article_limit=3`，NVDA / 2026-05-13，
走 router 路径（bearer token）。日志取自实际运行。

### Kimi-K2.6（推理模型）—— 失败

| # | 时间戳        | 间隔   | 状态                  |
| - | ------------- | ------ | --------------------- |
| 1 | 17:12:13      | -      | 200 OK                |
| 2 | 17:12:45      | +32s   | 200 OK                |
| 3 | 17:14:51      | +2m6s  | **524**（CF 超时）    |
| 4 | 17:16:57      | +2m6s  | **524**（自动重试也超）|

第 3 次调用开始 prompt 里塞进了 analyst 的工具调用结果，Kimi 推理时间
超 100s 被 Cloudflare 切。重试同样 prompt 同样超。**Kimi-K2.6 走 router
路径在 TradingAgents 这种长上下文场景下基本跑不通**——这是 Cloudflare
网关超时 vs 推理模型固有延迟的结构冲突，跟 prompt 怎么压缩关系不大
（试过最小配置照样超）。

### Qwen3-235B-Instruct（非推理 instruct）—— 成功

| #  | 时间戳    | 间隔   | 状态              |
| -- | --------- | ------ | ----------------- |
| 1  | 17:22:36  | -      | 200 OK            |
| 2  | 17:22:54  | +18s   | 200 OK            |
| 3  | 17:23:03  | +9s    | 200 OK            |
| 4  | 17:23:10  | +7s    | 200 OK            |
| 5  | 17:23:58  | +47s   | 200 OK            |
| 6  | 17:24:44  | +47s   | 200 OK            |
| 7  | 17:26:50  | +2m5s  | **524**（CF 超时）|
| 7' | 17:26:59  | +8s    | 200 OK（重试恢复）|
| 8  | 17:27:03  | +4s    | 200 OK            |
| 9  | 17:27:23  | +20s   | 200 OK            |
| 10 | 17:27:30  | +7s    | 200 OK            |

整条 pipeline 从首次 HTTP 到最后入库总耗时 **4 分 54 秒**（17:22:36 →
17:27:30），单次调用中位数 ~9-20 秒，两次落在 ~47 秒的较长一段（大概
率是 market analyst 整合工具结果 + research manager 判定）。中间一次
524 被 openai SDK 的内置重试 8 秒内救回来了。

最终入库：

```
rating         = Buy
final_decision = 1294 字符（PM 结构化输出）
market_report  = 5783 字符
trader_plan    = 528 字符
investment_plan= 1345 字符
```

### 结论

- **Router 路径 + Kimi-K2.6**：实测跑不通。哪怕最简配置也会超
  Cloudflare 100s。仅适合 short prompt 的单轮调用。
- **Router 路径 + Qwen3-235B-Instruct**：能跑通，但偶尔会有单次 524，
  靠 openai SDK 自动重试兜底。`app/runner.py` 因此默认用 Qwen3。
- **SDK 路径**：理论上绕过 Cloudflare，Kimi 应该可用。**未实测**
  （手上没有 secp256k1 私钥 + Gonka 节点 source URL）。

---

## 四、跑通需要的配置

### 4.1 环境

#### 推荐：conda

```bash
# 安装 miniconda（如已有可跳过）
curl -fsSL -o /tmp/miniconda.sh \
  https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh
bash /tmp/miniconda.sh -b -p ~/miniconda3
source ~/miniconda3/etc/profile.d/conda.sh

# 接受 channel TOS（首次需要）
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r

# 创建并激活环境
conda create -n tradingagents python=3.11 -y
conda activate tradingagents

# 装项目 + app 额外依赖（含 gonka-openai、streamlit、apscheduler）
cd <repo>
pip install -e ".[app]"
```

#### 备选：纯 pip（需要 libsecp256k1）

```bash
sudo apt-get install -y libsecp256k1-dev build-essential pkg-config
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app]"
```

### 4.2 `.env` 配置

把 `.env.example` 复制成 `.env` 后，**两条路径二选一**：

#### 路径 A：Router（最快上手，推荐用 Qwen3）

```bash
# Gonka router bearer token（router.gonkascan.com dashboard 签发）
GONKA_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 用 router 时强烈建议用 Qwen3-Instruct，避免 Kimi 触发 CF 100s 超时
TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
TRADINGAGENTS_QUICK_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
```

> `app/runner.py` 在 `TRADINGAGENTS_LLM_PROVIDER` 没设时会自动默认
> 走这个组合，所以只要设了 `GONKA_API_KEY` 就能跑。上面三个
> `TRADINGAGENTS_*` 是显式声明，建议留下方便 audit。

#### 路径 B：SDK（去中心化，可用 Kimi）

```bash
GONKA_PRIVATE_KEY=0x<64 hex chars>             # secp256k1 私钥
GONKA_SOURCE_URL=https://<gonka-node>          # 用来发现 endpoint 的网络节点

TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=moonshotai/Kimi-K2.6
TRADINGAGENTS_QUICK_THINK_LLM=moonshotai/Kimi-K2.6

# 可选
# GONKA_ENDPOINTS=https://node1:9000;gonka1addr,https://node2:9000;gonka2addr
# GONKA_VERIFY_PROOF=1
```

> `GonkaClient` 看到 `GONKA_PRIVATE_KEY` 和 `GONKA_SOURCE_URL` 同时
> 存在就自动切到 SDK 路径，不需要额外开关。

#### 应用层可选

```bash
# 自定义 SQLite 路径（默认 ~/.tradingagents/app/decisions.sqlite3）
TRADINGAGENTS_APP_DB=/path/to/decisions.sqlite3

# 覆盖默认 top-20 列表
SP500_TICKERS=NVDA,AAPL,MSFT,GOOGL,AMZN

# Scheduler 默认：周一-周五 16:30 America/New_York
TRADINGAGENTS_APP_CRON_HOUR=16
TRADINGAGENTS_APP_CRON_MINUTE=30
TRADINGAGENTS_APP_CRON_DOW=mon-fri
TRADINGAGENTS_APP_TIMEZONE=America/New_York
TRADINGAGENTS_APP_TOP_N=20
TRADINGAGENTS_APP_RUN_ON_START=false
```

### 4.3 跑

```bash
# 单 ticker 一次性跑（5 分钟左右 / 只）
python -m app.runner NVDA

# 默认 top-20 全跑
python -m app.runner

# 起 scheduler（前台进程，Ctrl-C 退出）
python -m app.scheduler

# 起 dashboard（默认 http://localhost:8501）
streamlit run app/dashboard.py

# 跑远程 / docker 等场景，外面要能访问就开 0.0.0.0
streamlit run app/dashboard.py \
  --server.address 0.0.0.0 --server.port 8501 --server.headless true
```

---

## 五、未完成 / 已知问题

- **没在 SDK 路径下做过 E2E**——没拿到测试私钥 + source URL。
  理论上 SDK 绕过 Cloudflare，Kimi 应该能跑，需要拿到凭据后验证一下。
- **批量 20 只全跑**没实测。按 ~5 分钟/只估，约 1.7 小时跑完一轮，
  调用次数约 200。Router 上偶发 524 + 8s 重试，整体应该能容忍，但
  建议加并发上限和退避策略（现在是严格串行）。
- **Dashboard 还没真人验收**。
- 当前 commit 历史：分支建在 main (a5cb7cb) 上，单 commit
  `feat(gonka): dual-mode Gonka LLM client + daily S&P 500 app`。
