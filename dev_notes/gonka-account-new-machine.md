# 在另一台机器配置 Gonka 账户开发

> 目标:在一台新机器上复用同一个 `gonka...` 地址,让 TradingAgents 走 `gonka-openai` SDK 路径直接请求 Gonka 网络,而不是走 router `sk-...`。
>
> 参考:Gonka Developer Quickstart <https://gonka.ai/docs/developer/quickstart/>

## 0. 先理解两件事

Gonka 账户不绑定机器。账户由助记词 / 私钥控制,同一个账户可以导入到多台开发机。

不要把助记词、私钥、`.env` 提交到 git。开发建议使用小余额热钱包,主钱包或大额 GNK 放在 Keplr / Cosmostation / 单独安全机器里。

## 1. 准备代码和 Python 环境

新机器需要 Git、Python 3.11 和本仓库。推荐 conda,因为 `gonka-openai` 依赖 secp256k1 相关 C 扩展,conda / wheel 路径通常更省心。

```bash
git clone -b gonka-tradeagents-kimi/v1 git@github.com:bonujel/TradingAgents-gonka.git
cd TradingAgents-gonka

conda create -n tradingagents python=3.11 -y
conda activate tradingagents

pip install -e ".[app]"
```

如果 `pip install -e ".[app]"` 在 `secp256k1` 附近失败:

```bash
# macOS
brew install pkg-config secp256k1

# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libsecp256k1-dev

pip install -e ".[app]"
```

快速确认 SDK 依赖已装好:

```bash
python -c "import gonka_openai; print('gonka_openai ok')"
```

## 2. 下载 `inferenced` CLI

`inferenced` 用来导入 / 查看 Gonka 账户、查余额、发布公钥。官方 quickstart 的下载入口是 Gonka GitHub Release。

以下命令会按当前系统选择 release 资产。当前验证过的 release 是 `release/v0.2.12`;如果官方 quickstart 指向更新版本,把 `RELEASE` 改成新的 tag。

```bash
mkdir -p .gonka-tools

RELEASE="release/v0.2.12"
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin) OS_NAME="darwin" ;;
  Linux) OS_NAME="linux" ;;
  *) echo "Unsupported OS: $OS" && exit 1 ;;
esac

case "$ARCH" in
  arm64|aarch64) ARCH_NAME="arm64" ;;
  x86_64|amd64) ARCH_NAME="amd64" ;;
  *) echo "Unsupported arch: $ARCH" && exit 1 ;;
esac

ASSET="inferenced-${OS_NAME}-${ARCH_NAME}.zip"
URL="https://github.com/gonka-ai/gonka/releases/download/${RELEASE}/${ASSET}"

curl -L "$URL" -o ".gonka-tools/${ASSET}"
unzip -o ".gonka-tools/${ASSET}" -d .gonka-tools
chmod +x .gonka-tools/inferenced

.gonka-tools/inferenced --help
```

macOS 如果提示无法打开,到 System Settings -> Privacy & Security 里允许 `inferenced`,或在确认来源后执行:

```bash
xattr -d com.apple.quarantine .gonka-tools/inferenced
```

本仓库已经把 `.gonka-tools/` 加入 `.gitignore` 和 `.dockerignore`,不要把这个 200MB 左右的二进制工具提交或打进镜像。

## 3. 在新机器导入同一个 Gonka 地址

下面使用 `--keyring-backend file`,它会让你设置本机 keyring passphrase。我们显式指定 `--home ~/.inferenced`,避免不同 CLI 默认 home 目录造成混乱。

```bash
export ACCOUNT_NAME=tradingagents
export INFERENCED_HOME="$HOME/.inferenced"

.gonka-tools/inferenced keys add "$ACCOUNT_NAME" \
  --recover \
  --home "$INFERENCED_HOME" \
  --keyring-backend file
```

按提示输入你原账户的助记词,并设置这台机器本地 keyring 的 passphrase。

确认地址:

```bash
export GONKA_ADDRESS="$(
  .gonka-tools/inferenced keys show "$ACCOUNT_NAME" \
    --address \
    --home "$INFERENCED_HOME" \
    --keyring-backend file
)"

echo "$GONKA_ADDRESS"
```

输出应当是你原来的 `gonka...` 地址。如果不是,先停下来检查助记词 / derivation path,不要继续转账或导出私钥。

## 4. 设置节点变量并检查余额

`NODE_URL` 用来查链上状态和广播交易。可以选官方 genesis node 或当前 epoch 的 active participant。先用一个稳定公开节点:

```bash
export NODE_URL="http://node1.gonka.ai:8000"

.gonka-tools/inferenced query bank balances "$GONKA_ADDRESS" \
  --node "$NODE_URL/chain-rpc/"
```

如果账户没有 GNK,可以导入账户和配置环境,但不能真正完成去中心化推理结算。Gonka quickstart 说明 GNK 当前用于支付推理请求,获取方式包括 host rewards、bounty、社区转账等。

## 5. 确认或发布链上公钥

推理前账户需要有余额和链上公钥。先查询账户数据:

```bash
curl -s "$NODE_URL/v2/accounts/$GONKA_ADDRESS"
```

如果这个账户以前已经发布过公钥,通常不需要在新机器重复发布。导入同一个账户只是把私钥复制到本机 keyring,链上账户状态不变。

