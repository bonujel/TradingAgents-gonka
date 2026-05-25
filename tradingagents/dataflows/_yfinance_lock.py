"""Process-wide serialisation lock for yfinance calls.

yfinance maintains a peewee-backed SQLite cache (``~/.cache/py-yfinance/``
— ``tkr-tz.db`` for timezones, ``cookies.db`` for session cookies). Every
``yf.download``, ``yf.Ticker(...).history``, ``.info``, ``.get_news`` etc.
opens a connection to that file. The default SQLite journal mode is
``DELETE`` which serialises *writes* via exclusive file locks.

The runner uses ``ThreadPoolExecutor`` (``app/runner.py:380``) to fan out
ticker analyses with ``-j N`` concurrency. All N worker threads live in
the same process and open peewee connections to the same cache file from
the same Python interpreter; under contention, the losing thread sees::

    sqlite3.OperationalError: database is locked

which the runner's retry whitelist does NOT cover — it is not an
``openai.*`` transport error, not an ``httpx.RemoteProtocolError``, and
its message doesn't match any marker in
``_RETRYABLE_API_ERROR_MARKERS`` / ``_RETRYABLE_VALUE_ERROR_MARKERS``.
So a single losing-thread race one-shot kills the ticker on the
``tools_market`` node (verified 2026-05-25 run on AMZN).

Wrapping every yfinance call in this module-level lock serialises them
within a process, eliminating the contention. yfinance calls are I/O
bound (~100ms cache hit, ~1s cold cache); serialising them across 8
threads costs a few seconds per ticker — negligible against per-ticker
LLM latency of 5–10 minutes.

Scope is intentionally process-wide (not per-thread, not per-DB):
yfinance's cache is one shared resource; one lock is the simplest correct
shape. If a future caller needs read-mostly concurrency they should
look at switching the cache to WAL mode instead.
"""
from __future__ import annotations

import threading


# A single re-entrant lock so a thread that holds it and recursively
# invokes another yfinance helper (e.g. ``yf_retry`` calling into another
# wrapped call) does not deadlock against itself. yfinance internals do
# call into each other, especially for tz/session bootstrap.
YFINANCE_LOCK: threading.RLock = threading.RLock()
