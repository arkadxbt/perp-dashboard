# 📊 Perp Dashboard — Bybit Linear Perpetuals

Realtime tracker for Bybit USDT-margined linear perpetual pairs.

## Architecture

```
Streamlit Cloud Server          User's Browser
─────────────────────           ───────────────────────────
app.py                  →       HTML + JS dashboard
(serves static HTML)            │
                                └── fetch() → api.bybit.com
                                    (public, no IP block)
```

**Why browser-side?** Both Binance and Bybit block Streamlit Cloud's AWS IP range (HTTP 403/451). By moving all API calls to the browser via JavaScript `fetch()`, we bypass the server-side block entirely. Bybit's public market data endpoints support CORS.

## Features

- Top-N USDT linear perpetuals by 24h turnover
- 6 timeframes: 5m · 15m · 1h · 2h · 4h · 1d
- Price % (close-to-close per TF)
- OI % (open interest change per TF; 2h derived from 1h)
- Funding Rate: last value + delta vs previous
- Sortable columns, search, auto-refresh (5–120s)

## Deploy

```bash
# requirements.txt only needs streamlit
pip install streamlit

# local run
streamlit run app.py

# Streamlit Cloud: push to GitHub, deploy normally
# No secrets or API keys needed
```

## Column notes

| Column | Source |
|--------|--------|
| P% {tf} | `/v5/market/kline` interval=5/15/60/120/240/D |
| OI% {tf} | `/v5/market/open-interest` intervalTime=5min/15min/1h/4h/1d |
| OI% 2h* | Derived: 1h OI index[0] vs index[2] |
| FR Last | `/v5/market/funding/history` last entry × 100 |
| FR Δ | last FR − previous FR (pp) |
