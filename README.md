# 📊 Perp Dashboard

Binance USDⓈ-M Futures perpetual pair tracker built with **Streamlit**.  
Tracks **Price %**, **Open Interest %**, and **Funding Rate** for the top-N USDT perpetuals across 6 timeframes (5m · 15m · 1h · 2h · 4h · 1d).

---

## Features

| Feature | Detail |
|---------|--------|
| **Price %** | Close-to-close change per timeframe |
| **OI %** | Open Interest change per timeframe |
| **Funding Rate** | Latest FR + delta vs previous |
| **Top-N filter** | 10 – 200 symbols by 24h volume |
| **Auto-refresh** | 5 – 120 second interval |
| **Search** | Filter by symbol name |
| **Sort** | Any column, ascending or descending |
| **Color coding** | Green / Red gradient on all % columns |

---

## Deploy to Streamlit Community Cloud (free)

### 1. Fork / push to GitHub

```bash
git clone https://github.com/arkadxbt/perp-dashboard
cd perp-dashboard
# copy app.py, requirements.txt here
git add .
git commit -m "init"
git push origin main
```

### 2. Connect to Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Select your GitHub repo `arkadxbt/perp-dashboard`
3. Branch: `main` | Main file: `app.py`
4. Click **Deploy** — it's live in ~60 seconds ✅

No secrets or API keys needed (Binance public REST).

---

## Local development

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Rate limit & performance notes

### Binance USDⓈ-M REST limits (as of 2024)
- **1200 request weight / minute** per IP  
- Each `/fapi/v1/klines` call = weight 1  
- Each `/futures/data/openInterestHist` call = weight 1  
- Each `/fapi/v1/fundingRate` call = weight 1

### How this app manages rate limits

| Technique | Implementation |
|-----------|----------------|
| **Async + semaphore** | All per-symbol requests run concurrently, limited to 20 in-flight at once (`SEMAPHORE_LIMIT`) |
| **Streamlit `@cache_data` TTL** | Symbol list: 1h · 24h ticker: 60s · Per-symbol data: 15s (avoid re-fetching on every widget interaction) |
| **Top-N filtering** | Fetch only the highest-volume symbols; default 50 means ~50×9=450 requests per refresh cycle |
| **Minimal kline fetch** | `limit=2` on klines — only the last two candles needed for close-to-close % |
| **Error isolation** | `asyncio.gather` wraps each symbol independently; a failed symbol returns `None` values rather than crashing the app |

### Scaling recommendations

- **Top 50 + 20s refresh** → safe for personal use (~27 req/s peak, well under limits)  
- **Top 100 + 10s refresh** → borderline; add a server-side cache (Redis / SQLite) if deploying publicly  
- **Round-robin update** (optional advanced): instead of refreshing all TFs at once, cycle one TF per tick to cut requests by 6×  
- **WebSocket upgrade**: for production, replace REST kline polling with `wss://fstream.binance.com` streams for zero poll overhead

---

## Project structure

```
perp-dashboard/
├── app.py            # Main Streamlit application
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

---

## License

MIT
