# Quantum

Quantum is a local MT5 auto-trading workspace for `XAUUSD` powered by a Double Bollinger Band (DBB) Momentum strategy.

- Live MT5 candles and ticks
- Neural Alpha React dashboard for monitoring and control
- DBB signal plotting with next-candle entry/exit execution
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
| Band expansion threshold | 1.04 |
| Minimum hold | 3 M15 bars |
| Hard stop | 18 XAUUSD points |
| Basis trend lookback | 8 M15 bars |
| Minimum breakout distance | 8% of current 1SD band width beyond the trigger band |
| Default lot size | 0.10 |

### Entry Logic

- Long signal: confirmed M15 close crosses above the 1SD upper band, bands are expanding, the DBB basis is rising over the last 8 bars, and the close clears the band by at least 8% of current 1SD width.
- Short signal: confirmed M15 close crosses below the 1SD lower band, bands are expanding, the DBB basis is falling over the last 8 bars, and the close clears the band by at least 8% of current 1SD width.
- The signal is detected on the closed candle.
- The signal marker is plotted on the confirmed signal candle.
- Auto-trade execution happens on the next candle open window.

### Exit Logic

- Long exit: confirmed M15 close crosses back under the 1SD upper band after the minimum hold.
- Short exit: confirmed M15 close crosses back over the 1SD lower band after the minimum hold.
- Exit markers are plotted on the confirmed exit signal candle.
- Auto-close execution happens on the next candle open window.

### Expansion Filter

```text
expanding = (u1 - l1) > (u1_prev - l1_prev) * 1.04
```

The current defensive profile uses `1.04` live. It blocks low-momentum chop and only allows entries when the inner DBB width is expanding with enough force.

### Defensive Filters

```text
long_ok  = basis > basis[8] and close >= u1 + ((u1 - l1) * 0.08)
short_ok = basis < basis[8] and close <= l1 - ((u1 - l1) * 0.08)
```

These filters were added after the demo audit showed weak long crosses causing the worst drawdowns.

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
- Markers are plotted on the confirmed signal candle.
- Auto-trade uses the following candle as the action candle.

The chart marker is the source of truth for the dashboard trigger: a fresh `B`, `S`, or `X` on the just-closed candle can fire on the next candle.

---

## Auto-Trade Timing

The dashboard DBB auto-trade path is intentionally next-candle based:

1. M15 candle closes.
2. The dashboard evaluates DBB markers on the confirmed candle that just closed.
3. The next M15 candle appears.
4. If the signal is from the just-closed candle, the dashboard sends `/api/dbb/webhook`.
5. The backend sends the market order or close request to MT5 immediately.

Current guard:

- Signals are computed from `m15Candles.slice(0, -1)` so the forming candle is not used.
- Execution is allowed only during the first 20 seconds of the next M15 candle.
- Duplicate signals are blocked by signal time and type.

Expected timing:

- Usually within the 1-second tick polling cadence after the new candle appears.
- Exact zero-delay execution is not possible because the browser polls tick data, posts the webhook, and MT5 then sends the order.

Important: the local DBB chart/webhook auto-trade path requires the dashboard tab to be open. The backend also has an autonomous worker path, but it is disabled by default unless configured through environment settings.

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
