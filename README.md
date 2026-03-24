# Quantum Terminal

Local MT5-inspired workspace with a glass UI and four chart panes:

- `M5`
- `M15`
- `H1`
- `H4`

## Run

```powershell
python server.py
```

Before opening the site:

- open MetaTrader 5
- log into your broker account
- make sure the symbol is visible in Market Watch

Then open:

```text
http://127.0.0.1:8090
```

Notes:

- the app auto-resolves broker suffixes such as `XAUUSD.m`
- the initial load can request `ALL` available bars from MT5 for each timeframe
- the board then syncs recent candles every 1 second while live sync is enabled
- the latest candle updates between refreshes from the live MT5 tick feed
- the bars input accepts `ALL` or a numeric cap
- charts support mouse-wheel zoom, drag-to-pan, and hover OHLC
