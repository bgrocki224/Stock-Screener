"""Research-only stock/options screener. Delayed/public feeds; never routes orders."""
import datetime as dt
import io
import math
import os
import re
import requests
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Options Opportunity Terminal", layout="wide")
st.title("Options Opportunity Terminal")
st.caption("S&P 500 · 2–10 trading-day horizon · defined-risk trades · research only")
st.warning("Data may be delayed, stale, unavailable, or incomplete. No orders are placed. Scores are heuristic, not probabilities or validated expected returns.")

FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "XOM", "UNH", "AVGO", "COST", "PLTR", "NFLX"]
HEADERS = {"User-Agent": "OpportunityResearch/0.1 (research; contact: configure-your-email)"}

@st.cache_data(ttl=86400, show_spinner=False)
def constituents():
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    try:
        response = requests.get(url, timeout=12, headers=HEADERS)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
        names = frame["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        if len(names) < 450:
            raise ValueError("Incomplete universe")
        return sorted(set(names)), "Public GitHub constituent snapshot (not official licensed index feed)"
    except Exception:
        return FALLBACK, "Fallback sample only — full constituent feed unavailable"

@st.cache_data(ttl=900, show_spinner=False)
def price_history(tickers):
    result = {}
    for start in range(0, len(tickers), 35):
        chunk = list(tickers[start:start + 35])
        try:
            raw = yf.download(chunk, period="5mo", interval="1d", group_by="ticker", auto_adjust=True, threads=True, progress=False, timeout=20)
            for ticker in chunk:
                try:
                    frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
                    frame = frame.dropna(subset=["Close", "Volume"])
                    if len(frame) >= 55:
                        result[ticker] = frame[["Close", "High", "Low", "Volume"]].copy()
                except (KeyError, ValueError):
                    pass
        except Exception:
            pass
    return result

def score_one(ticker, frame):
    close = frame.Close.astype(float)
    vol = frame.Volume.astype(float)
    if len(close) < 55 or close.iloc[-1] <= 0:
        return None
    ret5 = float(close.iloc[-1] / close.iloc[-6] - 1)
    ret10 = float(close.iloc[-1] / close.iloc[-11] - 1)
    sma20 = float(close.tail(20).mean())
    sma50 = float(close.tail(50).mean())
    rvol = float(vol.iloc[-1] / max(vol.iloc[-21:-1].mean(), 1))
    delta = close.diff()
    up = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    down = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = float(100 - 100 / (1 + up / max(down, 1e-9)))
    direction = "Bullish" if close.iloc[-1] > sma20 and sma20 > sma50 and ret5 > 0 else ("Bearish" if close.iloc[-1] < sma20 and sma20 < sma50 and ret5 < 0 else "Mixed")
    momentum = min(20, abs(ret5) * 220 + abs(ret10) * 80)
    volume = min(15, max(0, (rvol - 0.75) * 12))
    trend = 20 if direction != "Mixed" else 6
    rsi_fit = 10 if (direction == "Bullish" and 48 <= rsi <= 75) or (direction == "Bearish" and 25 <= rsi <= 52) else 3
    # 65 points are observable technical inputs; catalyst, sentiment, and options components remain unverified.
    technical = round(momentum + volume + trend + rsi_fit, 1)
    return {"Ticker": ticker, "Technical /65": technical, "Direction": direction, "Last close": round(float(close.iloc[-1]), 2), "5D %": round(ret5 * 100, 2), "10D %": round(ret10 * 100, 2), "Relative volume": round(rvol, 2), "RSI14": round(rsi, 1), "As of": str(close.index[-1].date())}

@st.cache_data(ttl=600, show_spinner=False)
def options_chain(ticker):
    obj = yf.Ticker(ticker)
    today = dt.date.today()
    expiry_list = []
    for expiry in obj.options:
        day = dt.date.fromisoformat(expiry)
        # ~2–10 trading days, approximated here by 2–16 calendar days; verify holidays manually.
        if 2 <= (day - today).days <= 16:
            expiry_list.append(expiry)
    frames = []
    for expiry in expiry_list[:4]:
        try:
            chain = obj.option_chain(expiry)
            for side, frame in [("call", chain.calls), ("put", chain.puts)]:
                f = frame.copy()
                f["side"] = side
                f["expiration"] = expiry
                frames.append(f)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def build_spreads(chain, spot, direction, max_width, max_spread_pct):
    if chain.empty:
        return pd.DataFrame()
    side = "call" if direction == "Bullish" else "put"
    rows = []
    for expiry, grp in chain[chain.side == side].groupby("expiration"):
        g = grp.copy()
        for field in ("strike", "bid", "ask", "openInterest", "volume"):
            g[field] = pd.to_numeric(g[field], errors="coerce")
        g = g.dropna(subset=["strike", "bid", "ask"])
        g = g[(g.bid >= 0) & (g.ask > 0) & (g.ask >= g.bid) & (g.openInterest.fillna(0) >= 100)]
        g = g[(g.ask - g.bid) / ((g.ask + g.bid) / 2) <= max_spread_pct]
        g = g[abs(g.strike - spot) / spot <= .12].sort_values("strike")
        legs = g.to_dict("records")
        for buy in legs:
            for sell in legs:
                width = (sell["strike"] - buy["strike"]) if side == "call" else (buy["strike"] - sell["strike"])
                if width <= 0 or width > max_width:
                    continue
                # Conservative natural execution: buy at ask, sell at bid.
                debit = float(buy["ask"] - sell["bid"])
                if debit <= 0 or debit >= width:
                    continue
                maxloss = debit * 100
                maxgain = (width - debit) * 100
                breakeven = buy["strike"] + debit if side == "call" else buy["strike"] - debit
                rows.append({"Expiry": expiry, "Strategy": "Call debit spread" if side == "call" else "Put debit spread", "Buy strike": buy["strike"], "Sell strike": sell["strike"], "Debit $": round(maxloss, 2), "Max gain $": round(maxgain, 2), "Breakeven": round(breakeven, 2), "Reward/risk": round(maxgain / maxloss, 2), "OI buy": int(buy["openInterest"]), "OI sell": int(sell["openInterest"]), "Buy contract": buy.get("contractSymbol", ""), "Sell contract": sell.get("contractSymbol", "")})
    return pd.DataFrame(rows).sort_values("Reward/risk", ascending=False) if rows else pd.DataFrame()

with st.sidebar:
    st.header("Scan settings")
    universe, universe_note = constituents()
    st.caption(universe_note)
    scan_size = st.select_slider("Scan size", options=[15, 50, 100, 200, 350, 503], value=50)
    st.caption("Scan size 503 means all currently available constituents, not necessarily exactly 503.")
    min_score = st.slider("Minimum technical score /65", 0, 65, 32)
    max_width = st.slider("Maximum spread width ($)", 1, 25, 10)
    max_quote_spread = st.slider("Maximum leg bid/ask spread (%)", 5, 60, 25) / 100
    scan = st.button("Refresh market scan", type="primary", use_container_width=True)
    st.caption("Prices: Yahoo Finance via yfinance (unofficial interface). Delays, rate limits, and outages possible.")

if "scan_rows" not in st.session_state or scan:
    with st.spinner("Downloading price history and calculating signals…"):
        names = universe[:scan_size]
        history = price_history(tuple(names))
        st.session_state.scan_rows = [x for ticker, frame in history.items() if (x := score_one(ticker, frame)) is not None]
        st.session_state.scan_count = len(names)
        st.session_state.scan_time = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

rows = st.session_state.scan_rows
st.caption(f"Last application refresh: {st.session_state.scan_time} UTC · {len(rows)}/{st.session_state.scan_count} symbols with usable data. Failed symbols are not silently counted as scanned.")
if not rows:
    st.error("No usable price data. Check connectivity or retry later; no sample results are presented as live data.")
    st.stop()
frame = pd.DataFrame(rows).sort_values("Technical /65", ascending=False)
qualified = frame[frame["Technical /65"] >= min_score]
c1, c2, c3 = st.columns(3)
c1.metric("Stocks with data", len(frame))
c2.metric("Technical candidates", len(qualified))
c3.metric("Directional candidates", len(qualified[qualified.Direction != "Mixed"]))
st.subheader("Technical opportunity ranking")
st.caption("Not a complete 100-point score: news, Reddit, filings, and options flow are NOT connected in this version and receive no points.")
st.dataframe(qualified, use_container_width=True, hide_index=True)

st.subheader("Inspect a stock & construct defined-risk trades")
selected = st.selectbox("Ticker", frame.Ticker.tolist())
item = frame.set_index("Ticker").loc[selected]
st.write(f"**{selected}** · {item['Direction']} technical setup · last close ${item['Last close']:,.2f} ({item['As of']})")
st.write("**Verification:** News catalyst — not connected · Reddit sentiment — not connected · SEC catalyst — not connected · Options flow — not verified")
st.markdown(f"[Search SEC filings](https://www.sec.gov/edgar/search/) · [Search news](https://www.google.com/search?q={selected}+stock+news) · [Search Reddit](https://www.reddit.com/search/?q=%24{selected})")
if item["Direction"] == "Mixed":
    st.info("Mixed trend: no directional spread is suggested. Select another ticker for bullish/bearish screening.")
else:
    if st.button(f"Load {selected} option chains"):
        with st.spinner("Retrieving available options contracts…"):
            try:
                st.session_state.option_ticker = selected
                st.session_state.option_data = options_chain(selected)
            except Exception as ex:
                st.session_state.option_data = pd.DataFrame()
                st.error(f"Options feed unavailable: {type(ex).__name__}")
    if st.session_state.get("option_ticker") == selected:
        chain = st.session_state.get("option_data", pd.DataFrame())
        if chain.empty:
            st.info("No eligible options-chain data available. No hypothetical quotes are substituted.")
        else:
            spreads = build_spreads(chain, float(item["Last close"]), item["Direction"], max_width, max_quote_spread)
            st.caption("Natural-price debit spread estimates (buy ask/sell bid), 100-share multiplier; no fees, early assignment, dividend or fill guarantee. Quotes may be delayed and underlying close may not be contemporaneous.")
            if spreads.empty:
                st.info("No spreads pass current filters. Try adjusting width/liquidity constraints.")
            else:
                st.dataframe(spreads.head(30), use_container_width=True, hide_index=True)
                st.caption("Reward/risk alone is NOT expected value. These are candidate structures, not trade recommendations. Confirm live quotes, expiration, earnings and corporate actions before any order.")

st.divider()
st.subheader("Signal verification and data-integrity gates")
st.write("**Implemented:** 5-/10-day returns, moving-average alignment, RSI, relative volume, delayed/public market history, option bid/ask and open-interest filters, debit-spread payoff math, missing-data flags.")
st.write("**Next adapters:** licensed market snapshots, earnings calendar, SEC 8-K monitoring, authorized Reddit OAuth, publisher-approved news feeds, historical validation and paper-trade journal.")
st.caption("Research tool only. Options can lose 100% of premium. This prototype has not been backtested and must not be treated as a live execution signal.")
