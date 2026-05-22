import unittest

import pytest

from cli.utils import normalize_ticker_symbol
from tradingagents.agents.utils.agent_utils import build_instrument_context
from tradingagents.dataflows.utils import to_yahoo_symbol


@pytest.mark.unit
class TickerSymbolHandlingTests(unittest.TestCase):
    def test_normalize_ticker_symbol_preserves_exchange_suffix(self):
        self.assertEqual(normalize_ticker_symbol(" cnc.to "), "CNC.TO")

    def test_build_instrument_context_mentions_exact_symbol(self):
        context = build_instrument_context("7203.T")
        self.assertIn("7203.T", context)
        self.assertIn("exchange suffix", context)


@pytest.mark.unit
class ToYahooSymbolTests(unittest.TestCase):
    """`to_yahoo_symbol` rewrites canonical ticker notation to Yahoo's
    convention: '-' for US class shares (BRK-B), '.' for exchange suffixes
    (7203.T). Without this, yfinance returns empty results and logs
    'possibly delisted; no timezone found' for tickers like BRK.B."""

    def test_us_class_share_dot_becomes_dash(self):
        self.assertEqual(to_yahoo_symbol("BRK.B"), "BRK-B")
        self.assertEqual(to_yahoo_symbol("BF.B"), "BF-B")
        self.assertEqual(to_yahoo_symbol("HEI.A"), "HEI-A")
        self.assertEqual(to_yahoo_symbol("BRK.A"), "BRK-A")

    def test_lowercase_input_is_uppercased(self):
        self.assertEqual(to_yahoo_symbol("brk.b"), "BRK-B")
        self.assertEqual(to_yahoo_symbol("aapl"), "AAPL")

    def test_whitespace_stripped(self):
        self.assertEqual(to_yahoo_symbol("  BRK.B  "), "BRK-B")

    def test_no_dot_unchanged(self):
        self.assertEqual(to_yahoo_symbol("AAPL"), "AAPL")
        self.assertEqual(to_yahoo_symbol("NVDA"), "NVDA")

    def test_index_symbol_with_caret_unchanged(self):
        self.assertEqual(to_yahoo_symbol("^GSPC"), "^GSPC")
        self.assertEqual(to_yahoo_symbol("^N225"), "^N225")

    def test_digit_prefixed_ticker_keeps_dot_as_exchange_suffix(self):
        """Tokyo Toyota / Taiwan Semiconductor — digits before the dot
        means the suffix is an exchange code, not a US class share."""
        self.assertEqual(to_yahoo_symbol("7203.T"), "7203.T")
        self.assertEqual(to_yahoo_symbol("2330.TW"), "2330.TW")
        self.assertEqual(to_yahoo_symbol("0700.HK"), "0700.HK")

    def test_multi_letter_exchange_suffix_unchanged(self):
        """Two-letter suffixes (HK, AS, AX, SS, BO, TO) are exchange codes."""
        self.assertEqual(to_yahoo_symbol("RDS.AS"), "RDS.AS")
        self.assertEqual(to_yahoo_symbol("BHP.AX"), "BHP.AX")
        self.assertEqual(to_yahoo_symbol("CNC.TO"), "CNC.TO")

    def test_already_dashed_us_class_share_unchanged(self):
        """Yahoo-form input passes through idempotent."""
        self.assertEqual(to_yahoo_symbol("BRK-B"), "BRK-B")

    def test_crypto_unchanged(self):
        self.assertEqual(to_yahoo_symbol("BTC-USD"), "BTC-USD")

    def test_fx_unchanged(self):
        self.assertEqual(to_yahoo_symbol("EURUSD=X"), "EURUSD=X")

    def test_empty_returns_empty(self):
        self.assertEqual(to_yahoo_symbol(""), "")


if __name__ == "__main__":
    unittest.main()
