# Quick Start

> 架构、实测数据与已知限制见 [gonka-integration.md](./gonka-integration.md)。

## 1. 前置条件

- Git 与 Python 3.11(Linux 或 macOS,aarch64 / x86_64 均支持)
- Gonka router API key(`sk-...`),由 [router.gonkascan.com](https://router.gonkascan.com/dashboard) 申请

## 2. 安装

```bash
git clone -b gonka-tradeagents-kimi/v1 git@github.com:bonujel/TradingAgents-gonka.git
cd TradingAgents-gonka

# Conda(推荐;避免 secp256k1 等 C 扩展的系统级构建依赖)
curl -fsSL -o /tmp/mc.sh \
  https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh
bash /tmp/mc.sh -b -p ~/miniconda3
source ~/miniconda3/etc/profile.d/conda.sh
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda create -n tradingagents python=3.11 -y && conda activate tradingagents

pip install -e ".[app]"
```

如使用 venv,需先安装系统依赖:`libsecp256k1-dev build-essential pkg-config`。

## 3. 配置

```bash
cp .env.example .env
```

最少需配置以下两项,其余可沿用默认:

```
GONKA_API_KEY=sk-...
TRADINGAGENTS_APP_MAX_WORKERS=4
```

未显式设置 `TRADINGAGENTS_LLM_PROVIDER` 时,`app/runner.py` 默认使用 `gonka` provider 与 `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`,streaming 默认开启。更换模型或启用 SDK 路径见 [gonka-integration.md §5.2](./gonka-integration.md)。

## 4. 端到端验证

```bash
conda activate tradingagents
python -m app.runner NVDA
```

预期输出包含 `[NVDA] propagate complete` 与 `{'ticker': 'NVDA', 'ok': True, ...}`,数据库写入一条记录。典型耗时 5-15 分钟。

## 5. 常用命令

| 用途                       | 命令                                          |
| -------------------------- | --------------------------------------------- |
| 跑默认 top-20 S&P 500      | `python -m app.runner -j 4`                   |
| 跑指定 ticker              | `python -m app.runner -j 4 NVDA AAPL ...`     |
| 启动调度器(前台进程)     | `python -m app.scheduler`                     |
| 启动看板(默认 `:8501`)   | `streamlit run app/dashboard.py`              |

## 6. 数据库

默认路径 `~/.tradingagents/app/decisions.sqlite3`,可通过 `TRADINGAGENTS_APP_DB` 覆盖。`decisions` 表以 `(ticker, trade_date)` 为唯一键。

```bash
sqlite3 ~/.tradingagents/app/decisions.sqlite3 \
  'SELECT ticker, rating, created_at FROM decisions
   ORDER BY trade_date DESC, ticker ASC;'
```

## 7. 故障排查

| 现象                                        | 处理                                                            |
| ------------------------------------------- | --------------------------------------------------------------- |
| 安装 `secp256k1` 失败                       | 使用 conda 环境,或安装 `libsecp256k1-dev`                       |
| 终端长时间无输出                            | 单 ticker 运行约 5-15 分钟,通过 INFO 级日志观察进度             |
| HTTP 524 / `peer closed connection`         | 确认 streaming 未被关闭;切换至 Qwen3                            |
| `messages[i].content must not be empty`     | 升级至 commit `85b1ccd` 或更新版本                              |
| 使用 Kimi-K2.6 失败                         | router 路径下不可用,详见 [gonka-integration.md §6.1](./gonka-integration.md) |
