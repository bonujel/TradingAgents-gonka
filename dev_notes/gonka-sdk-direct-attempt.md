# gonka-openai SDK 直连尝试 —— 走不通的根因记录

> 日期:2026-05-14
> 目标:绕过 router(`api.gonkascan.com`),用 `gonka-openai` SDK 路径(`GONKA_PRIVATE_KEY` + `GONKA_SOURCE_URL`)签名直连 Gonka 节点,跑通 NVDA 单 ticker。模型:`moonshotai/Kimi-K2.6`。
> 结论先行:**普通用户拿到的 secp256k1 私钥派生地址不在链上 `allowed_transfer_addresses` 白名单里,节点会以 `403 Transfer Agent not allowed` 拒收。SDK 路径只对治理批准的 7 个 transfer agent 地址开放。**

---

## 一、起手配置

```bash
GONKA_PRIVATE_KEY=0x7ca593f248fbff190ad7de781c8e653ab20ad61bb85a28139d6e10f295466481
GONKA_SOURCE_URL=https://node4.gonka.ai
TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=moonshotai/Kimi-K2.6
TRADINGAGENTS_QUICK_THINK_LLM=moonshotai/Kimi-K2.6
```

`GonkaClient` 选路规则在 [`gonka_client.py:196-199`](../tradingagents/llm_clients/gonka_client.py):同时设了 PRIVATE_KEY + SOURCE_URL → 优先走 SDK。

---

## 二、踩坑过程(共 4 道关卡)

### 关卡 1:`SOURCE_URL=node4.gonka.ai` → `IndexError: empty sequence`

```
GET https://node4.gonka.ai/chain-api/productscience/inference/inference/params
→ 404 Not Found
→ gonka_openai/utils.py:154 random.choice([]) → IndexError
```

**原因**:`node4.gonka.ai` 根目录返回 HTML(网页),不是 chain RPC 节点。SDK 在 endpoint discovery 第一步(参与者列表 + transfer agent 白名单)就拉不到任何数据,`endpoint_list` 为空。

**解法**:暴力探测常见域名,找到真正暴露 `chain-api/` 的节点:

```bash
for url in api rpc node1..node5 mainnet; do
  curl -o /dev/null -w "%{http_code}" "https://$url.gonka.ai/chain-api/productscience/inference/inference/params"
done
# → 只有 node3.gonka.ai 返回 200
```

改 `GONKA_SOURCE_URL=https://node3.gonka.ai`。

### 关卡 2:`POST http://node1.gonka.ai:8000 → 308 Permanent Redirect`

node3 chain-api 拉到了 55 个活跃 participants,SDK `random.choice` 选中:

```
http://node1.gonka.ai:8000/v1/chat/completions
→ 308 'please use https://node4.gonka.ai/v1/ base url'
→ openai SDK 不跟随 POST 重定向 → APIStatusError
```

**原因**:链上 participant 注册数据里 `node1.gonka.ai:8000` 是老入口,实际已迁到 `https://node4.gonka.ai/v1/`,节点用 308 提示客户端;但 openai-python 客户端默认不跟随重定向 POST。
**有趣副产物**:从 308 响应体反向确认 **`https://node4.gonka.ai/v1/models` 返回 Qwen3-235B + moonshotai/Kimi-K2.6** —— Kimi K2.6 已经在 Gonka 主网上线(`gonka-integration.md` 第 109 行的说法已过时)。

**解法**:用 `GONKA_ENDPOINTS` 跳过随机选择,锁定一个支持 Kimi 的真实参与者节点。从链上拉支持 Kimi 的参与者列表(55 个中有 23 个声明 `moonshotai/Kimi-K2.6`):

```bash
curl https://node3.gonka.ai/v1/epochs/current/participants | \
  jq '.active_participants.participants[]
       | select(.models[]? == "moonshotai/Kimi-K2.6")
       | "\(.inference_url);\(.index)"'
```

挑了第一个:`http://89.149.242.149:8000;gonka10mmdjau4dnj8krs7sh7t7635ttnmq9u3vqgz09`。

### 关卡 3:`POST .../chat/completions → 405 Not Allowed`

环境变量改完直接 405。看实际请求 URL:

```
POST http://89.149.242.149:8000/chat/completions ← 注意:没 /v1
```

**原因**:[`gonka_openai/utils.py:79-95`](../../miniconda3/envs/tradingagents/lib/python3.11/site-packages/gonka_openai/utils.py):`get_endpoints_from_env_or_default` 走环境变量分支时**不调用 `_ensure_v1`**,URL 原样使用;只有从 chain-api 自动发现的 URL 才会被规范化加 `/v1`。

**解法**:`GONKA_ENDPOINTS` 里手动写 `/v1`:

```bash
GONKA_ENDPOINTS=http://89.149.242.149:8000/v1;gonka10mmdjau4dnj8krs7sh7t7635ttnmq9u3vqgz09
```