如果还没发布过公钥,并且账户里已有 GNK,执行:

```bash
.gonka-tools/inferenced publish-pubkey \
  --from "$ACCOUNT_NAME" \
  --home "$INFERENCED_HOME" \
  --keyring-backend file \
  --node "$NODE_URL/chain-rpc/" \
  --chain-id "gonka-mainnet" \
  --yes
```

如果账户是在外部钱包创建的,官方 quickstart 说也可以发任意一笔链上交易来发布公钥,自转账也可以。

## 6. 导出 SDK 需要的私钥

TradingAgents 的 SDK 路径需要 `GONKA_PRIVATE_KEY`。这一步会输出明文私钥,只在你自己的终端里执行,不要截图,不要贴到聊天,不要写进 shell history 以外的共享位置。

```bash
.gonka-tools/inferenced keys export "$ACCOUNT_NAME" \
  --home "$INFERENCED_HOME" \
  --keyring-backend file \
  --unarmored-hex \
  --unsafe
```

复制输出的 hex 私钥,后面写入项目 `.env`。如果输出不带 `0x`,在 `.env` 里加上 `0x` 前缀。

## 7. 修改项目 `.env`

从示例文件创建 `.env`:

```bash
cp .env.example .env
```

编辑 `.env`,保留 SDK 路径,注释掉 router key。建议最小配置如下:

```env
# Gonka SDK / crypto-native path
GONKA_PRIVATE_KEY=0x你的私钥
GONKA_SOURCE_URL=https://node4.gonka.ai

# 不走 router 时建议留空或注释,避免误判
#GONKA_API_KEY=

TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=moonshotai/Kimi-K2.6
TRADINGAGENTS_QUICK_THINK_LLM=moonshotai/Kimi-K2.6
TRADINGAGENTS_APP_MAX_WORKERS=4
```

说明:

- `GONKA_SOURCE_URL=https://node4.gonka.ai` 是官方 quickstart 当前推荐的 public inference gateway。
- 本仓库代码里,只要 `GONKA_PRIVATE_KEY + GONKA_SOURCE_URL` 都存在,就优先走 `gonka-openai` SDK 路径。
- 如果同时配置 `GONKA_API_KEY`,它会被 SDK 路径覆盖,但为了排查清晰,建议注释掉。
- `TRADINGAGENTS_LLM_PROVIDER` 只保留一行,不要重复。
- 如果 Kimi 跑得慢或报模型 / 节点可用性问题,先切 Qwen 跑通链路:

```env
TRADINGAGENTS_DEEP_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
TRADINGAGENTS_QUICK_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
```

## 8. 验证

先确认 `.env` 会被加载,且 `gonka_openai` 可 import:

```bash
conda activate tradingagents
python -c "import gonka_openai; print('sdk import ok')"
```

跑单 ticker 端到端:

```bash
python -m app.runner NVDA
```

成功标志类似:

```text
[NVDA] propagate complete
...
{'ticker': 'NVDA', 'ok': True, 'rating': 'Buy'}
```

日常运行:

```bash
# 默认 top-20 S&P 500,并发数由 .env 的 TRADINGAGENTS_APP_MAX_WORKERS 控制
python -m app.runner

# 手动指定并发和 ticker
python -m app.runner -j 4 NVDA AAPL MSFT GOOGL

# 看板(两进程:后端 + 前端)
uvicorn app.api:app --port 8000
cd frontend && npm install && npm run dev
```

看板地址:

```text
http://localhost:3000
```

SQLite 默认位置:

```bash
sqlite3 ~/.tradingagents/app/decisions.sqlite3 \
  'select ticker, rating from decisions order by trade_date desc limit 20;'
```

## 9. Docker 路径的额外注意

如果新机器用 Docker 跑 SDK 路径,当前 `Dockerfile` 需要安装 app extra,否则镜像里没有 `gonka-openai`:

```diff
-RUN pip install --no-cache-dir .
+RUN pip install --no-cache-dir ".[app]"
```

然后重新构建:

```bash
docker compose build --no-cache tradingagents
```

当前 `docker-compose.yml` 默认启动的是 `tradingagents` 交互式 CLI,不是 `app.runner` 或 Streamlit 看板。做应用开发时优先用本机 conda 环境;如果要把 runner / dashboard 都容器化,需要再给 compose 增加对应 command、ports 和 volume。

## 10. 常见问题

**`export` 要不要写进 `~/.zshrc`?**

`ACCOUNT_NAME`、`NODE_URL` 这种非敏感值可以写。`GONKA_PRIVATE_KEY` 不建议写 shell rc,放项目 `.env` 并确保 `.env` 被 git ignore。

**另一台机器是否要重新 `publish-pubkey`?**

不用。发布公钥是链上账户状态,不是机器状态。只要导入的是同一个地址,且之前已经发布过,新机器不需要重复发布。

**没有 GNK 可以做什么?**

可以导入账户、安装依赖、配置 `.env`。但发布公钥和实际推理一般需要账户有可用 GNK。

**如何确认现在走的是 SDK 不是 router?**

`.env` 里存在 `GONKA_PRIVATE_KEY` 和 `GONKA_SOURCE_URL` 时,本仓库 `GonkaClient` 会优先走 SDK。为了排查清楚,SDK 模式下把 `GONKA_API_KEY` 注释掉。
