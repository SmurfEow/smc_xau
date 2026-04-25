# Quantum

Quantum is a local MT5 auto-trading workspace for `XAUUSD` powered by a **Double Bollinger Band (DBB) Momentum** strategy.

- live M15 signal detection tied to MT5 bar closes
- autonomous entry and exit execution through MT5
- Neural Alpha frontend as the control and monitoring layer
- Google Sheets sync for trade history

The server is the engine. The Neural Alpha dashboard is the display and control layer.

---

## Current Strategy — Double Bollinger Band (DBB) Momentum

### Parameters

| Parameter | Value |
|---|---|
| Timeframe | M15 |
| BB Length | 20 |
| Inner Band (1SD) | `basis ± 1 × stdev` |
| Outer Band (2SD) | `basis ± 2 × stdev` |
| Band Expansion Threshold | 1.02 |
| Min Hold Bars | 5 (75 minutes) |
| Entry Window | No new entries at or after 22:00 broker time |
| Lot Size | 0.10 |

### Entry Logic

- **Long:** M15 close crosses above the 1SD upper band (`u1`) AND bands are expanding
- **Short:** M15 close crosses below the 1SD lower band (`l1`) AND bands are expanding
- Entries are blocked at or after 22:00 broker time

### Exit Logic

- **Long exit:** M15 close crosses back under `u1` — only allowed after 5 bars (75 min) have passed since entry
- **Short exit:** M15 close crosses back over `l1` — only allowed after 5 bars (75 min) have passed since entry

### Band Expansion Filter

A new entry is only valid when the current band width is more than 2% wider than the previous bar's band width:

```
expanding = (u1 - l1) > (u1_prev - l1_prev) × 1.02
```

This filters out low-momentum, choppy markets and only enters during genuine breakout moves.

### Backtest Results (TradingView, Feb–Apr 2026, M15)

| Metric | Value |
|---|---|
| Total P&L | +$12,326 |
| Return | +122.80% |
| Max Drawdown | 15.36% |
| Win Rate | 49.07% (105/214) |
| Profit Factor | 1.728 |
| Avg Win | $276.69 |
| Avg Loss | $154.23 |
| Avg Bars Held | 12 |

### Why It Works

- Entries only fire during expanding momentum — avoids choppy consolidation
- Dynamic exits (crossback through u1/l1) let winners run and cut losers naturally
- Min hold bars prevents whipsaw exits on noise right after entry
- Winners naturally hold for ~13 bars, losers exit at ~10 bars — the hold window filters fast losers

---

## Architecture

### Core Flow

1. `server.py` starts the local HTTP server on `127.0.0.1:8090`
2. A background worker watches for new M15 bar closes (polls every 5 seconds)
3. On each new bar close, DBB bands are computed from live MT5 M15 data
4. If a crossover/crossunder signal fires → MT5 order is placed immediately (within ~5 seconds of bar close)
5. Exit check runs before every entry check — if min hold is met and price crosses back, position is closed
6. All decisions and trades are logged for review

### Signal Timing

The autonomous worker fires on **M15 bar close**, not on a fixed timer. This means execution happens within ~5 seconds of bar close — effectively the same as TradingView alert-based trading without requiring a paid subscription.

---

## Files

### Backend

- `server.py`
  Main server, MT5 integration, DBB signal engine, autonomous bar-close worker, logging, auto-trade execution

### Frontend

- `neural_website_repo/`
  Neural Alpha frontend (React/Vite). The compiled `dist/` folder is served by `server.py` automatically.

### Strategy

- `dbb_momentum_baseline.txt`
  Pine Script v5 source for the DBB strategy — paste into TradingView for visual backtesting

### Backtest

- `dbb_backtest.py`
  Python backtest using yfinance data (no MT5 needed). Set `INTERVAL` and `PERIOD` at the top.

### Logs

- `ai_trade_decisions.json` — every decision cycle output
- `ai_trade_reviews.json` — executed trade review records
- `ai_logic_audit.json` — pipeline and validation audit trail

### Local Runtime Data

- `manual_news_calendar.json` — manual news block events for auto-trade guard
- `google_sheet_sync_state.json` — last sync state for Google Sheets

---

## Auto-Trade

### Enabling

1. Run `server.py`
2. Open `http://localhost:8090`
3. Enable autotrade from the Neural Alpha dashboard

Once enabled, the engine runs fully autonomously — no browser tab needs to stay open.

### Guards

Even when autotrade is enabled, an order will not be placed if:

- A trade is already open
- The entry window is closed (at or after 22:00 broker time)
- A manual news block is active
- The duplicate signal guard fires
- Auto-trade is toggled off

### Lot Size

Current live lot size: `0.10` (10 oz XAUUSD = $10 per $1 price move)

---

## Autonomous Behavior

- **Trigger:** new M15 bar close (detected within ~5 seconds)
- **Symbol:** `XAUUSD` (or broker equivalent e.g. `XAUUSD.m`)
- **Exit check:** runs before entry check on every bar
- **Entry check:** crossover/crossunder + expansion filter + entry window

---

## Manual News Block

The dashboard includes a broker-time news calendar.

- events are entered in MT5 broker time
- each saved event hard-blocks auto-trade for 45 minutes before and after
- past broker-time dates cannot be added as new events

---

## Google Sheets Sync

`google_sheet_sync.py` publishes closed-trade results to Google Sheets.

### Local Usage

```powershell
python google_sheet_sync.py
python google_sheet_sync.py --json
python google_sheet_sync.py --date 2026-03-31
```

### Push to Google Sheets

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON='C:\path\to\service-account.json'
$env:GOOGLE_SHEET_ID='your-google-sheet-id'
python google_sheet_sync.py --push
```

Auto-sync on trade close is active when credentials are set and `server.py` is running.

---

## API Endpoints

- `/api/sync` — live board and timeframe data
- `/api/tick` — latest tick
- `/api/ai/status` — latest strategy output and autonomous status
- `/api/ai/trade` — manual on-demand setup evaluation
- `/api/autotrade/status` — autotrade state
- `/api/autotrade/config` — autotrade configuration
- `/api/history/dashboard` — dashboard analytics and MT5 closed history

---

## Run

```powershell
python server.py
```

or

```powershell
start_server.bat
```

Then open: `http://localhost:8090`

---

## Requirements

- Python 3.10+
- MetaTrader 5 terminal (open and logged in)
- `MetaTrader5` Python package
- `yfinance` (optional, for offline backtest only)
