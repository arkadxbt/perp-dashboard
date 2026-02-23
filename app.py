"""
Perp Dashboard — Bybit V5 Linear Perpetual Tracker
Author : arkadxbt
Repo   : https://github.com/arkadxbt/perp-dashboard
Deploy : Streamlit Community Cloud

Endpoints used (all public, no API key required):
  GET https://api.bybit.com/v5/market/tickers          → symbol list + 24h volume
  GET https://api.bybit.com/v5/market/kline            → OHLC (price %)
  GET https://api.bybit.com/v5/market/open-interest    → OI history
  GET https://api.bybit.com/v5/market/funding/history  → funding rate history

Bybit V5 kline interval mapping:
  5m→"5", 15m→"15", 1h→"60", 2h→"120", 4h→"240", 1d→"D"

Bybit V5 OI intervalTime values:
  5min, 15min, 1h, 4h, 1d
  ⚠ "2h" does NOT exist in OI endpoint.
  Strategy: fetch 1h OI with limit=3, aggregate last-two 1h candles each side
  to approximate a 2h period change. Column is labelled "OI% 2h*" to indicate
  it is derived rather than native.
"""

import math
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import nest_asyncio
import pandas as pd
import streamlit as st

nest_asyncio.apply()   # Streamlit Cloud runs its own event loop; this patches it

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

BYBIT_BASE   = "https://api.bybit.com"
BYBIT_BASE2  = "https://api.bytick.com"   # fallback CDN

# Timeframes shown in UI
TIMEFRAMES = ["5m", "15m", "1h", "2h", "4h", "1d"]

# Bybit kline "interval" param (minutes for intraday, "D" for daily)
TF_TO_KLINE_INTERVAL = {
    "5m" : "5",
    "15m": "15",
    "1h" : "60",
    "2h" : "120",
    "4h" : "240",
    "1d" : "D",
}

# Bybit OI "intervalTime" param — NOTE: no native 2h
TF_TO_OI_INTERVAL: dict[str, Optional[str]] = {
    "5m" : "5min",
    "15m": "15min",
    "1h" : "1h",
    "2h" : None,    # ← derived from 1h (see fetch_symbol_data)
    "4h" : "4h",
    "1d" : "1d",
}

HTTP_TIMEOUT     = 15.0
SEMAPHORE_LIMIT  = 10      # max concurrent in-flight requests
KLINE_LIMIT      = 3       # need 2 candles; fetch 3 as buffer
OI_LIMIT         = 4       # need 2 points; fetch 4 for 2h derivation

CACHE_TTL_TICKERS = 30     # 24h ticker cache
CACHE_TTL_SYMBOLS = 1800   # symbol list cache

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("perp_dashboard")


