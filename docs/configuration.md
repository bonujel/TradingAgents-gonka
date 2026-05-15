# Configuration

This document is a complete reference for every configuration knob the
system exposes: environment variables, settings file, dashboard fields,
and command-line flags. For a step-by-step first-time setup, see
[`dev_notes/quickstart.md`](../dev_notes/quickstart.md).

## 1. Configuration layers and precedence

The system reads configuration from four sources. They are listed below
from highest to lowest precedence:

1. **Process environment** (explicit `export VAR=...` or values passed
   to the runner subprocess by the dashboard).
2. **Dashboard settings file** at `~/.tradingagents/app/settings.json`,
   used by the dashboard's subprocess launcher to construct the runner's
   environment.
3. **Project `.env` file** at the repository root, loaded by the user's
   shell before launching either the CLI or the dashboard.
4. **Code defaults** in `tradingagents/default_config.py` and
   `app/runner.py`.

The dashboard's Settings page writes into layer 2. The CLI reads from
layers 1, 3, and 4 only.

## 2. Gonka connection

Exactly one of the two credential groups MUST be configured. The SDK
group takes precedence when both are set.

### 2.1 Router mode (centralised gateway, bearer token)

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `GONKA_API_KEY` | Yes | — | Bearer token issued by [router.gonkascan.com](https://router.gonkascan.com/dashboard). |

The router endpoint is hard-coded to `https://api.gonkascan.com/v1`.
This is the simplest path to get running but is fronted by Cloudflare
and the `new-api` gateway, both of which apply request normalisation
and inter-chunk timeouts.

### 2.2 SDK direct mode (decentralised, ECDSA signing)

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `GONKA_PRIVATE_KEY` | Yes | — | secp256k1 hex private key (`0x...` or bare hex). Signs every request; pays from the user's on-chain GNK balance. |
| `GONKA_SOURCE_URL`  | Yes | — | Public inference gateway URL, e.g. `https://node4.gonka.ai`. The client calls `GET {url}/v1/identity` to discover the gateway's whitelisted transfer-agent address. |
| `GONKA_ENDPOINTS`   | No  | — | Explicit `url;address[,url;address...]` override for endpoint discovery. **Setting this disables the `/v1/identity` discovery path and is not recommended** — the SDK's env-override branch does not normalise URLs, so missing `/v1` paths silently produce HTTP 405. |
| `GONKA_VERIFY_PROOF`| No  | unset | Set to `1` to enable ICS23 Merkle-proof verification during endpoint discovery. |

The SDK path requires the `gonka-openai` package and its `secp256k1`
C extension; both are installed by the `[app]` extra of this project.

### 2.3 Mode and model selection

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `TRADINGAGENTS_LLM_PROVIDER`   | `openai` in upstream; `gonka` is auto-selected by `app/runner.py` when unset | LLM provider identifier. Must be `gonka` to engage `GonkaClient`. |
| `TRADINGAGENTS_DEEP_THINK_LLM` | `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8` (in `app/runner.py`) | Model used by the Research Manager and Portfolio Manager. |
| `TRADINGAGENTS_QUICK_THINK_LLM`| `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8` | Model used by all four analysts, the bull/bear researchers, the trader, the three risk debaters, the reflector, and the signal processor. |
| `TRADINGAGENTS_LLM_BACKEND_URL`| Unset | Optional override of the LLM endpoint URL. Leave unset; `GonkaClient` derives the endpoint from the credential mode. |

Currently supported Gonka models:

* `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8` — recommended for production.
* `moonshotai/Kimi-K2.6` — slower and less reliable through the router;
  better when reached over the SDK path but still subject to inter-chunk
  timeouts on long reasoning passages.

## 3. Pipeline tuning

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS`   | `1` | Bull/Bear research debate rounds. Set `0` to skip the debate (used in minimal-configuration probes). |
| `TRADINGAGENTS_MAX_RISK_ROUNDS`     | `1` | Aggressive/Neutral/Conservative risk-debate rounds. |
| `TRADINGAGENTS_OUTPUT_LANGUAGE`     | `English` | Language of the analyst reports and final decision. Internal debates always run in English for reasoning quality. |
| `TRADINGAGENTS_CHECKPOINT_ENABLED`  | `false` | Persist LangGraph state per step so a crashed run can resume. |
| `TRADINGAGENTS_BENCHMARK_TICKER`    | unset | Override the per-region benchmark used in alpha calculations. Leave unset to use the suffix map (SPY for US tickers). |

## 4. Application layer

### 4.1 Storage paths

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `TRADINGAGENTS_APP_DB`        | `~/.tradingagents/app/decisions.sqlite3` | SQLite database for `decisions` and `run_log` tables. |
| `TRADINGAGENTS_RESULTS_DIR`   | `~/.tradingagents/logs`        | Per-ticker JSON state dumps written by `TradingAgentsGraph._log_state`. |
| `TRADINGAGENTS_CACHE_DIR`     | `~/.tradingagents/cache`       | yfinance / FinnHub caches and LangGraph checkpoint sqlite. |
| `TRADINGAGENTS_MEMORY_LOG_PATH`| `~/.tradingagents/memory/trading_memory.md` | Cross-run decision memory log injected into the PM prompt. |

The dashboard additionally writes:

* `~/.tradingagents/app/settings.json` — connection mode and credentials.
* `~/.tradingagents/app/active_tasks.json` — registry of active runs.
* `~/.tradingagents/app/logs/run_<timestamp>_<pid>.log` — per-run stdout.

### 4.2 Ticker universe

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `SP500_TICKERS` | unset | Comma-separated ticker override. When set, replaces the static top-20 list in `app/sp500.py`. Whitespace is trimmed; case is normalised to upper. |

### 4.3 Concurrency

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `TRADINGAGENTS_APP_MAX_WORKERS` | `1` (serial) | Number of concurrent ticker analyses. Each worker holds its own `TradingAgentsGraph`. Reasonable upper bound is `8`; beyond that, upstream rate limits dominate. |

The `-j N` flag on `python -m app.runner` overrides this variable for a
single invocation.

### 4.4 Scheduler

The standalone scheduler (`python -m app.scheduler`) reads:

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `TRADINGAGENTS_APP_CRON_HOUR`     | `16`             | Hour in the configured timezone. |
| `TRADINGAGENTS_APP_CRON_MINUTE`   | `30`             | Minute. |
| `TRADINGAGENTS_APP_CRON_DOW`      | `mon-fri`        | APScheduler day-of-week expression. |
| `TRADINGAGENTS_APP_TIMEZONE`      | `America/New_York` | IANA timezone identifier. |
| `TRADINGAGENTS_APP_TOP_N`         | `20`             | Number of S&P 500 constituents to analyse per run. Ignored when `SP500_TICKERS` is set. |
| `TRADINGAGENTS_APP_RUN_ON_START`  | `false`          | When truthy (`1`, `true`, `yes`, `on`), the scheduler kicks off one immediate run on startup in addition to the cron schedule. |

## 5. Dashboard settings file

The dashboard's Settings page reads and writes
`~/.tradingagents/app/settings.json`. Schema:

```json
{
  "mode": "router",
  "router_api_key": "sk-...",
  "sdk_private_key": "0x...",
  "sdk_source_url": "https://node4.gonka.ai",
  "deep_model":  "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
  "quick_model": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
  "max_workers": 4
}
```

Notes:

* `mode` is `"router"` or `"sdk"`. The opposite mode's credentials are
  preserved on disk so the operator can toggle without re-entering them.
* When the dashboard launches a runner subprocess, it constructs the
  child's environment by clearing all inherited Gonka variables and
  then setting only the variables for the active mode. This guarantees
  the runner uses the mode the operator selected, regardless of what
  was already in `os.environ`.
* The file is written via temp-file + atomic rename to avoid partial
  writes on concurrent saves.

## 6. Command-line surface

### 6.1 `python -m app.runner`

```
python -m app.runner [-j N] [TICKER ...]
```

| Flag / arg | Default | Description |
| ---------- | ------- | ----------- |
| `-j N`, `--workers N` | env or 1 | Concurrent ticker count. |
| `TICKER ...`          | top-20 SP500 | Tickers to analyse. |

Exit code is `0` when all tickers succeed, `1` otherwise.

### 6.2 `python -m app.scheduler`

Foreground scheduler; trap SIGINT/SIGTERM. No flags. All configuration
via the environment variables in §4.4.

### 6.3 `streamlit run app/dashboard.py`

Standard Streamlit invocation. For headless deployment (no browser
auto-open):

```bash
streamlit run app/dashboard.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
```

## 7. Example configurations

### 7.1 Minimum router setup

```bash
GONKA_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Everything else falls through to the documented defaults (Qwen3, serial,
top-20 SP500, weekday-16:30-ET cron).

### 7.2 Production SDK setup with 4-way concurrency

```bash
GONKA_PRIVATE_KEY=0x<your-64-hex-secp256k1-key>
GONKA_SOURCE_URL=https://node4.gonka.ai

TRADINGAGENTS_LLM_PROVIDER=gonka
TRADINGAGENTS_DEEP_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
TRADINGAGENTS_QUICK_THINK_LLM=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8

TRADINGAGENTS_APP_MAX_WORKERS=4
TRADINGAGENTS_APP_DB=/var/lib/tradingagents/decisions.sqlite3
```

### 7.3 Custom universe override (one-shot)

```bash
SP500_TICKERS=NVDA,AAPL,MSFT,GOOGL,AMZN python -m app.runner -j 4
```

## 8. Troubleshooting

| Symptom | Likely cause | Action |
| ------- | ------------ | ------ |
| `secp256k1` build failure during `pip install` | C toolchain missing | Use the conda environment, or `apt install libsecp256k1-dev build-essential pkg-config`. |
| `HTTP 524` from `api.gonkascan.com` | Streaming disabled, Cloudflare upstream timeout fired | Ensure `streaming` is not overridden to `False`; switch from Kimi to Qwen3. |
| `403 Transfer Agent not allowed` (SDK path) | `transfer_address` is your own derived address, not the gateway's | Unset `GONKA_ENDPOINTS`; let the client discover via `/v1/identity`. |
| `messages[i].content must not be empty` | Stale client without the streaming compat fix | Update to a commit at or after `85b1ccd`. |
| Dashboard Tasks page shows finished run as still active | Stale dashboard process holds an un-`wait()`-ed zombie | Restart `streamlit run`. The Tasks page is now zombie-aware and will not regress, but a stale process from before the fix may have leaked entries. |
| `do_request_failed` (HTTP 500) intermittently on router | `new-api` gateway upstream flake | Retry; if persistent, try a different model. Custom `extra_body` fields trigger this deterministically and should not be set on the router path. |
