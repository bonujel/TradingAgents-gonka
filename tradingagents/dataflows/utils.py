import os
import re
import json
import pandas as pd
from datetime import date, timedelta, datetime
from typing import Annotated

SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]

# Tickers can contain letters, digits, dot, dash, underscore, and caret
# (for index symbols like ^GSPC). Anything else is rejected so the value
# never escapes a containing directory when interpolated into a path.
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^]+$")


# Yahoo Finance uses '-' for US class shares (BRK-B, BF-B, HEI-A) but '.'
# for exchange suffixes (7203.T, 2330.TW, RDS.AS). Canonical industry
# notation uses '.' for both, so requests like ``yf.Ticker("BRK.B")``
# return empty and log "possibly delisted; no timezone found". We detect
# the US-class-share pattern (all letters before the dot, single letter
# after) and rewrite '.' to '-'. Multi-letter suffixes and digit-prefixed
# tickers are left alone because their '.' is genuinely an exchange suffix.
_US_CLASS_SHARE_RE = re.compile(r"^[A-Z]+\.[A-Z]$")


def to_yahoo_symbol(symbol: str) -> str:
    """Rewrite a canonical ticker to the form Yahoo Finance expects.

    All whitespace — leading, trailing, and internal — is removed. A
    ticker never legitimately contains whitespace, and an LLM emitting a
    tool-call argument occasionally stutters a space into the symbol
    string (observed: the model produced ``"GOO  GL"`` for GOOGL, which
    then failed every downstream data fetch). Stripping it here, on the
    single normalisation point every data source funnels through, repairs
    the value transparently before it reaches yfinance / stockstats.

    Examples:
        BRK.B    -> BRK-B
        BF.B     -> BF-B
        HEI.A    -> HEI-A
        AAPL     -> AAPL     (no dot)
        7203.T   -> 7203.T   (digits before dot -> exchange suffix)
        2330.TW  -> 2330.TW  (multi-letter suffix -> exchange)
        RDS.AS   -> RDS.AS   (multi-letter suffix -> exchange)
        ^GSPC    -> ^GSPC    (index symbol)
        btc-usd  -> BTC-USD  (already correct form; just uppercased)
        GOO  GL  -> GOOGL    (LLM whitespace stutter repaired)
    """
    if not isinstance(symbol, str) or not symbol:
        return symbol
    s = re.sub(r"\s+", "", symbol).upper()
    if _US_CLASS_SHARE_RE.match(s):
        return s.replace(".", "-")
    return s


def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    Tickers come from user CLI input or from LLM tool calls, both of which
    can be influenced by attacker-controlled content (e.g. prompt injection
    embedded in fetched news). Without validation, a value like
    ``"../../../etc/foo"`` flows into ``os.path.join`` / ``Path /`` and
    escapes the configured cache, checkpoint, or results directory.

    Returns ``value`` unchanged when it matches the allowed pattern; raises
    ``ValueError`` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_PATH_RE.fullmatch(value):
        raise ValueError(
            f"ticker contains characters not allowed in a filesystem path: {value!r}"
        )
    # The regex above allows '.', so values like '.', '..', '...' would pass,
    # and as a path component they traverse the parent directory. Reject any
    # value that's only dots.
    if set(value) == {"."}:
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    return value


def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    if save_path:
        data.to_csv(save_path, encoding="utf-8")
        print(f"{tag} saved to {save_path}")


def get_current_date():
    return date.today().strftime("%Y-%m-%d")


def decorate_all_methods(decorator):
    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():
            if callable(attr_value):
                setattr(cls, attr_name, decorator(attr_value))
        return cls

    return class_decorator


_DATE_PATTERN = re.compile(r"^(\d{1,4})-(\d{1,2})-(\d{1,2})$")


def normalize_date(date_str) -> str | None:
    """Coerce a possibly-garbled date string into canonical YYYY-MM-DD.

    LLM tool-call arguments are not always clean: in the 2026-05-19 batch
    the model produced things like ``'226-05-15'`` (year truncated to 3
    digits), ``'20 20-04-01'`` (whitespace inserted), or ``'22'`` (just
    junk). Bare ``datetime.strptime`` raises ``ValueError`` and bubbles
    out of the LangGraph node, killing the whole ticker run.

    Strategy (each step is cheap; we bail at the first that succeeds):
      1. Strip all whitespace (fixes ``'20 20-04-01'`` and ``'20 -005-14'``
         when the latter happens to land on a valid date after stripping).
      2. Try strict parse.
      3. If the string still looks like ``Y-M-D`` but the year is < 4
         digits, pad to 4 digits assuming the 21st century:
         ``'226'`` → ``'2026'``, ``'26'`` → ``'2026'``.
      4. Re-parse the padded form; ``datetime(Y, M, D)`` rejects illegal
         month/day combinations so ``'206-0-19'`` (month 0) returns None.

    Returns canonical ``YYYY-MM-DD`` on success, ``None`` when the string
    is too far gone to salvage. Callers decide how to handle ``None`` —
    typically by returning an error string the LLM can read and retry from.
    """
    if not date_str:
        return None
    cleaned = re.sub(r"\s+", "", str(date_str))
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    m = _DATE_PATTERN.match(cleaned)
    if not m:
        return None
    y_raw, mo, d = m.groups()
    y_int = int(y_raw)
    if y_int < 100:
        y_int += 2000        # '26' → 2026
    elif y_int < 1000:
        y_int = 2000 + (y_int % 100)  # '226' → 2026
    try:
        return datetime(y_int, int(mo), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def safe_strptime(date_str, *, fmt: str = "%Y-%m-%d") -> datetime:
    """Drop-in replacement for ``datetime.strptime(date_str, "%Y-%m-%d")``
    that recovers from common LLM date garbling via ``normalize_date``.

    Raises ``ValueError`` with the *original* input in the message when
    the string is unrecoverable, so callers' existing ``except ValueError``
    handlers keep working.
    """
    if fmt != "%Y-%m-%d":
        return datetime.strptime(date_str, fmt)
    normalized = normalize_date(date_str)
    if normalized is None:
        raise ValueError(
            f"time data {date_str!r} does not match format '%Y-%m-%d'"
        )
    return datetime.strptime(normalized, fmt)


def get_next_weekday(date):

    if not isinstance(date, datetime):
        date = safe_strptime(date)

    if date.weekday() >= 5:
        days_to_add = 7 - date.weekday()
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        return date