### 关卡 4(终极拦路):`POST .../v1/chat/completions → 403 'Transfer Agent not allowed'`

URL 终于对了,SDK 也签了名,节点的链层鉴权拒收:

```
openai.PermissionDeniedError: Error code: 403 - {'error': 'Transfer Agent not allowed'}
```

**根本原因**:Gonka 节点检查请求签名身份是否在 `transfer_agent_access_params.allowed_transfer_addresses` 白名单里。从我们的 `GONKA_PRIVATE_KEY` 派生 cosmos 地址:

```
你的 gonka 地址: gonka1f7zenm8wun48m48xc4yzz6uh72w03un3x4eh9f
链上 allowed_transfer_addresses 总数: 7
你在白名单内: False
```

7 个白名单地址前 5 个:

```
gonka10fynmy2npvdvew0vj2288gz8ljfvmjs35lat8n
gonka1ddswmmmn38esxegjf6qw36mt4aqyw6etvysy5x
gonka1dkl4mah5erqggvhqkpc8j3qs5tyuetgdy552cp
gonka1gndhek2h2y5849wf6tmw6gnw9qn4vysgljed0u
gonka1kx9mca3xm8u8ypzfuhmxey66u0ufxhs7nm6wc5
```

派生方法:

```python
from ecdsa import SigningKey, SECP256k1
import hashlib
from bech32 import bech32_encode, convertbits

sk = SigningKey.from_string(bytes.fromhex(pk_hex), curve=SECP256k1)
pk = sk.get_verifying_key().to_string('compressed')
ripemd = hashlib.new('ripemd160', hashlib.sha256(pk).digest()).digest()
addr = bech32_encode('gonka', convertbits(ripemd, 8, 5))
```

---

## 三、为什么会这样设计:Router 路径的存在意义

Gonka 的付费推理流程要求**每个请求都有一个能代付推理费用的链上地址签名**。allowed_transfer_addresses 是治理参数,只有 7 个地址被授权。

| 路径 | 谁签名 | 谁付费 |
|---|---|---|
| SDK 直连 | 用户私钥 | 用户地址必须在白名单 → 普通用户拿不到 |
| Router | `router.gonkascan.com` 后台持有的白名单私钥 | router 统一代付,通过 `sk-...` bearer 区分用户做记账 |

所以 router 不是"为了简化"才存在的代理 —— 它是**让链上白名单门槛对外不可见**的付费层。任何普通 Gonka 钱包都无法直接走 SDK 路径,这是设计约束不是 bug。

---

## 四、可用 vs 不可用模型(2026-05-14 实测)

`https://node4.gonka.ai/v1/models` 列出:

```
Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
moonshotai/Kimi-K2.6
```

55 个活跃 participants 中:
- 23 个声明支持 `moonshotai/Kimi-K2.6`(含 Qwen)
- 30+ 个只跑 Qwen
- 极少数 models=None(可能是 PoC 计算节点不提供推理)

**Kimi 现在确实在 Gonka 主网上**,`gonka-integration.md:109` 旧结论已作废。但因为 SDK 路径被白名单卡住,**普通用户用 Kimi 仍然只能走 router**。

---

## 五、最终选择 & 操作建议

| 场景 | 配置 | 备注 |
|---|---|---|
| 普通用户跑业务 | router + Kimi 或 Qwen | 注释掉 PRIVATE_KEY/SOURCE_URL/ENDPOINTS,保留 GONKA_API_KEY |
| 复现 CF 524 | router + Kimi + `stream=False` | router 路径 + 长生成 + 关 streaming → CF 100s 上游超时硬限 |
| 走 SDK | **不可行**,除非你拥有 7 个白名单地址之一的私钥 | 模型选什么都一样,403 在签名鉴权层,跟模型无关 |

实测数据(对比 router 路径,见 `gonka-integration.md` 第 91 行起):
- SDK 路径**对普通用户根本走不到推理调用**(403 在第一次 POST 就返回,5s 内结束)
- router + Qwen:NVDA 单 ticker 9 分 3 秒,27 次 API 调用全 200
- router + Kimi:慢约 4 倍,中途仍可能因生成过长触发 CF 524(单次推理 >100s)

---

## 六、给后续接手者的核对清单

如果将来 Gonka 治理放开了白名单(`allowed_transfer_addresses` 数量明显增多 / 公开 onboarding 流程):

1. 用上述派生脚本算出你的 gonka 地址
2. `curl https://node3.gonka.ai/chain-api/productscience/inference/inference/params | jq '.params.transfer_agent_access_params.allowed_transfer_addresses'` 确认你的地址在其中
3. `GONKA_ENDPOINTS` 里手动加 `/v1`(SDK 不会自动加,关卡 3 那条 bug 没修)
4. 从 `https://node3.gonka.ai/v1/epochs/current/participants` 挑一个支持目标模型且 `inference_url` 不是 `node1.gonka.ai:8000` 老入口的 participant

完成上面四步 SDK 才能真正发起付费推理请求。
