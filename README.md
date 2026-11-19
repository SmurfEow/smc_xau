# Quantum

Quantum is a local MT5 auto-trading workspace for `XAUUSD` powered by a Double Bollinger Band (DBB) Momentum strategy.

- Live MT5 candles and ticks
- Neural Alpha React dashboard for monitoring and control
- One Time Window range, breakout, stop-loss, and 1.5R target plotting
- Optional dashboard-driven auto-trade execution through MT5
- Trade/audit logs and Google Sheets sync support

The Python server is the MT5/API engine. The Neural Alpha dashboard is the live chart, control, and local One Time Window signal layer.

---

## Current Strategy: One Time Window

### Setup

| Parameter | Value |
|---|---|
| Range timeframe | M15 |
| Range candle | 08:15–08:30 New York time |
| Trigger timeframe | M5 |
| Trigger | First closed M5 candle whose close is outside the range |
| Reward | 1.5R |
| Stop | Latest confirmed inside-range M5 swing; opposite range boundary if no usable swing exists |

### Entry and Stop Logic

- The high and low of the closed 08:15 New York M15 candle define the daily window.
- After 08:30, the first closed M5 candle that closes above the high triggers a buy; the first candle that closes below the low triggers a sell.
- For a buy, the stop uses the latest confirmed M5 swing low formed inside the range. For a sell, it uses the latest confirmed swing high formed inside the range.
- If there is no usable inside-range swing, the stop falls back to the opposite edge of the M15 range.
- The target is 1.5 times the entry-to-stop distance. If there is no valid risk distance, no order plan is produced.
- New York time uses the `America/New_York` timezone, including daylight-saving changes.

---


## Live Chart Behavior

The dashboard separates cheap live updates from heavier sync work:

- `/api/tick` is polled every 1 second.
- The latest forming candle is patched from live tick price.
- Recent MT5 candles sync every 2 seconds.
- Heavier AI/board context refreshes less often to reduce chart lag.

Chart markers:

- `B` means DBB buy action.
- `S` means DBB sell action.
- `X` means DBB exit action.
- Markers are shifted to the next candle and include a small horizontal tick at that candle's open price.

The marker shift is visual only. The trading logic is controlled separately by the auto-trade effect and backend webhook.

---

## Auto-Trade Timing

The dashboard One Time Window auto-trade path uses the closed M5 breakout candle:

1. The 08:15 New York M15 candle closes at 08:30 and its high/low become the daily window.
2. The dashboard waits for the first closed M5 candle outside that range.
3. It selects the conditional M5 stop, computes the 1.5R target, then sends `/api/dbb/webhook` with side, SL, and TP.
4. The backend validates the levels against the live MT5 quote and sends the order.

Current guard:

- Only closed M15 and M5 candles are used.
- Entry execution is allowed for 90 seconds after the breakout candle closes, covering dashboard polling latency without replaying older signals.
- Duplicate entries are blocked by breakout time and side.

Expected timing:

- Usually within the 1-second tick polling cadence after the new candle appears.
- Exact zero-delay execution is not possible because the browser polls tick data, posts the webhook, and MT5 then sends the order.

Important: the local DBB chart/webhook auto-trade path requires the dashboard tab to be open. The backend also has an autonomous worker path, but it is disabled by default unless configured through environment settings.

---

## Run

```powershell
python server.py
```

or:

```powershell
start_server.bat
```

Then open:

```text
http://localhost:8090
```

---

## Frontend

Source:

```text
neural_website_repo/
```

Useful commands:

```powershell
cd neural_website_repo
npm.cmd run lint
npm.cmd run build
```

The compiled `neural_website_repo/dist/` folder is served by `server.py` when present.

---

## API Endpoints

- `/api/timeframe` - candles for one timeframe
- `/api/sync` - recent candles for all configured timeframes
- `/api/tick` - latest MT5 tick
- `/api/board` - full board snapshot with structure/context
- `/api/ai/status` - current AI/autonomous status snapshot
- `/api/autotrade/status` - autotrade state
- `/api/autotrade/config` - enable/disable autotrade and lot size
- `/api/dbb/webhook` - dashboard buy/sell/close webhook; One Time Window entries include SL and TP
- `/api/history/dashboard` - closed-trade analytics

---

## Files

- `server.py` - local HTTP server, MT5 integration, API endpoints, order execution, logging
- `neural_website_repo/src/components/TradingChart.tsx` - live chart, Trade Matrix, setup plotting, dashboard webhook trigger
- `dbb_momentum_baseline.txt` - Pine Script DBB reference
- `dbb_backtest.py` - optional offline backtest helper
- `google_sheet_sync.py` - Google Sheets sync helper
- `ai_logic_audit.json` - audit trail
- `ai_trade_decisions.json` - decision records
- `ai_trade_reviews.json` - executed trade review records

---

## Google Sheets Sync

Manual usage:

```powershell
python google_sheet_sync.py
python google_sheet_sync.py --json
python google_sheet_sync.py --date 2026-03-31
```

Push usage:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON='C:\path\to\service-account.json'
$env:GOOGLE_SHEET_ID='your-google-sheet-id'
python google_sheet_sync.py --push
```

Auto-sync after closed trades is active when credentials are set and `server.py` is running.

---

## Requirements

- Python 3.10+
- MetaTrader 5 terminal open and logged in
- `MetaTrader5` Python package
- Node.js for frontend development/builds
- `yfinance` only if using `dbb_backtest.py`
