import time
import logging

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from stockstats import wrap
from typing import Annotated
import os
from ._yfinance_lock import YFINANCE_LOCK
from .config import get_config
from .utils import safe_ticker_component, to_yahoo_symbol

logger = logging.getLogger(__name__)


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.

    The call is held under ``YFINANCE_LOCK`` so that the runner's
    ThreadPoolExecutor cannot have multiple threads open yfinance's
    peewee/SQLite cache concurrently — that race produces
    ``sqlite3.OperationalError: database is locked`` which the runner
    cannot retry (verified 2026-05-25 run on AMZN, see commit body).
    See ``_yfinance_lock.py`` for the full rationale.
    """
    for attempt in range(max_retries + 1):
        try:
            with YFINANCE_LOCK:
                return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise


# Maps every plausible spelling of the date column to the canonical
# ``Date`` that downstream code expects.
#
# Why this matters: ``load_ohlcv`` writes the cache after
# ``data.reset_index()``. yfinance returns a DataFrame whose
# ``DatetimeIndex`` has ``name=None`` on some yfinance / pandas combos
# (verified on the deploy host 2026-05-25: every CSV in
# ``~/.tradingagents/cache/`` starts ``index,Close,High,Low,Open,Volume``).
# When that gets read back, ``_clean_dataframe`` raised ``KeyError:
# 'Date'`` on every indicator call — hundreds of identical lines in the
# runner log per batch.
_DATE_COLUMN_ALIASES = ("Date", "Datetime", "date", "datetime", "index", "Unnamed: 0")


def _normalize_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Make the date column be named ``Date``, whatever it was on disk.

    Picks the first column in the frame whose name matches a known alias
    (in priority order) and renames it to ``Date``. If a real ``Date``
    column already exists we leave the frame alone — even when there is a
    stray second alias, renaming it would create a duplicate column.
    """
    if "Date" in data.columns:
        return data
    for alias in _DATE_COLUMN_ALIASES[1:]:
        if alias in data.columns:
            return data.rename(columns={alias: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _normalize_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 15 years of data up to today and caches per symbol. On
    subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    # Reject ticker values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    # Yahoo expects 'BRK-B' (dash) not 'BRK.B' (dot) for class shares;
    # normalising before both the cache key and the fetch keeps the two
    # sides in sync so we don't cache an empty result under the wrong key.
    yahoo_symbol = to_yahoo_symbol(symbol)
    safe_symbol = safe_ticker_component(yahoo_symbol)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (15y to today) so one file per symbol
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        data = yf_retry(lambda: yf.download(
            yahoo_symbol,
            start=start_str,
            end=end_str,
            multi_level_index=False,
            progress=False,
            auto_adjust=True,
        ))
        # Some yfinance / pandas combos return a DatetimeIndex with
        # ``name=None``; reset_index() then names the resulting column
        # ``"index"`` instead of ``"Date"``. Normalise before we persist
        # so downstream readers and the cache stay consistent — older
        # ``"index"``-headed CSVs still on disk are tolerated at read
        # time by ``_clean_dataframe``.
        data = _normalize_date_column(data.reset_index())
        data.to_csv(data_file, index=False, encoding="utf-8")

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
