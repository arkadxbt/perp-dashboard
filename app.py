"""
Perp Dashboard — Binance USDⓈ-M Futures perpetual tracker
Author : arkadxbt
Repo   : https://github.com/arkadxbt/perp-dashboard
Deploy : Streamlit Community Cloud
"""

import time
import math
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
BASE_URL     = "https://fapi.binance.com"
OI_URL       = "https://fapi.binance.com"   # same host, different path

TIMEFRAMES   = ["5m", "15m", "1h", "2h", "4h", "1d"]

# How many candles to fetch per kline call (we only need last 2)
KLINE_LIMIT  = 2

# OI history period mapping  →  Binance "period" param
TF_TO_OI_PERIOD = {
    "5m" : "5m",
    "15m": "15m",
    "1h" : "1h",
    "2h" : "2h",
    "4h" : "4h",
    "1d" : "1d",
}

# Funding rate history: always fetch last 2 entries
FUNDING_LIMIT = 2

# Streamlit cache TTL (seconds) — longer = fewer API calls
CACHE_TTL_SYMBOLS   = 3600   # symbol list changes rarely
CACHE_TTL_VOLUME    = 60     # 24h ticker — refresh every minute
CACHE_TTL_DATA      = 15     # per-symbol price/OI/FR data

# HTTP client timeout
HTTP_TIMEOUT = 10.0

# Max concurrent requests (respect Binance rate limits)
SEMAPHORE_LIMIT = 20

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("perp_dashboard")

# ──────────────────────────────────────────────
# HELPERS — HTTP
# ──────────────────────────────────────────────

async def _get(client: httpx.AsyncClient, url: str, params: dict = None) -> Optional[dict | list]:
    """Single async GET with error handling. Returns None on failure."""
    try:
        r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("GET %s params=%s → %s", url, params, e)
        return None


# ──────────────────────────────────────────────
# CACHED DATA FETCHERS
# ──────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL_SYMBOLS, show_spinner=False)
def fetch_usdt_perp_symbols() -> list[str]:
    """Return all active USDT-margined perpetual symbols from exchangeInfo."""
    try:
        r = httpx.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=HTTP_TIMEOUT)
        data = r.json()
        symbols = [
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ]
        return sorted(symbols)
    except Exception as e:
        log.error("fetch_symbols: %s", e)
        return []


@st.cache_data(ttl=CACHE_TTL_VOLUME, show_spinner=False)
def fetch_top_symbols_by_volume(top_n: int) -> list[str]:
    """Return top-N USDT perp symbols sorted by 24h quote volume (descending)."""
    all_symbols = set(fetch_usdt_perp_symbols())
    try:
        r = httpx.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=HTTP_TIMEOUT)
        tickers = r.json()
        filtered = [
            t for t in tickers
            if t["symbol"] in all_symbols
        ]
        filtered.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in filtered[:top_n]]
    except Exception as e:
        log.error("fetch_top_symbols: %s", e)
        return list(all_symbols)[:top_n]


# ──────────────────────────────────────────────
# ASYNC DATA FETCH — PER SYMBOL
# ──────────────────────────────────────────────

def _pct(new_val, old_val) -> Optional[float]:
    """Percent change helper."""
    try:
        new_val = float(new_val)
        old_val = float(old_val)
        if old_val == 0:
            return None
        return (new_val - old_val) / abs(old_val) * 100.0
    except Exception:
        return None


