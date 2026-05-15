# Requirements

This document specifies the functional and non-functional requirements for
the Gonka integration layer and the operator-facing dashboard built on top
of the upstream **TradingAgents** multi-agent framework.

## 1. Scope and goals

Run the TradingAgents pipeline against a configurable set of US equity
tickers each trading day, route every LLM call through the
[Gonka](https://gonka.ai) decentralised inference network, persist the
resulting reports and ratings to a local SQLite store, and present them
through an operator dashboard.

Out of scope: order execution, brokerage integration, position tracking,
or any form of automated trading. Outputs are advisory only.

## 2. Connection requirements

### 2.1 Two interchangeable transport modes

The system MUST support both Gonka access patterns and select between them
based solely on which environment variables are set at runtime.

| Mode | Selector | Authentication | Endpoint |
| ---- | -------- | -------------- | -------- |
| Router | `GONKA_API_KEY` is set | Bearer token issued by `router.gonkascan.com` | `https://api.gonkascan.com/v1` |
| SDK   | `GONKA_PRIVATE_KEY` AND `GONKA_SOURCE_URL` are set | ECDSA signature per request via `gonka-openai`; transfer-agent identity discovered from `GET {source}/v1/identity` | `{source}/v1`, e.g. `https://node4.gonka.ai/v1` |

When both modes are configured, the SDK path MUST take precedence. When
neither is configured, the client MUST raise an error that names the
specific environment variable group required.

### 2.2 SDK transport details

The SDK mode MUST implement the **two-identity model** correctly:

* **Signer / payer** — the address derived from the user's secp256k1
  private key. This identity pays for the inference and is debited from
  the user's on-chain GNK balance.
* **Transfer agent** — the gateway's on-chain address, discovered by
  calling `GET {source}/v1/identity` and reading `data.address`. The
  gateway's address is one of the governance-approved entries in
  `transfer_agent_access_params.allowed_transfer_addresses`.

The client MUST NOT pass the user's derived address as `transfer_address`.
Doing so triggers `403 Transfer Agent not allowed` from Gonka nodes,
because regular user addresses are not on the whitelist.

### 2.3 Streaming

Streaming MUST be enabled by default for both modes (`stream=True`,
`stream_usage=True`). This is required because:

* The Gonka API node buffers non-streamed JSON responses entirely before
  forwarding (`io.ReadAll` in `proxy.go`); a multi-minute inference leaves
  the TCP connection silent and is severed by the Cloudflare 100 second
  upstream-response timeout.
* Streamed responses are forwarded chunk-by-chunk (`bufio.Scanner`), which
  keeps bytes flowing on the wire and avoids the timeout.

Callers MUST be able to disable streaming explicitly when needed.

### 2.4 vLLM payload compatibility

When LangChain's streaming chunk aggregator yields an assistant message
that carries `tool_calls` together with whitespace-only `content` (a known
artefact of how `\n` chunks fold into the aggregated content field), the
client MUST rewrite that `content` to `None` before sending the next turn.

vLLM rejects `messages[i].content: must not be empty` for both empty
strings and pure whitespace; the OpenAI SDK serialises `None` as JSON
`null`, which vLLM accepts. This rewrite MUST NOT affect any other
message role or any assistant message with real content.

### 2.5 Model support

The client MUST support, at minimum, the two models that Gonka mainnet
currently serves:

* `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8`
* `moonshotai/Kimi-K2.6`

The selection is exposed via `TRADINGAGENTS_DEEP_THINK_LLM` and
`TRADINGAGENTS_QUICK_THINK_LLM` (one model identifier each). The runner
defaults to `Qwen/Qwen3-235B-A22B-Instruct-2507-FP8` for both roles when
the operator has set `GONKA_API_KEY` but has not explicitly chosen
models, on the basis of measured reliability.

## 3. Daily run requirements

### 3.1 Inputs

* A list of tickers (CLI positional args or, when omitted, the top 20
  S&P 500 constituents by market capitalisation defined in
  `app/sp500.py`, overridable via `SP500_TICKERS`).
* A trade date (defaults to today's UTC date).
* A concurrency level (`-j N` flag, env var
  `TRADINGAGENTS_APP_MAX_WORKERS`, or in-dashboard setting).

### 3.2 Execution model

* Tickers MUST be processable in parallel via a thread pool, with each
  worker constructing its own `TradingAgentsGraph` instance. Sharing one
  graph across threads is not permitted because `propagate()` mutates
  per-instance state.
* Failures on individual tickers MUST NOT abort the batch. Each ticker's
  outcome (success or error) MUST be persisted independently.

### 3.3 Outputs

For each (ticker, trade_date) pair, the system MUST persist:

* The five-tier rating (Buy / Overweight / Hold / Underweight / Sell)
  extracted from the Portfolio Manager's structured output.
* The full Portfolio Manager decision markdown.
* The Trader plan markdown.
* The Research Manager / debate verdict markdown.
* The four analyst reports (Market, Sentiment, News, Fundamentals).
* The model provider, deep model, and quick model identifiers used.
* Any error message and traceback, when the run failed.
* A creation timestamp.

Re-running the same (ticker, trade_date) MUST replace the previous row
rather than accumulate duplicates.

### 3.4 Scheduling

A scheduler (APScheduler) MUST trigger a batch run at a configurable
cron expression (default: weekdays, 16:30 America/New_York). The
scheduler MUST be runnable as a standalone foreground process.

## 4. Dashboard requirements

The dashboard is a Streamlit application that operators use to monitor
and control the system. It MUST expose three pages, navigable from a
sidebar.

### 4.1 Decisions page

* Lists all stored decisions for a selected trade date.
* Allows filtering by ticker.
* Renders each decision as a card with: ticker symbol, colour-coded
  rating badge, model attribution, and expanders for the PM decision,
  trader plan, research verdict, and the four analyst reports.
* Surfaces failed runs with their error message in a visually
  distinguishable form.

### 4.2 Tasks page

* Displays summary metrics for the active connection: mode (Router or
  SDK), default deep model, default worker count.
* Provides an inline form to launch a new run, with the ticker list
  pre-populated from the configured default (top 20 S&P 500).
* Lists active runs with PID, elapsed time, mode, model, ticker list, a
  log-tail expander (last 30 lines), and a Stop button per run.
* Lists the most recent runs from the persistent `run_log` table with
  outcome counts and total elapsed time.
* MUST detect process termination correctly, including the zombie state
  that follows un-`wait()`-ed children. A finished run MUST disappear
  from the active list within one page refresh.
* Launching a run MUST NOT block the dashboard. Runs MUST be spawned as
  detached subprocesses in their own process group, so that a Stop
  button can signal the entire process tree via `killpg(SIGTERM)`.
* The Run button MUST be disabled when the currently-selected
  connection mode is missing its credentials.

### 4.3 Settings page

* Radio-style selector for connection mode (Router or SDK).
* Conditional fields per mode:
  * Router: `GONKA_API_KEY` (password input).
  * SDK: `GONKA_PRIVATE_KEY` (password input) and `GONKA_SOURCE_URL`
    (text input, defaulting to `https://node4.gonka.ai`).
* Selectors for the deep and quick models from the supported list, plus
  the default `max_workers` value.
* A Save button that persists the selections to a JSON file at
  `~/.tradingagents/app/settings.json` via an atomic write (temp +
  rename).
* The runner subprocess MUST inherit credentials from the saved
  settings file, not from the dashboard process's own `os.environ`.
  Inherited Gonka environment variables MUST be cleared on the
  subprocess environment before applying the chosen mode's
  credentials, to prevent silent mode confusion.

### 4.4 State persistence

* Settings: `~/.tradingagents/app/settings.json`.
* Active task registry: `~/.tradingagents/app/active_tasks.json`.
* Per-run log files: `~/.tradingagents/app/logs/run_<timestamp>_<pid>.log`.
* Decisions database: `~/.tradingagents/app/decisions.sqlite3` by
  default, overridable via `TRADINGAGENTS_APP_DB`.

All persistence paths MUST be configurable via environment variable.

## 5. Failure handling requirements

The system MUST tolerate the following Gonka-side failure modes
without dropping the entire batch:

* HTTP 524 (Cloudflare upstream timeout) — addressed by the streaming
  requirement (§2.3).
* HTTP 500 `do_request_failed` — surfaced via openai-sdk retries; if
  all retries fail, persisted as a per-ticker error.
* `RemoteProtocolError: peer closed connection (incomplete chunked
  read)` — same handling as 500.
* `inference: winner inference incomplete (nonce_finished=false)` —
  same handling.
* `messages[i].content: must not be empty` (400) — prevented by the
  vLLM payload compatibility rewrite in §2.4.

Per-ticker errors MUST be persisted to the decisions row's `error`
column with the full traceback, so the dashboard can render the
failure without re-running the ticker.

## 6. Non-functional requirements

### 6.1 Performance

A four-ticker parallel run with the default analyst configuration and
Qwen3-235B SHOULD complete in approximately 12–14 minutes on a stable
network. Single-ticker minimal-configuration runs SHOULD complete in
approximately 3 minutes. Kimi-K2.6 runs are expected to be slower and
less reliable; the operator should treat Kimi as experimental.

### 6.2 Security

* Private keys and bearer tokens MUST be rendered as password inputs
  in the dashboard and MUST NOT appear in plain text in log files.
* Configuration files containing credentials MUST be excluded from git
  by `.gitignore`.
* The settings JSON file MUST be created with default user-only
  permissions (the application MUST NOT widen permissions explicitly).

### 6.3 Compatibility

* Python 3.11 on Linux (aarch64 and x86_64) and macOS.
* Conda environment recommended due to `secp256k1` C extension build
  requirements; native pip installation requires `libsecp256k1-dev`
  and matching toolchain on Debian/Ubuntu.

## 7. Open requirements (future work)

The following are explicit non-goals for the current release but are
recognised as planned extensions:

* Multi-TA fan-out across the active participant set (today the SDK
  mode targets one gateway).
* In-graph parallelism across the four analyst nodes.
* In-dashboard scheduler control (currently only via CLI).
* Authentication on the dashboard itself.
