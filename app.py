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
import nest_asyncio          # required on Streamlit Cloud (already-running event loop)
import pandas as pd
import streamlit as st

nest_asyncio.apply()          # allow asyncio.run() inside Streamlit's own loop

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

# Binance sometimes blocks certain cloud IPs; we try multiple hostnames in order
FAPI_HOSTS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
]

TIMEFRAMES = ["5m", "15m", "1h", "2h", "4h", "1d"]

KLINE_LIMIT   = 2
FUNDING_LIMIT = 2

CACHE_TTL_SYMBOLS = 3600   # symbol list changes rarely
CACHE_TTL_VOLUME  = 60     # 24h ticker
HTTP_TIMEOUT      = 15.0
SEMAPHORE_LIMIT   = 10     # conservative for cloud deployments

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("perp_dashboard")


# ──────────────────────────────────────────────
# FIND WORKING BASE URL (fallback chain)
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def find_working_base_url() -> tuple[str, str]:
    """
    Try each FAPI host with a lightweight ping to /fapi/v1/time.
    Returns (base_url, "") on success or ("", error_details) on total failure.
    """
    errors = []
    for host in FAPI_HOSTS:
        try:
            r = httpx.get(f"{host}/fapi/v1/time", timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            log.info("Using Binance host: %s", host)
            return host, ""
        except Exception as e:
            msg = f"{host}  →  {type(e).__name__}: {e}"
            log.warning("Host failed: %s", msg)
            errors.append(msg)
    return "", "\n".join(errors)


# ──────────────────────────────────────────────
# CACHED SYMBOL / VOLUME FETCHERS
# ──────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL_SYMBOLS, show_spinner=False)
def fetch_usdt_perp_symbols(base_url: str) -> list[str]:
    """Return all active USDT-margined perpetual symbols from exchangeInfo."""
    try:
        r = httpx.get(f"{base_url}/fapi/v1/exchangeInfo", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
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
        log.error("fetch_symbols error: %s", e)
        return []


@st.cache_data(ttl=CACHE_TTL_VOLUME, show_spinner=False)
def fetch_top_symbols_by_volume(base_url: str, top_n: int) -> list[str]:
    """Return top-N USDT perp symbols sorted by 24h quote volume (descending)."""
    all_symbols = set(fetch_usdt_perp_symbols(base_url))
    if not all_symbols:
        return []
    try:
        r = httpx.get(f"{base_url}/fapi/v1/ticker/24hr", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        tickers = r.json()
        filtered = [t for t in tickers if t["symbol"] in all_symbols]
        filtered.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in filtered[:top_n]]
    except Exception as e:
        log.error("fetch_top_symbols error: %s", e)
        return sorted(all_symbols)[:top_n]


# ──────────────────────────────────────────────
# ASYNC PER-SYMBOL FETCH
# ──────────────────────────────────────────────

def _pct(new_val, old_val) -> Optional[float]:
    """Safe percent change."""
    try:
        n, o = float(new_val), float(old_val)
        if o == 0:
            return None
        return (n - o) / abs(o) * 100.0
    except Exception:
        return None


async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict = None,
) -> Optional[dict | list]:
    """Single async GET; returns None on any error (never raises)."""
    try:
        r = await client.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("GET %s params=%s → %s", url, params, e)
        return None


async def fetch_symbol_data(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    symbol: str,
) -> dict:
    """
    Fetch price%, OI%, FR for all timeframes for one symbol concurrently.
    A failed sub-request leaves its column as None — never crashes the app.
    """
    result: dict = {"symbol": symbol}

    async with sem:
        # 1. Klines — last 2 candles per timeframe
        price_tasks = [
            _get(client, f"{base_url}/fapi/v1/klines",
                 {"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT})
            for tf in TIMEFRAMES
        ]
        # 2. OI history — last 2 data points per timeframe
        oi_tasks = [
            _get(client, f"{base_url}/futures/data/openInterestHist",
                 {"symbol": symbol, "period": tf, "limit": 2})
            for tf in TIMEFRAMES
        ]
        # 3. Funding rate — last 2 entries
        fr_task = _get(client, f"{base_url}/fapi/v1/fundingRate",
                       {"symbol": symbol, "limit": FUNDING_LIMIT})

        all_res = await asyncio.gather(*price_tasks, *oi_tasks, fr_task)

    n = len(TIMEFRAMES)
    price_res = all_res[:n]
    oi_res    = all_res[n : 2*n]
    fr_res    = all_res[2*n]

    # ── Parse price % ─────────────────────────────────────────────────
    for tf, klines in zip(TIMEFRAMES, price_res):
        try:
            result[f"price_{tf}"] = (
                _pct(klines[-1][4], klines[-2][4])
                if klines and len(klines) >= 2 else None
            )
        except Exception:
            result[f"price_{tf}"] = None

    # ── Parse OI % ────────────────────────────────────────────────────
    for tf, oi in zip(TIMEFRAMES, oi_res):
        try:
            result[f"oi_{tf}"] = (
                _pct(oi[-1]["sumOpenInterestValue"], oi[-2]["sumOpenInterestValue"])
                if oi and len(oi) >= 2 else None
            )
        except Exception:
            result[f"oi_{tf}"] = None

    # ── Parse Funding Rate ────────────────────────────────────────────
    try:
        if fr_res and len(fr_res) >= 1:
            last_fr = float(fr_res[-1]["fundingRate"]) * 100
            result["fr_last"]  = last_fr
            result["fr_delta"] = (
                last_fr - float(fr_res[-2]["fundingRate"]) * 100
                if len(fr_res) >= 2 else None
            )
        else:
            result["fr_last"] = result["fr_delta"] = None
    except Exception:
        result["fr_last"] = result["fr_delta"] = None

    return result


async def _fetch_all(base_url: str, symbols: list[str]) -> list[dict]:
    """Run all symbol fetches concurrently with a shared semaphore."""
    sem    = asyncio.Semaphore(SEMAPHORE_LIMIT)
    limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks   = [fetch_symbol_data(client, sem, base_url, s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    clean = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Symbol fetch exception: %s", r)
        else:
            clean.append(r)
    return clean


def run_fetch(base_url: str, symbols: list[str]) -> pd.DataFrame:
    """Synchronous entry point for async fetch (nest_asyncio-safe)."""
    rows = asyncio.run(_fetch_all(base_url, symbols))
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# DISPLAY HELPERS
# ──────────────────────────────────────────────

def _fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    return f"{val:+.2f}%"


def _color_pct(val) -> str:
    """CSS color string for a numeric percent value."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "color: #666"
    if val > 0:
        return "color: #26a69a; font-weight:600"
    if val < 0:
        return "color: #ef5350; font-weight:600"
    return "color: #ccc"


def build_display_df(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape raw data dict-df into the user-facing display DataFrame."""
    if raw.empty:
        return pd.DataFrame()
    col_rename = {"symbol": "Symbol"}
    col_rename.update({f"price_{tf}": f"P% {tf}" for tf in TIMEFRAMES})
    col_rename.update({f"oi_{tf}":    f"OI% {tf}" for tf in TIMEFRAMES})
    col_rename["fr_last"]  = "FR Last"
    col_rename["fr_delta"] = "FR Δ"

    cols = (
        ["symbol"]
        + [f"price_{tf}" for tf in TIMEFRAMES]
        + [f"oi_{tf}"    for tf in TIMEFRAMES]
        + ["fr_last", "fr_delta"]
    )
    cols = [c for c in cols if c in raw.columns]
    return raw[cols].rename(columns=col_rename)


# ──────────────────────────────────────────────
# MAIN APP
# ──────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Perp Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ── Sidebar ───────────────────────────────────────────────────────
    st.sidebar.title("⚙️ Settings")
    top_n       = st.sidebar.slider("Top N symbols (by 24h volume)", 10, 200, 50, step=10)
    refresh_sec = st.sidebar.slider("Auto-refresh (seconds)", 5, 120, 20, step=5)
    search_q    = st.sidebar.text_input("🔍 Search symbol", "").upper().strip()

    st.sidebar.markdown("---")
    st.sidebar.caption("Sort by")
    sort_col = st.sidebar.selectbox(
        "Column",
        ["FR Last", "FR Δ"]
        + [f"P% {tf}" for tf in TIMEFRAMES]
        + [f"OI% {tf}" for tf in TIMEFRAMES],
    )
    sort_asc = st.sidebar.radio("Order", ["Descending ▼", "Ascending ▲"]) == "Ascending ▲"
    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"Last refresh: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )

    # ── Title ─────────────────────────────────────────────────────────
    st.title("📊 Perp Dashboard — Binance USDⓈ-M Futures")
    st.caption(
        f"Realtime price%, open interest%, and funding rate · top-{top_n} USDT perpetuals"
    )

    # ── Resolve working Binance host ──────────────────────────────────
    with st.spinner("Connecting to Binance API…"):
        base_url, conn_err = find_working_base_url()

    if not base_url:
        st.error(
            "❌ **Cannot reach Binance Futures API from this server.**\n\n"
            "Streamlit Cloud's IP range is blocked by Binance for USDⓈ-M Futures endpoints. "
            "This is a known infrastructure restriction, not a code bug.\n\n"
            "**Recommended workarounds:**\n"
            "- **Run locally:** `streamlit run app.py` (your home IP works fine)\n"
            "- **Deploy on a VPS** (Hetzner / DigitalOcean / Vultr) outside blocked IP ranges\n"
            "- **Use a proxy** — add `proxies={'all': 'http://your-proxy:port'}` to httpx calls\n\n"
            f"<details><summary>Technical details</summary>\n\n```\n{conn_err}\n```\n</details>",
            icon="🚫",
        )
        st.stop()

    st.sidebar.success(f"✅ `{base_url.replace('https://','')}`")

    # ── Symbol list ───────────────────────────────────────────────────
    with st.spinner("Loading symbol list…"):
        symbols = fetch_top_symbols_by_volume(base_url, top_n)

    if not symbols:
        st.error("Could not fetch symbol list. Binance API may be temporarily unavailable.")
        st.stop()

    if search_q:
        symbols = [s for s in symbols if search_q in s]
        if not symbols:
            st.warning(f"No symbols match **{search_q}**.")
            st.stop()

    # ── Market data ───────────────────────────────────────────────────
    with st.spinner(f"Fetching data for {len(symbols)} symbols…"):
        raw_df = run_fetch(base_url, symbols)

    if raw_df.empty:
        st.error("No market data returned. Try again in a moment.")
        st.stop()

    display_df = build_display_df(raw_df)

    if sort_col in display_df.columns:
        display_df = display_df.sort_values(
            sort_col, ascending=sort_asc, na_position="last"
        )

    # ── Metrics row ───────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Symbols shown",   len(display_df))
    c2.metric("Refresh interval", f"{refresh_sec}s")
    fr_ok = display_df["FR Last"].notna().sum() if "FR Last" in display_df.columns else 0
    c3.metric("FR data available", f"{fr_ok}/{len(display_df)}")
    st.markdown("---")

    # ── Data table ────────────────────────────────────────────────────
    num_cols = [c for c in display_df.columns if c != "Symbol"]
    styled = (
        display_df.style
        .map(_color_pct, subset=num_cols)          # Pandas >= 2.1 (.map not .applymap)
        .format({c: _fmt_pct for c in num_cols}, na_rep="—")
    )
    st.dataframe(
        styled,
        use_container_width=True,
        height=min(60 + 36 * len(display_df), 820),
        hide_index=True,
    )

    # ── Legend ────────────────────────────────────────────────────────
    with st.expander("ℹ️ Column legend"):
        st.markdown("""
| Column | Description |
|--------|-------------|
| **P% {tf}** | Close-to-close price change % for that timeframe |
| **OI% {tf}** | Open Interest change % for that timeframe |
| **FR Last** | Latest funding rate (converted to %) |
| **FR Δ** | Delta vs previous funding rate (percentage points) |

Data source: **Binance USDⓈ-M Futures public REST API** (no API key required)
        """)

    # ── Auto-refresh via JS ───────────────────────────────────────────
    st.markdown(
        f"<script>setTimeout(()=>window.location.reload(),{refresh_sec*1000});</script>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
