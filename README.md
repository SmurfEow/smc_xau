# Quantum

Quantum is a local MT5 auto-trading workspace for `XAUUSD` with a dashboard-driven One Time Window breakout strategy.

- Live MT5 candles and ticks
- Neural Alpha React dashboard for monitoring and control
- One Time Window range, breakout, conditional stop, and 1.5R target plotting
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
- The target is 1.5 times the entry-to-stop distance. If there is no valid risk distance, no trade plan is produced.
- New York time uses `America/New_York`, including daylight-saving changes.

---


## Live Chart Behavior

The dashboard separates cheap live updates from heavier sync work:

- `/api/tick` is polled every 1 second.
- The latest forming candle is patched from live tick price.
- M5 and M15 each load all candle history available from MT5 when the dashboard opens. The charts open near the newest bars and can be panned/zoomed back through the loaded history.
- M1 and H1 retain the smaller 500-bar context window.
- The `/api/tick` stream updates the newest forming M5/M15 candle once per second; historical candles stay fixed until a candle closes.
- Only the recent candle tail syncs every 15 seconds, so the dashboard does not redraw the full history on each tick.
- Heavier AI/board context refreshes less often to reduce chart lag.

Chart overlays show the One Time Window high/low and, after a breakout, the entry, conditional stop, 1.5R target, and direction marker.

---

## Auto-Trade Timing

The dashboard and server worker use the One Time Window setup:

1. The 08:15 New York M15 candle closes at 08:30 and defines the daily range.
2. The first closed M5 candle outside the range selects buy or sell.
3. The stop uses the latest confirmed inside-range swing, with the opposite range edge as fallback; target is 1.5R.
4. The dashboard sends the order plan to `/api/dbb/webhook`. The server worker evaluates the same setup independently.
5. The server validates SL/TP against the live MT5 quote before submitting an order.

Execution guard:

- Only closed candles are used.
- The signal may execute only within 90 seconds of the breakout candle close.
- The existing auto-trade switch, open-position lock, cooldown, session guard, and MT5 order handling remain active.

The dashboard tab can show and draw the setup; the backend worker uses the same rules when the server is running.

---



## Run

Before the first start, create the administrator account in the same PowerShell window. Use a unique, long password; it is stored only as a slow password hash in `quantum_auth.sqlite3`.

```powershell
$env:QUANTUM_INITIAL_ADMIN_EMAIL = "you@example.com"
$env:QUANTUM_INITIAL_ADMIN_PASSWORD = "replace-this-with-a-unique-long-password"
# Required when hosting behind HTTPS; leave false for localhost development.
$env:QUANTUM_COOKIE_SECURE = "true"
```

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

## Accounts and access

Every dashboard/API request now requires a signed-in account. The first account configured with `QUANTUM_INITIAL_ADMIN_EMAIL` is an administrator; administrators can issue client accounts through `POST /api/auth/users` with an email and a password of at least 12 characters. Client accounts can view the terminal, but only an administrator can call the trade-changing API endpoints.

Self-service registration is off by default. Set `QUANTUM_ALLOW_SIGNUP=true` only if you deliberately want new visitors to be able to register.

This release protects access to the existing, single local MT5 terminal. It deliberately does **not** pretend that one shared MT5 login is separate client brokerage accounts: isolated client portfolios require one MT5/broker connection and user-scoped trade/history storage per client, which should be the next deployment phase before enabling client trading.

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
- `/api/dbb/webhook` - dashboard One Time Window buy/sell/close webhook
- `/api/history/dashboard` - closed-trade analytics

---

## Files

- `server.py` - local HTTP server, MT5 integration, API endpoints, order execution, logging
- `neural_website_repo/src/components/TradingChart.tsx` - live chart, Trade Matrix, One Time Window plotting, dashboard webhook trigger
- `dbb_momentum_baseline.txt` - Pine Script One Time Window reference
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

Google Sheets sync is disabled in a fresh clone. Auto-sync after closed master trades activates only when both local variables above are set and `server.py` is running. Do not put a service-account JSON file, sheet ID, Telegram token, broker password, or MT5 profile into Git.

---

## Requirements

- Python 3.10+
- MetaTrader 5 terminal open and logged in
- `MetaTrader5` Python package
- Node.js for frontend development/builds
- `yfinance` only if using `dbb_backtest.py`
