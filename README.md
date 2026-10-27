# Options Opportunity Terminal v1

A working local Streamlit research prototype for S&P 500 technical screening and **defined-risk debit spread** candidate construction over a roughly 2–10 trading-day target window.

## Run

Requires Python 3.10+ and internet access.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the localhost URL printed by Streamlit. Use **Refresh market scan** to update cached market data; use **Load option chains** on a selected bullish or bearish candidate.

## Data and licensing

- Constituents: community-maintained GitHub S&P 500 CSV, fallback to a clearly labeled 15-symbol sample if unavailable. Not an official index membership license.
- Equity history and options: `yfinance`, an unofficial Yahoo Finance interface, for prototyping/personal research only; terms, coverage, rate limits and redistribution rights must be reviewed before commercial or professional use.
- SEC, Reddit and premium publisher integrations are **not implemented**; the UI explicitly labels these missing signals.
- The UI is not an automatically updating exchange-real-time feed. Cached equity history refreshes at most every 15 minutes when queried; option chains at most every 10 minutes when queried. No background server polling is implemented.

## Ranking

Technical-only score out of 65: 20 momentum, 15 relative volume, 20 trend alignment, 10 RSI fit. This is an unvalidated heuristic, not a forecast or win probability. Missing sentiment/news/options components are not fabricated.

## Options logic

For bullish technical setups, scan call debit spreads; for bearish setups, put debit spreads. Require both legs to have >=100 reported open interest and per-leg quoted spread below a configurable threshold. Calculate debit conservatively at ask of purchased leg minus bid of sold leg; max loss = debit x 100, max gain = (strike width - debit) x 100. Quotes and underlying closes may be asynchronous. The calendar-day expiry filter (2–16 days) is only an approximation of 2–10 trading days and does not handle market holidays.

**Limitations:** No earnings/event exclusions, quote timestamps, volatility surface, options trade direction classification, early assignment/dividend modeling, backtest, portfolio sizing, execution, fees, or slippage beyond natural-price bid/ask. Always independently verify market data before trading. No trade placement.

## Production-ready extension plan

1. Replace `yfinance` with licensed stock and OPRA options APIs; store timestamps and stale-data status, batch full-universe snapshots, enforce entitlements.
2. Add SEC company ticker/CIK map and 8-K submissions adapter, with proper User-Agent and rate limits.
3. Add Reddit OAuth with approved API access and licensed/approved publisher headline feeds (do not scrape paywalls).
4. Add earnings and corporate-action blackout checks, reliable trading-day calendars, delta/IV percentile and market regime controls.
5. Store point-in-time signals in PostgreSQL, schedule ingestion via worker, expose FastAPI `/signals`, `/chains`, `/strategies`, `/health`.
6. Backtest with survivorship-bias-aware constituents, point-in-time options quotes, transaction costs and realistic fills; then paper-trade before use.
7. Add authentication, secrets management, observability and explicit vendor redistribution/commercial licenses before external deployment.

## Reference documentation

- https://massive.com/docs/rest/options/overview
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