async def fetch_symbol_data(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    symbol: str,
) -> dict:
    """
    Fetch price%, OI%, FR for all timeframes for one symbol.
    Returns a flat dict with column keys like price_5m, oi_1h, fr_last, fr_delta, etc.
    """
    result: dict = {"symbol": symbol}

    async with sem:
        # ── 1. KLINES (price change) for each TF ──────────────────────
        price_tasks = [
            _get(client, f"{BASE_URL}/fapi/v1/klines",
                 {"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT})
            for tf in TIMEFRAMES
        ]

        # ── 2. OI HISTORY for each TF ─────────────────────────────────
        oi_tasks = [
            _get(client, f"{OI_URL}/futures/data/openInterestHist",
                 {"symbol": symbol, "period": TF_TO_OI_PERIOD[tf], "limit": 2})
            for tf in TIMEFRAMES
        ]

        # ── 3. FUNDING RATE (last 2 entries) ──────────────────────────
        fr_task = _get(client, f"{BASE_URL}/fapi/v1/fundingRate",
                       {"symbol": symbol, "limit": FUNDING_LIMIT})

        # Fire all requests concurrently
        all_results = await asyncio.gather(
            *price_tasks, *oi_tasks, fr_task,
            return_exceptions=False
        )

    n_tf = len(TIMEFRAMES)
    price_results = all_results[:n_tf]
    oi_results    = all_results[n_tf:2*n_tf]
    fr_result     = all_results[2*n_tf]

    # ── Parse price% ──────────────────────────────────────────────────
    for tf, klines in zip(TIMEFRAMES, price_results):
        key = f"price_{tf}"
        try:
            # kline format: [openTime, open, high, low, close, ...]
            if klines and len(klines) >= 2:
                prev_close = klines[-2][4]
                last_close = klines[-1][4]
                result[key] = _pct(last_close, prev_close)
            else:
                result[key] = None
        except Exception:
            result[key] = None

    # ── Parse OI% ─────────────────────────────────────────────────────
    for tf, oi_data in zip(TIMEFRAMES, oi_results):
        key = f"oi_{tf}"
        try:
            if oi_data and len(oi_data) >= 2:
                prev_oi = oi_data[-2]["sumOpenInterestValue"]
                last_oi = oi_data[-1]["sumOpenInterestValue"]
                result[key] = _pct(last_oi, prev_oi)
            else:
                result[key] = None
        except Exception:
            result[key] = None

    # ── Parse Funding Rate ────────────────────────────────────────────
    try:
        if fr_result and len(fr_result) >= 1:
            last_fr  = float(fr_result[-1]["fundingRate"]) * 100   # → %
            result["fr_last"] = last_fr
            if len(fr_result) >= 2:
                prev_fr = float(fr_result[-2]["fundingRate"]) * 100
                result["fr_delta"] = last_fr - prev_fr
            else:
                result["fr_delta"] = None
        else:
            result["fr_last"]  = None
            result["fr_delta"] = None
    except Exception:
        result["fr_last"]  = None
        result["fr_delta"] = None

    return result


async def fetch_all_symbols(symbols: list[str]) -> list[dict]:
    """Fetch data for all symbols concurrently."""
    sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    async with httpx.AsyncClient() as client:
        tasks = [fetch_symbol_data(client, sem, sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    # Filter out exceptions — return whatever succeeded
    clean = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Symbol fetch exception: %s", r)
        else:
            clean.append(r)
    return clean


def run_fetch(symbols: list[str]) -> pd.DataFrame:
    """Sync wrapper around async fetch. Returns a DataFrame."""
    rows = asyncio.run(fetch_all_symbols(symbols))
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# UI HELPERS
# ──────────────────────────────────────────────

def _fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    return f"{val:+.2f}%"


def _color_pct(val) -> str:
    """Return CSS color string for a percent value."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "color: #888"
    if val > 0:
        return "color: #26a69a"   # green
    if val < 0:
        return "color: #ef5350"   # red
    return "color: #eee"


def style_df(df: pd.DataFrame):
    """Apply conditional formatting to the display dataframe (Pandas 2.x compatible)."""
    pct_cols = [c for c in df.columns if c != "Symbol"]
    # .map() replaces deprecated .applymap() in Pandas >= 2.1
    styler = df.style.map(_color_pct, subset=pct_cols)
    styler = styler.format({c: _fmt_pct for c in pct_cols}, na_rep="—")
    return styler


# ──────────────────────────────────────────────
# BUILD DISPLAY TABLE
# ──────────────────────────────────────────────

def build_display_df(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape raw data into a pretty display DataFrame."""
    if raw.empty:
        return pd.DataFrame()

    price_cols = [f"price_{tf}" for tf in TIMEFRAMES]
    oi_cols    = [f"oi_{tf}"    for tf in TIMEFRAMES]

    col_rename = {"symbol": "Symbol"}
    col_rename.update({f"price_{tf}": f"P% {tf}" for tf in TIMEFRAMES})
    col_rename.update({f"oi_{tf}":    f"OI% {tf}" for tf in TIMEFRAMES})
    col_rename["fr_last"]  = "FR Last"
    col_rename["fr_delta"] = "FR Δ"

    cols_order = (
        ["symbol"]
        + price_cols
        + oi_cols
        + ["fr_last", "fr_delta"]
    )

    # Only keep columns that exist
    cols_order = [c for c in cols_order if c in raw.columns]
    display = raw[cols_order].rename(columns=col_rename)
    return display


# ──────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Perp Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ── Sidebar controls ──────────────────────────────────────────────
    st.sidebar.title("⚙️ Settings")

    top_n = st.sidebar.slider(
        "Top N symbols (by 24h volume)", 10, 200, 50, step=10
    )
    refresh_sec = st.sidebar.slider(
        "Auto-refresh (seconds)", 5, 120, 20, step=5
    )
    search_query = st.sidebar.text_input("🔍 Search symbol", "").upper().strip()

    st.sidebar.markdown("---")
    st.sidebar.caption("Sort by")
    sort_col = st.sidebar.selectbox(
        "Column",
        options=(
            ["FR Last", "FR Δ"]
            + [f"P% {tf}" for tf in TIMEFRAMES]
            + [f"OI% {tf}" for tf in TIMEFRAMES]
        ),
        index=0,
    )
    sort_asc = st.sidebar.radio("Order", ["Descending ▼", "Ascending ▲"]) == "Ascending ▲"

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Last refresh: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

    # ── Title ─────────────────────────────────────────────────────────
    st.title("📊 Perp Dashboard — Binance USDⓈ-M Futures")
    st.caption(
        "Realtime price%, open interest%, and funding rate tracker "
        f"for top-{top_n} USDT perpetual pairs."
    )

    # ── Fetch data ────────────────────────────────────────────────────
    with st.spinner("Fetching market data…"):
        symbols = fetch_top_symbols_by_volume(top_n)

    if not symbols:
        st.error("Could not fetch symbol list. Check your connection.")
        st.stop()

    # Apply search filter before fetching (saves API calls)
    if search_query:
        symbols = [s for s in symbols if search_query in s]
        if not symbols:
            st.warning(f"No symbols match '{search_query}'.")
            st.stop()

    with st.spinner(f"Loading data for {len(symbols)} symbols…"):
        raw_df = run_fetch(symbols)

    if raw_df.empty:
        st.error("No data returned. Binance API may be unavailable.")
        st.stop()

    display_df = build_display_df(raw_df)

    # Apply sort
    if sort_col in display_df.columns:
        display_df = display_df.sort_values(
            sort_col, ascending=sort_asc, na_position="last"
        )

    # ── Metrics row ───────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    col1.metric("Symbols shown",   len(display_df))
    col2.metric("Refresh interval", f"{refresh_sec}s")
    non_null = display_df["FR Last"].notna().sum() if "FR Last" in display_df.columns else 0
    col3.metric("FR data available", f"{non_null}/{len(display_df)}")

    st.markdown("---")

    # ── Table ─────────────────────────────────────────────────────────
    # Numeric columns (all except Symbol)
    num_cols = [c for c in display_df.columns if c != "Symbol"]

    # background_gradient raises ValueError on NaN-only columns, so we use
    # .map() (replaces deprecated .applymap() in Pandas >= 2.1) instead.
    styled = (
        display_df.style
        .map(_color_pct, subset=num_cols)
        .format({c: _fmt_pct for c in num_cols}, na_rep="—")
    )

    st.dataframe(
        styled,
        use_container_width=True,
        height=min(60 + 36 * len(display_df), 800),
        hide_index=True,
    )

    # ── Legend ────────────────────────────────────────────────────────
    with st.expander("ℹ️ Column legend"):
        st.markdown("""
| Column | Description |
|--------|-------------|
| **P% {tf}** | Close-to-close price change % for timeframe |
| **OI% {tf}** | Open Interest change % for timeframe |
| **FR Last** | Latest funding rate (%) |
| **FR Δ** | Change from previous funding rate (pp) |

- Data source: **Binance USDⓈ-M Futures public REST API**  
- OI History endpoint: `/futures/data/openInterestHist`  
- Funding Rate endpoint: `/fapi/v1/fundingRate`
        """)

    # ── Auto-refresh ──────────────────────────────────────────────────
    # Clear caches that have shorter TTL so next run re-fetches
    time.sleep(0)   # yield to Streamlit
    st.markdown(
        f"""
        <script>
        setTimeout(function() {{ window.location.reload(); }}, {refresh_sec * 1000});
        </script>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
