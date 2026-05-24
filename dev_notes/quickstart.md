# Quick Start

> 架构、实测数据与已知限制见 [gonka-integration.md](./gonka-integration.md)。

后端 + 前端通过 [`start.sh`](../start.sh) 一键启停；所有运行时配置（router
key、模型选择、worker 数等）通过 UI Settings 页面修改并落盘 SQLite。`.env`
仅在用 SDK 直连模式或 CI 场景下需要。

## 1. 前置条件

- Git、Python 3.11（Linux 或 macOS，x86_64 / aarch64）
- Node.js 18+（前端 Nuxt 3 用）
- [uv](https://docs.astral.sh/uv/)（包管理；用 `curl -LsSf https://astral.sh/uv/install.sh | sh` 安装）
- Gonka router API key (`sk-...`)，由 [router.gonkascan.com](https://router.gonkascan.com/dashboard) 申请

## 2. 安装

```bash
git clone -b gonka-tradeagents-kimi/v1 git@github.com:bonujel/TradingAgents-gonka.git
cd TradingAgents-gonka

# 后端：conda 环境 + uv 装依赖（conda 用于 secp256k1 等 C 扩展隔离；uv 用于解析 uv.lock）
curl -fsSL -o /tmp/mc.sh \
  https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-$(uname -m).sh
bash /tmp/mc.sh -b -p ~/miniconda3
source ~/miniconda3/etc/profile.d/conda.sh
conda create -n tradingagents python=3.11 -y && conda activate tradingagents
uv pip install -e ".[app]"

# 前端：一次性 npm install
cd frontend && npm install && cd ..
```

如纯 venv（无 conda），需自行预装 `libsecp256k1-dev build-essential pkg-config`，再
`uv pip install -e ".[app]"`。`start.sh` 默认 activate `CONDA_ENV=tradingagents`；用别的环境名/纯 venv 启动时通过 env var 覆盖（详见 §5）。

## 3. 启动

```bash
./start.sh
```

输出大致：

```
[start.sh] Stopping backend, frontend, and analysis subprocesses...
[start.sh] Starting backend on http://127.0.0.1:8000 ...
[start.sh] Starting frontend on http://127.0.0.1:3000 ...
[start.sh] Up. Open  http://127.0.0.1:3000
----------------------------------------------------------------
  Super-admin account
  username: admin
  password: <每次启动后端时随机生成、写在 log-back.log>
----------------------------------------------------------------
```

打开 http://127.0.0.1:3000 → 用上面打印的 `admin` 凭证登录。

`./start.sh` 同时负责 **kill 旧的 backend / frontend / 任何残留的 `app.runner` 子进程**，所以反复跑总是从干净状态启动。

### 停止

```bash
./start.sh stop
```

## 4. 首次配置（UI）

1. 登录后进 **Settings**：
   - **Connection mode**：选 *Router*（最简单）
   - **GONKA_API_KEY**：粘贴 router key
   - **Deep / Quick model**：默认 Qwen3-235B-Instruct（router 路径下唯一可用；Kimi-K2.6 当前生产不稳，详见 [gonka-integration.md §6.1](./gonka-integration.md)）
   - **Manual default workers**：4
   - 点 *Save settings*
2. 进 **Tasks** → 输入 ticker → *Run* → 进度在该页面跟。

## 5. 常用命令

| 用途 | 命令 |
|---|---|
| 启动 / 重启全栈 | `./start.sh` |
| 停止全栈 | `./start.sh stop` |
| 跟后端日志 | `tail -f log-back.log` |
| 跟前端日志 | `tail -f log-front.log` |
| 命令行单跑 ticker（不走 UI） | `python -m app.runner -j 4 NVDA AAPL` |
| 命令行跑默认 top-20 S&P 500 | `python -m app.runner -j 4` |
| 启动定时调度器（独立前台进程） | `python -m app.scheduler` |
| 自定义 conda env 名启动 | `CONDA_ENV=my-env ./start.sh` |
| 自定义端口启动 | `BACKEND_PORT=18000 FRONTEND_PORT=13000 ./start.sh` |

## 6. 数据库

默认路径 `~/.tradingagents/app/decisions.sqlite3`，可通过 `TRADINGAGENTS_APP_DB` 覆盖。`decisions` 表以 `(ticker, trade_date)` 为唯一键。

```bash
sqlite3 ~/.tradingagents/app/decisions.sqlite3 \
  'SELECT ticker, rating, created_at FROM decisions
   ORDER BY trade_date DESC, ticker ASC LIMIT 20;'
```

设置文件（包含 router key、模型、worker 数等）在
`~/.tradingagents/app/settings.json`。如需在另一台机器上"克隆"配置，直接拷贝该文件即可。

## 7. 故障排查

| 现象 | 处理 |
|---|---|
| `./start.sh` 报 "could not find conda" | 设 `CONDA_EXE` 或装到 `~/miniconda3` |
| `uv: command not found` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| 安装 `secp256k1` 失败 | 用 conda env，或装 `libsecp256k1-dev` |
| 前端 `Cannot find module` / 启动失败 | `cd frontend && rm -rf node_modules && npm install` |
| 后端端口被占 | `./start.sh stop` 强清；或换 `BACKEND_PORT=...` |
| 忘了 admin 密码 | `grep -A4 "Super-admin account" log-back.log` |
| 调用 Kimi-K2.6 报错 / 评级为空 | router 路径下当前不稳；切回 Qwen3。诊断见 [daily-2026-05-25.md](./daily-2026-05-25.md) |
| `HTTP 524` / `peer closed connection` | 后端把 streaming 关了；恢复默认 `streaming=True` |
