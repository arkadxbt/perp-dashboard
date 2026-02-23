# 📊 Perp Dashboard — Bybit Linear Perpetuals

Realtime tracker for Bybit USDT-margined linear perpetual pairs.  
Tracks **Price %**, **Open Interest %**, and **Funding Rate** across 6 timeframes.

---

## Data sources (Bybit V5 public REST — no API key needed)

| Data | Endpoint |
|------|----------|
| Symbol list + 24h volume | `GET /v5/market/tickers?category=linear` |
| Price % (OHLC) | `GET /v5/market/kline` |
| Open Interest % | `GET /v5/market/open-interest` |
| Funding Rate | `GET /v5/market/funding/history` |

### Timeframe mapping

| UI label | Kline interval | OI intervalTime |
|----------|---------------|-----------------|
| 5m  | `5`   | `5min` |
| 15m | `15`  | `15min` |
| 1h  | `60`  | `1h` |
| 2h  | `120` | *derived from 1h* (`OI% 2h*`) |
| 4h  | `240` | `4h` |
| 1d  | `D`   | `1d` |

> **Note:** Bybit V5 OI endpoint has no native `2h` interval.  
> `OI% 2h*` is approximated by comparing OI at `t=now` vs `t=2h ago` using 1h-resolution data.

---

## Deploy to Streamlit Community Cloud (free)

```bash
# 1. Push to GitHub
git clone https://github.com/arkadxbt/perp-dashboard
cd perp-dashboard
# copy app.py, requirements.txt, README.md
git add . && git commit -m "bybit v5 rewrite" && git push

# 2. Go to share.streamlit.io → New app
#    Repo: arkadxbt/perp-dashboard | Branch: main | File: app.py
#    Click Deploy ✅
```

No secrets or environment variables needed.

---

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Rate limit notes

- Bybit market data: **120 requests/min** per IP (conservative estimate)
- Top-50 symbols × ~9 requests = 450 req/cycle
- At 20s refresh → ~22 req/s → well within limits
- `SEMAPHORE_LIMIT=10` caps concurrent in-flight requests