# ──────────────────────────────────────────────
# CONNECTION CHECK
# ──────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def find_working_base() -> tuple[str, str]:
    """
    Ping /v5/market/time on each Bybit host.
    Returns (working_url, "") or ("", error_detail).
    """
    errors = []
    for host in [BYBIT_BASE, BYBIT_BASE2]:
        try:
            r = httpx.get(f"{host}/v5/market/time", timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            log.info("Bybit host OK: %s", host)
            return host, ""
        except Exception as e:
            msg = f"{host}  →  {type(e).__name__}: {e}"
            log.warning(msg)
            errors.append(msg)
    return "", "\n".join(errors)


# ──────────────────────────────────────────────
# SYMBOL & VOLUME FETCHERS
# ──────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL_TICKERS, show_spinner=False)
def fetch_tickers(base: str) -> list[dict]:
    """
    Fetch all linear perpetual tickers from Bybit.
    Returns list of ticker dicts; includes turnover24h for volume sort.
    """
    try:
        r = httpx.get(
            f"{base}/v5/market/tickers",
            params={"category": "linear"},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            log.error("Bybit tickers retCode %s: %s", data.get("retCode"), data.get("retMsg"))
            return []
        return data["result"]["list"]
    except Exception as e:
        log.error("fetch_tickers: %s", e)
        return []


def get_top_symbols(base: str, top_n: int) -> list[str]:
    """
    Filter USDT linear perpetuals, sort by 24h quote turnover, return top-N symbols.
    """
    tickers = fetch_tickers(base)
    if not tickers:
        return []

    # Keep only USDT-quoted perpetuals (symbol ends with USDT, no delivery date suffix)
    usdt_perps = [
        t for t in tickers
        if t.get("symbol", "").endswith("USDT")
        and "-" not in t.get("symbol", "")   # exclude delivery futures like BTC-31MAR25
    ]

    # Sort by turnover24h descending (USD volume)
    usdt_perps.sort(
        key=lambda x: float(x.get("turnover24h") or 0),
        reverse=True,
    )
    return [t["symbol"] for t in usdt_perps[:top_n]]


# ──────────────────────────────────────────────
# ASYNC HTTP HELPER
# ──────────────────────────────────────────────

async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
) -> Optional[dict]:
    """GET with error swallowing; always returns parsed JSON or None."""
    try:
        r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            log.debug("Bybit non-zero retCode %s for %s %s", data.get("retCode"), url, params)
            return None
        return data
    except Exception as e:
        log.debug("GET %s %s → %s", url, params, e)
        return None


# ──────────────────────────────────────────────
# CALC HELPERS
# ──────────────────────────────────────────────

def _pct(new_val, old_val) -> Optional[float]:
    try:
        n, o = float(new_val), float(old_val)
        if o == 0:
            return None
        return (n - o) / abs(o) * 100.0
    except Exception:
        return None


def _parse_kline_pct(data: Optional[dict]) -> Optional[float]:
    """
    Bybit kline response list is in REVERSE chronological order:
      list[0] = newest candle, list[1] = previous candle
    Each entry: [startTime, open, high, low, close, volume, turnover]
    """
    try:
        lst = data["result"]["list"]
        if not lst or len(lst) < 2:
            return None
        close_now  = lst[0][4]   # index 4 = close
        close_prev = lst[1][4]
        return _pct(close_now, close_prev)
    except Exception:
        return None


def _parse_oi_pct(data: Optional[dict], derive_2h: bool = False) -> Optional[float]:
    """
    Bybit OI response list is in REVERSE chronological order:
      list[0] = newest, list[1] = previous
    Each entry: {"openInterest": "...", "timestamp": "..."}

    For 2h derivation (derive_2h=True), we expect 1h data with ≥3 points:
      list[0] = now, list[1] = 1h ago, list[2] = 2h ago
      We compare list[0] vs list[2] to get ~2h change.
    """
    try:
        lst = data["result"]["list"]
        if not lst:
            return None
        if derive_2h:
            if len(lst) < 3:
                return None
            return _pct(lst[0]["openInterest"], lst[2]["openInterest"])
        else:
            if len(lst) < 2:
                return None
            return _pct(lst[0]["openInterest"], lst[1]["openInterest"])
    except Exception:
        return None


def _parse_fr(data: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (fr_last_pct, fr_delta_pp).
    Bybit funding/history list is in DESC order: list[0] = most recent.
    fundingRate is a raw decimal (e.g. 0.0001 = 0.01%).
    """
    try:
        lst = data["result"]["list"]
        if not lst:
            return None, None
        last_fr = float(lst[0]["fundingRate"]) * 100   # → %
        if len(lst) >= 2:
            prev_fr = float(lst[1]["fundingRate"]) * 100
            delta   = last_fr - prev_fr
        else:
            delta = None
        return last_fr, delta
    except Exception:
        return None, None


# ──────────────────────────────────────────────
# PER-SYMBOL ASYNC FETCH
# ──────────────────────────────────────────────

async def fetch_symbol_data(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base: str,
    symbol: str,
) -> dict:
    """
    Concurrently fetch all price%, OI%, and FR data for one symbol.
    Any sub-request failure results in None for that cell — never crashes.
    """
    result: dict = {"symbol": symbol}

    async with sem:
        # ── 1. Klines for every timeframe ──────────────────────────────
        kline_tasks = {
            tf: _get(client, f"{base}/v5/market/kline", {
                "category": "linear",
                "symbol"  : symbol,
                "interval": TF_TO_KLINE_INTERVAL[tf],
                "limit"   : KLINE_LIMIT,
            })
            for tf in TIMEFRAMES
        }

        # ── 2. OI for native timeframes ────────────────────────────────
        # For "2h" we fetch 1h OI with extra points and derive it.
        oi_tasks = {}
        for tf in TIMEFRAMES:
            interval = TF_TO_OI_INTERVAL[tf]
            if tf == "2h":
                # Fetch 1h with enough points to derive 2h
                oi_tasks[tf] = _get(client, f"{base}/v5/market/open-interest", {
                    "category"    : "linear",
                    "symbol"      : symbol,
                    "intervalTime": "1h",
                    "limit"       : 4,   # need index 0 and 2
                })
            elif interval is not None:
                oi_tasks[tf] = _get(client, f"{base}/v5/market/open-interest", {
                    "category"    : "linear",
                    "symbol"      : symbol,
                    "intervalTime": interval,
                    "limit"       : OI_LIMIT,
                })

        # ── 3. Funding rate ────────────────────────────────────────────
        fr_task = _get(client, f"{base}/v5/market/funding/history", {
            "category": "linear",
            "symbol"  : symbol,
            "limit"   : 2,
        })

        # Fire everything concurrently
        all_keys    = list(kline_tasks) + list(oi_tasks) + ["fr"]
        all_coros   = (
            list(kline_tasks.values())
            + list(oi_tasks.values())
            + [fr_task]
        )
        all_results = await asyncio.gather(*all_coros, return_exceptions=True)

    # Unpack results
    n_kline = len(kline_tasks)
    n_oi    = len(oi_tasks)

    kline_results = dict(zip(kline_tasks.keys(), all_results[:n_kline]))
    oi_results    = dict(zip(oi_tasks.keys(),    all_results[n_kline:n_kline + n_oi]))
    fr_raw        = all_results[n_kline + n_oi]

    # ── Parse price % ──────────────────────────────────────────────────
    for tf in TIMEFRAMES:
        raw = kline_results.get(tf)
        result[f"price_{tf}"] = (
            _parse_kline_pct(raw)
            if not isinstance(raw, Exception) and raw is not None
            else None
        )

    # ── Parse OI % ─────────────────────────────────────────────────────
    for tf in TIMEFRAMES:
        raw = oi_results.get(tf)
        if isinstance(raw, Exception) or raw is None:
            result[f"oi_{tf}"] = None
        elif tf == "2h":
            result[f"oi_{tf}"] = _parse_oi_pct(raw, derive_2h=True)
        else:
            result[f"oi_{tf}"] = _parse_oi_pct(raw)

    # ── Parse Funding Rate ─────────────────────────────────────────────
    if isinstance(fr_raw, Exception) or fr_raw is None:
        result["fr_last"] = result["fr_delta"] = None
    else:
        result["fr_last"], result["fr_delta"] = _parse_fr(fr_raw)

    return result


async def _fetch_all(base: str, symbols: list[str]) -> list[dict]:
    sem    = asyncio.Semaphore(SEMAPHORE_LIMIT)
    limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks   = [fetch_symbol_data(client, sem, base, s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    clean = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Symbol exception: %s", r)
        else:
            clean.append(r)
    return clean


def run_fetch(base: str, symbols: list[str]) -> pd.DataFrame:
    """Sync entry-point for the async fetch pipeline."""
    rows = asyncio.run(_fetch_all(base, symbols))
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# DISPLAY / FORMATTING
# ──────────────────────────────────────────────

def _fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    return f"{val:+.2f}%"


def _color_pct(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "color: #555"
    if val > 0:
        return "color: #26a69a; font-weight:600"
    if val < 0:
        return "color: #ef5350; font-weight:600"
    return "color: #ccc"


def build_display_df(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()

    # "2h" OI is derived → label with asterisk to signal approximation
    col_rename = {"symbol": "Symbol"}
    for tf in TIMEFRAMES:
        col_rename[f"price_{tf}"] = f"P% {tf}"
        col_rename[f"oi_{tf}"]    = f"OI% {tf}" if tf != "2h" else "OI% 2h*"
    col_rename["fr_last"]  = "FR Last"
    col_rename["fr_delta"] = "FR Δ"

    col_order = (
        ["symbol"]
        + [f"price_{tf}" for tf in TIMEFRAMES]
        + [f"oi_{tf}"    for tf in TIMEFRAMES]
        + ["fr_last", "fr_delta"]
    )
    col_order = [c for c in col_order if c in raw.columns]
    return raw[col_order].rename(columns=col_rename)


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

    # ── Sidebar ─────────────────────────────────────────────────────
    st.sidebar.title("⚙️ Settings")

    top_n = st.sidebar.slider(
        "Top N symbols (by 24h volume)", 10, 200, 50, step=10
    )
    refresh_sec = st.sidebar.slider(
        "Auto-refresh (seconds)", 5, 120, 20, step=5
    )
    search_q = st.sidebar.text_input("🔍 Search symbol", "").upper().strip()

    st.sidebar.markdown("---")
    st.sidebar.caption("Sort by")

    # Build sort column choices using the same labels as build_display_df
    oi_2h_label = "OI% 2h*"
    sort_choices = (
        ["FR Last", "FR Δ"]
        + [f"P% {tf}" for tf in TIMEFRAMES]
        + [f"OI% {tf}" if tf != "2h" else oi_2h_label for tf in TIMEFRAMES]
    )
    sort_col = st.sidebar.selectbox("Column", sort_choices)
    sort_asc = (
        st.sidebar.radio("Order", ["Descending ▼", "Ascending ▲"]) == "Ascending ▲"
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"Last refresh: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )

    # ── Header ──────────────────────────────────────────────────────
    st.title("📊 Perp Dashboard — Bybit Linear Perpetuals")
    st.caption(
        f"Realtime price%, open interest%, and funding rate · "
        f"top-{top_n} USDT perpetuals · powered by Bybit V5 API"
    )

    # ── Connection check ─────────────────────────────────────────────
    with st.spinner("Connecting to Bybit API…"):
        base, conn_err = find_working_base()

    if not base:
        st.error(
            "❌ **Cannot reach Bybit API.**\n\n"
            f"```\n{conn_err}\n```"
        )
        st.stop()

    st.sidebar.success(f"✅ `{base.replace('https://','')}`")

    # ── Top-N symbol list ────────────────────────────────────────────
    with st.spinner("Loading symbol list…"):
        symbols = get_top_symbols(base, top_n)

    if not symbols:
        st.error("Could not fetch symbol list from Bybit.")
        st.stop()

    if search_q:
        symbols = [s for s in symbols if search_q in s]
        if not symbols:
            st.warning(f"No symbols match **{search_q}**.")
            st.stop()

    # ── Market data fetch ────────────────────────────────────────────
    with st.spinner(f"Fetching data for {len(symbols)} symbols…"):
        raw_df = run_fetch(base, symbols)

    if raw_df.empty:
        st.error("No data returned. Bybit API may be temporarily unavailable.")
        st.stop()

    display_df = build_display_df(raw_df)

    if sort_col in display_df.columns:
        display_df = display_df.sort_values(
            sort_col, ascending=sort_asc, na_position="last"
        )

    # ── Metrics row ──────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Symbols shown",    len(display_df))
    c2.metric("Refresh interval", f"{refresh_sec}s")
    fr_ok = (
        display_df["FR Last"].notna().sum()
        if "FR Last" in display_df.columns else 0
    )
    c3.metric("FR available", f"{fr_ok}/{len(display_df)}")
    st.markdown("---")

    # ── Table ────────────────────────────────────────────────────────
    num_cols = [c for c in display_df.columns if c != "Symbol"]
    styled = (
        display_df.style
        .map(_color_pct, subset=num_cols)           # Pandas ≥ 2.1
        .format({c: _fmt_pct for c in num_cols}, na_rep="—")
    )
    st.dataframe(
        styled,
        use_container_width=True,
        height=min(60 + 36 * len(display_df), 820),
        hide_index=True,
    )

    # ── Legend ───────────────────────────────────────────────────────
    with st.expander("ℹ️ Column legend & notes"):
        st.markdown("""
| Column | Description |
|--------|-------------|
| **P% {tf}** | Close-to-close price change % for that timeframe |
| **OI% {tf}** | Open Interest change % (native Bybit OI history) |
| **OI% 2h\*** | OI change % over ~2h — *derived* from 1h OI data (index 0 vs index 2). Native 2h OI is not available in Bybit V5 API. |
| **FR Last** | Latest funding rate (converted to %) |
| **FR Δ** | Change vs previous funding rate (percentage points) |

**Data source:** Bybit V5 public REST API — `api.bybit.com`  
**No API key required.**  
**Rate limits:** Bybit allows 120 req/min on market data endpoints. At top-50 with ~9 requests/symbol this dashboard makes ~450 requests per refresh cycle — well within limits at default 20s interval.
        """)

    # ── Auto-refresh ─────────────────────────────────────────────────
    st.markdown(
        f"<script>setTimeout(()=>window.location.reload(),{refresh_sec * 1000});</script>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
