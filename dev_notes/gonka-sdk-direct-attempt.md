# gonka-openai SDK 直连 —— 走通的正确姿势

> 日期:2026-05-15(修订 2026-05-14 版本)
> 目标:绕过 router(`api.gonkascan.com`),用 `gonka-openai` SDK 路径(`GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL`)签名直连 Gonka 推理网关,跑通 NVDA 单 ticker。模型:`moonshotai/Kimi-K2.6`。
>
> **结论先行**:SDK 直连**对普通用户可行**。前一版结论"403 → SDK 路径只对 7 个治理白名单地址开放"是把"用户签名身份"和"transfer-agent 身份"混成了一件事;按官方 quickstart 的 *public inference gateway* 模式,**用户私钥负责签名+付费,网关(`node4.gonka.ai` 等)的链上白名单地址负责满足 transfer-agent 校验**——两件事,不是一件。

---

## 一、核心模型(以前理解错的那一刀)

Gonka 推理请求在签名校验时有两个独立身份:

| 身份 | 由谁提供 | 校验什么 |
|---|---|---|
| **签名者 (signer / payer)** | `GONKA_PRIVATE_KEY` 派生的 secp256k1 公钥 + bech32 地址 | 谁付推理费用、扣谁的余额 |
| **Transfer Agent (TA)** | 请求体里携带的 `transfer_address` 字段 | 必须命中链上 `transfer_agent_access_params.allowed_transfer_addresses` 白名单(7 个治理批准的地址) |

错误做法是把签名者地址直接当作 TA(我们之前就这么干的——`GONKA_ENDPOINTS=<url>;<我们自己派生的地址>`),自然 403。

**正确做法**:TA 用网关自己的白名单地址。`node4.gonka.ai` 这个 public inference gateway 自带白名单地址 `gonka1kx9mca3xm8u8ypzfuhmxey66u0ufxhs7nm6wc5`,通过 `GET https://node4.gonka.ai/v1/identity` 就能查到:

```bash
$ curl -s https://node4.gonka.ai/v1/identity
{"data":{"address":"gonka1kx9mca3xm8u8ypzfuhmxey66u0ufxhs7nm6wc5","block":4075641,...},"signature":"..."}

$ curl -s https://node3.gonka.ai/chain-api/productscience/inference/inference/params \
  | jq '.params.transfer_agent_access_params.allowed_transfer_addresses'
[
  "gonka1y2a9p56kv044327uycmqdexl7zs82fs5ryv5le",
  "gonka1dkl4mah5erqggvhqkpc8j3qs5tyuetgdy552cp",
  "gonka1kx9mca3xm8u8ypzfuhmxey66u0ufxhs7nm6wc5",   # ← node4 在内
  "gonka1ddswmmmn38esxegjf6qw36mt4aqyw6etvysy5x",
  "gonka10fynmy2npvdvew0vj2288gz8ljfvmjs35lat8n",
  "gonka1v8gk5z7gcv72447yfcd2y8g78qk05yc4f3nk4w",
  "gonka1gndhek2h2y5849wf6tmw6gnw9qn4vysgljed0u"
]
```

普通用户**不需要**申请加入这 7 个地址的列表;借用某个网关的 TA 身份转发即可,真正的付费仍由你的私钥签名授权(扣你的余额、记你的账)。

---

## 二、可用的最小配置

`.env`:

```bash
GONKA_PRIVATE_KEY=0x<你的 secp256k1 私钥>
GONKA_SOURCE_URL=https://node4.gonka.ai
# 关键:不要设 GONKA_ENDPOINTS。设置会跳过 SDK 的 transfer-agent 解析逻辑。
TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=moonshotai/Kimi-K2.6
TRADINGAGENTS_QUICK_THINK_LLM=moonshotai/Kimi-K2.6
```

代码侧:`tradingagents/llm_clients/gonka_client.py:_build_sdk_llm` 已经改成走 `/v1/identity` 路径,核心调用:

```python
src = source_url.rstrip("/")                                    # https://node4.gonka.ai
transfer_address = httpx.get(f"{src}/v1/identity",
                             timeout=30).json()["data"]["address"]
http_client = gonka_http_client(
    private_key=private_key,
    transfer_address=transfer_address,                          # ← 网关的地址,不是用户的
)
ChatOpenAI(model=..., base_url=f"{src}/v1", http_client=http_client, api_key="gonka-network")
```

注意**没有**调用 `resolve_and_select_endpoint(source_url=...)`。SDK 的 `resolve_*` 实现需要 `{source}/chain-api/...` 暴露完整的参与者列表 + 白名单过滤,而 `node4.gonka.ai` 是面向用户的 gateway,根域名跑前端 HTML,不暴露 chain-api。要走 `resolve_*` 必须用 `node3.gonka.ai` 之类的 validator 节点,但官方 quickstart 推荐的就是 gateway 模式。

