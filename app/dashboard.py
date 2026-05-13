"""Streamlit dashboard for TradingAgents daily decisions.

Run with::

    streamlit run app/dashboard.py

The dashboard is read-only: it queries the SQLite store filled by
``app.runner``/``app.scheduler`` and renders one card per ticker for the
selected trade date, plus a "trigger manual run" sidebar action that calls
``run_daily`` synchronously.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Allow ``streamlit run app/dashboard.py`` from the repo root without an
# editable install — Streamlit only sees the dashboard file's directory on
# sys.path by default, which would hide the ``tradingagents`` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st  # noqa: E402

from app import db  # noqa: E402
from app.sp500 import get_top_tickers  # noqa: E402


RATING_COLORS = {
    "Buy": "#16a34a",
    "Overweight": "#65a30d",
    "Hold": "#737373",
    "Underweight": "#ea580c",
    "Sell": "#dc2626",
}


def _rating_badge(rating: str | None) -> str:
    if not rating:
        return "<span style='color:#9ca3af'>—</span>"
    color = RATING_COLORS.get(rating, "#374151")
    return (
        f"<span style='background:{color};color:white;padding:2px 8px;"
        f"border-radius:6px;font-size:0.85em'>{rating}</span>"
    )


def _render_decision(row) -> None:
    cols = st.columns([1, 1, 4])
    with cols[0]:
        st.markdown(f"### {row['ticker']}")
    with cols[1]:
        st.markdown(_rating_badge(row["rating"]), unsafe_allow_html=True)
    with cols[2]:
        st.caption(
            f"Trade date: {row['trade_date']} · model: "
            f"{row['model_provider']}/{row['deep_model']} · "
            f"stored: {row['created_at']}"
        )

    if row["error"]:
        st.error(row["error"])
        return

    if row["final_decision"]:
        with st.expander("Portfolio Manager decision", expanded=True):
            st.markdown(row["final_decision"])
    if row["trader_plan"]:
        with st.expander("Trader plan"):
            st.markdown(row["trader_plan"])
    if row["investment_plan"]:
        with st.expander("Research manager / debate verdict"):
            st.markdown(row["investment_plan"])

    report_fields = [
        ("Market report", row["market_report"]),
        ("Sentiment report", row["sentiment_report"]),
        ("News report", row["news_report"]),
        ("Fundamentals report", row["fundamentals_report"]),
    ]
    available = [(label, body) for label, body in report_fields if body]
    if available:
        with st.expander("Analyst reports"):
            tabs = st.tabs([label for label, _ in available])
            for tab, (_, body) in zip(tabs, available):
                with tab:
                    st.markdown(body)


def main() -> None:
    st.set_page_config(
        page_title="TradingAgents · Gonka Kimi",
        layout="wide",
        page_icon="📈",
    )
    db.init_db()

    st.title("TradingAgents — Gonka × Kimi K2.6")
    st.caption(
        "Daily S&P 500 analysis powered by the Gonka decentralised LLM router. "
        "Decisions are produced by the TradingAgents multi-agent framework and "
        "stored in SQLite at " + str(db.get_db_path())
    )

    dates = db.list_run_dates()
    with st.sidebar:
        st.header("Filters")
        if dates:
            selected_date = st.selectbox("Trade date", dates, index=0)
        else:
            selected_date = None
            st.info("No runs recorded yet. Trigger one below.")

        ticker_filter = st.text_input("Ticker (optional)", "").strip().upper() or None

        st.divider()
        st.header("Manual run")
        default_tickers = ",".join(get_top_tickers(20))
        tickers_input = st.text_area(
            "Tickers (comma-separated)",
            value=default_tickers,
            help="Defaults to the top-20 S&P 500 constituents by market cap.",
        )
        run_clicked = st.button("Run analysis now", type="primary")

        st.divider()
        st.header("Recent runs")
        for run in db.list_runs(limit=5):
            st.caption(
                f"{run['run_date']} · {run['success_count']}✓ / {run['failure_count']}✗"
            )

    if run_clicked:
        from app.runner import run_daily

        tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
        with st.spinner(f"Running TradingAgents on {len(tickers)} tickers — this can take a while…"):
            summary = run_daily(tickers, trade_date=datetime.utcnow().strftime("%Y-%m-%d"))
        st.success(
            f"Run finished: {summary['success']} ok / {summary['failure']} failed "
            f"(date={summary['trade_date']})"
        )
        # Re-query after the run so the new rows show up without a manual refresh.
        dates = db.list_run_dates()
        selected_date = dates[0] if dates else None

    if not selected_date:
        st.stop()

    rows = db.list_decisions(trade_date=selected_date, ticker=ticker_filter)
    if not rows:
        st.warning("No decisions stored for this filter.")
        return

    success = sum(1 for r in rows if not r["error"])
    failure = len(rows) - success
    st.markdown(f"**{len(rows)} tickers** · {success} succeeded · {failure} failed")

    for row in rows:
        _render_decision(row)
        st.divider()


if __name__ == "__main__":
    main()
