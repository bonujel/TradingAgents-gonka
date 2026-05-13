# Quick Start

> 给新接手这个项目的同学:跟着跑完这五步就能起来。
> 实现细节 / 实测数据 / 常见坑见 [gonka-integration.md](./gonka-integration.md)。

---

## 0. 前置条件

- Git + Linux/macOS(aarch64 / x86_64 都行)
- 一个 Gonka **router API key**(`sk-...`),在 [router.gonkascan.com](https://router.gonkascan.com/dashboard) dashboard 自助申请

## 1. 装环境(一次性,~5 分钟)

```bash
git clone -b gonka-tradeagents-kimi/v1 git@github.com:bonujel/TradingAgents-gonka.git
cd TradingAgents-gonka

# 推荐 conda:secp256k1 / numpy 等 C 扩展有预编译 wheel,
# 省得装系统级 libsecp256k1-dev / build-essential。
curl -fsSL -o /tmp/mc.sh \
  https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh
bash /tmp/mc.sh -b -p ~/miniconda3
source ~/miniconda3/etc/profile.d/conda.sh
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda create -n tradingagents python=3.11 -y && conda activate tradingagents

pip install -e ".[app]"
```

> 不想用 conda?
> `apt install libsecp256k1-dev build-essential pkg-config` 后用 venv + pip 也行。

## 2. 配 `.env`

```bash
cp .env.example .env
# 在 .env 里至少加这两行(其它默认值都已在 .env.example 里):
GONKA_API_KEY=sk-你的router-key
TRADINGAGENTS_APP_MAX_WORKERS=4
```

模型不用单独配 —— `app/runner.py` 在 `TRADINGAGENTS_LLM_PROVIDER` 没设时
自动默认走 `gonka` provider + `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`,
streaming 默认开,是验证过的稳定组合。
要换模型 / 切到 SDK 路径见 [gonka-integration.md §5.2](./gonka-integration.md)。

## 3. 端到端验证(~5 分钟)

```bash
conda activate tradingagents
python -m app.runner NVDA          # 单 ticker,跑通 + 入库
```

成功标志:终端最后两行是

```
[NVDA] propagate complete
[2026-05-13] NVDA -> {'ticker': 'NVDA', 'ok': True, 'rating': 'Buy'}
```

## 4. 日常使用

```bash
# 跑批:默认 top-20 S&P 500,4 worker 并行(约 30 分钟)
python -m app.runner -j 4

# 调度器:周一-周五 16:30 America/New_York 自动跑,前台进程,Ctrl-C 退
python -m app.scheduler

# 看板:浏览器开 http://localhost:8501
streamlit run app/dashboard.py
```

## 5. 查数据库

SQLite 默认落在 `~/.tradingagents/app/decisions.sqlite3`,
按 `(ticker, trade_date)` 唯一。直接 SQL 查:

```bash
sqlite3 ~/.tradingagents/app/decisions.sqlite3 \
  'select ticker, rating, created_at from decisions
   order by trade_date desc, ticker asc;'
```

要换路径:`export TRADINGAGENTS_APP_DB=/your/path.sqlite3`。

---

## 遇到问题排查方向

| 现象                              | 看哪里                              |
| --------------------------------- | ----------------------------------- |
| 装 `secp256k1` 报错               | 用 conda 装,或装 `libsecp256k1-dev` |
| 跑起来卡很久没输出                | 正常,单 ticker ~5-15 分钟,看进度日志 |
| HTTP 524 错误                     | router 上 CF 100s 超时;确认 streaming 没被关 + 用 Qwen3 不要用 Kimi |
| `messages[i].content must not be empty` | 老版本 bug,确认 commit ≥ `85b1ccd` |
| 想换 Kimi-K2.6                    | 暂时别换,见 [gonka-integration.md §3.4](./gonka-integration.md) |

更详细的诊断 / 架构 / 优化路径见 [gonka-integration.md](./gonka-integration.md);
今日工作汇总见 [daily-2026-05-13.md](./daily-2026-05-13.md)。