---

## 三、之前为什么走不通(四道坎复盘)

每一道坎都是"沿着错误的心智模型走出来的副作用",在新模型下要么不出现、要么有更简单的解法。

### 坎 1:`SOURCE_URL=node4.gonka.ai` → `IndexError: empty sequence`

老调用走 `resolve_and_select_endpoint`,先打 `{source}/chain-api/productscience/inference/inference/params` 拿白名单 → node4 不暴露 chain-api → 404 → endpoint 列表空。

**新模型下**:不走 `resolve_*`,改打 `/v1/identity`(这个网关一定有),问题消失。

### 坎 2:`POST .../v1/chat/completions → 308 Permanent Redirect`

老调用换 `SOURCE_URL=node3.gonka.ai` 后,`random.choice` 选中链上注册但已迁站的 participant,POST 撞 308 而 openai-python 默认不跟随。

**新模型下**:不再随机选 participant,固定打 gateway。

### 坎 3:`POST .../chat/completions → 405 Not Allowed`

老调用用 `GONKA_ENDPOINTS` 强制锁定一台 participant 时,SDK 的 `get_endpoints_from_env_or_default` 走环境变量分支会**跳过 `_ensure_v1` 规范化**——env 给的 URL 没 `/v1` 就照样发出去。

**新模型下**:不再用 `GONKA_ENDPOINTS`,我们自己拼 `{src}/v1`,问题消失。
> 注:这是 SDK 的真 bug,值得给上游提个 PR,但不影响我们使用。

### 坎 4:`POST .../v1/chat/completions → 403 Transfer Agent not allowed`

老调用把从 PRIVATE_KEY 派生出来的用户地址 `gonka1f7zenm8wun48m48xc4yzz6uh72w03un3x4eh9f` 当 TA 塞进 `GONKA_ENDPOINTS=...;<addr>`,该地址不在 7 个白名单里。

**新模型下**:TA 用网关地址(在白名单里),问题消失。这条不是治理门槛,是我们用错了字段。

---

## 四、可用模型(2026-05-15 实测)

`https://node4.gonka.ai/v1/models`:

```
Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
moonshotai/Kimi-K2.6
```

**Kimi-K2.6 在 Gonka 主网真上线了**,55 个活跃 participants 中 23 个声明支持 Kimi。`dev_notes/gonka-integration.md:109` 那句"Kimi K2.6 还没上线"已过时。

---

## 五、为什么 Router 路径仍然存在?

Router 不再是"白名单门槛的唯一出口"(那是上一版的错误判断)。它现在的定位更接近:

- **不想自己管私钥的用户**:router 用 `sk-*` bearer + 后台代签,UX 跟普通 OpenAI 兼容 API 一致;
- **被动迁移流量**:旧客户端拿到的可能就是 router URL。

SDK 直连 + gateway 模式则更接近"用 Gonka 算力但仍自管账户"的中间形态——签名/付费由你掌控,但仍然走 Cloudflare 前面的 gateway,所以 **Cloudflare 100s 上游超时(524)还是会撞**,跟走不走 router 关系不大。要"真正分摊到多 TA"必须自己做客户端层 fan-out(参考 `dev_notes/gonka-integration.md` 4.4 节草图)。

---

## 六、给 Gonka 团队的反馈(可选)

按重要性:

1. **`gonka-openai` SDK 文档**:把"public gateway 模式 vs validator 模式"显式分开。`fetch_node_identity` 和 `resolve_and_select_endpoint` 适用场景不一样,README 没写清楚。
2. **SDK 小 bug**:`utils.py:79-95` 的 `get_endpoints_from_env_or_default` env-override 分支应该也走一遍 `_ensure_v1`,否则 env 给的 URL 缺 `/v1` 时静默落到根路径拿 405。
3. **链上 participant 注册数据**:发现一些 participant 的 `inference_url` 已 308 重定向到 node4(老入口仍登记),建议网络层定期回收死链。

---

## 七、给后续接手者的核对清单(新版)

1. `.env` 只设 `GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL=https://node4.gonka.ai`,**不设** `GONKA_ENDPOINTS`。
2. 任何"403 Transfer Agent not allowed"先想:`transfer_address` 是不是写成了你自己的地址?应该是网关 `/v1/identity` 返回的那个。
3. 任何"IndexError empty sequence"先想:`source_url` 是 gateway 还是 validator?是 gateway 就别让代码走 `resolve_*`。
4. 任何 524 / 100s 卡死先想:是 streaming 关了吗?gateway 前面是 Cloudflare,上游响应间隔 >100s 必断。`tradingagents/llm_clients/gonka_client.py` 默认 `streaming=True` + `stream_usage=True` 就是为它准备的。
