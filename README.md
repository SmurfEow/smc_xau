# Quantum

Quantum is a local MT5 auto-trading workspace for `XAUUSD` powered by a Double Bollinger Band (DBB) Momentum strategy.

- Live MT5 candles and ticks
- Neural Alpha React dashboard for monitoring and control
- DBB signal plotting with realistic next-candle entry/exit markers
- Optional dashboard-driven auto-trade execution through MT5
- Trade/audit logs and Google Sheets sync support

The Python server is the MT5/API engine. The Neural Alpha dashboard is the live chart, control, and local DBB signal layer.

---

## Current Strategy: Double Bollinger Band Momentum

### Parameters

| Parameter | Value |
|---|---|
| Symbol | `XAUUSD` or broker equivalent such as `XAUUSD.m` |
| Strategy timeframe | M15 |
| BB length | 20 |
| Inner band | `basis +/- 1 * stdev` |
| Outer band | `basis +/- 2 * stdev` |
| Band expansion threshold | 1.02 |
| Minimum hold | 5 M15 bars |
| Default lot size | 0.10 |

### Entry Logic

- Long signal: confirmed M15 close crosses above the 1SD upper band and bands are expanding.
- Short signal: confirmed M15 close crosses below the 1SD lower band and bands are expanding.
- The signal is detected on the closed candle.
- The actual plotted action point is the next candle open.

### Exit Logic

- Long exit: confirmed M15 close crosses back under the 1SD upper band after the minimum hold.
- Short exit: confirmed M15 close crosses back over the 1SD lower band after the minimum hold.
- Exit markers are also plotted on the next candle open because that is the realistic close action point.

### Expansion Filter

```text
expanding = (u1 - l1) > (u1_prev - l1_prev) * 1.02
```

This blocks low-momentum chop and only allows entries when the inner DBB width is expanding.

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

The dashboard DBB auto-trade path is intentionally next-candle based:

1. M15 candle closes.
2. The dashboard evaluates DBB signals using confirmed candles only.
3. The next M15 candle appears.
4. If the signal is from the just-closed candle, the dashboard sends `/api/dbb/webhook`.
5. The backend sends the market order or close request to MT5 immediately.

Current guard:

- Signals are computed from `m15Candles.slice(0, -1)` so the forming candle is not used.
- Execution is allowed only during the first 5 seconds of the next M15 candle.
- Duplicate signals are blocked by signal time and type.

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
- `/api/dbb/webhook` - dashboard DBB buy/sell/close webhook
- `/api/history/dashboard` - closed-trade analytics

---

## Files

- `server.py` - local HTTP server, MT5 integration, API endpoints, order execution, logging
- `neural_website_repo/src/components/TradingChart.tsx` - live chart, Trade Matrix, DBB plotting, dashboard webhook trigger
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
