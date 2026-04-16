# Quantum

Quantum is a local MT5 trading workspace for `XAUUSD` with:

- a live multi-timeframe board in the workspace
- a server-side autonomous decision loop that evaluates every 5 minutes
- auto-trade execution with safety checks
- a manual broker-time news block calendar for CPI, FOMC, NFP, and custom event pauses
- a dashboard for setup, session, expectancy, and missed-trade analytics

The website is the display and control layer. The server is the engine.

## Current Architecture

### Core flow

1. `server.py` starts the local HTTP server on `127.0.0.1:8090`
2. the workspace page renders the live board from MT5 data
3. the server builds its own board snapshot object every 5 minutes
4. the local setup engine evaluates the market
5. if a setup is valid, the server runs auto-trade validation and can place the order through MT5
6. decisions, reviews, and audit events are logged for later analysis

### Live pages

- `index.html`
  Live workspace, auto-trade control, and manual broker-time news block calendar
- `dashboard.html`
  Historical and analytical dashboard

### Important note

The current live decision engine is local and rule-based.

- default engine: `local`
- current strategy model label: `local-setup-engine`

Older Ollama/vision experiments are not the live trading path now.

## Files

### Backend

- `server.py`
  Main server, MT5 integration, local setup engine, autonomous loop, logging, and auto-trade execution

### Frontend

- `index.html`
- `app.js`
- `dashboard.html`
- `dashboard.js`
- `styles.css`

### Logs

- `ai_trade_decisions.json`
  Every decision cycle
- `ai_trade_reviews.json`
  Executed trade review records
- `ai_logic_audit.json`
  Pipeline and validation audit trail

### Snapshot

- `snapshots/latest-board.png`
  Latest workspace-rendered board image for manual review or sharing

### Local runtime data

- `manual_news_calendar.json`
  Saved manual news block events used by the auto-trade guard

## Trading Method

The system uses a top-down Smart Money Concepts (SMC) framework. Every trade decision flows through three timeframe layers in order. No layer can be skipped.

### Timeframe roles

| Timeframe | Role |
|-----------|------|
| `H1` | Sets the directional bias. Must show `Uptrend` or `Downtrend` regime. |
| `M15` | Defines the watch zone. The OTE Fibonacci range is drawn from the M15 impulse leg. |
| `M5 / M1` | Provides the entry trigger. A confirmed candle inside the zone on M5 or M1 is required before any order is placed. |

### How a trade forms — step by step

**Step 1 — H1 sets the direction**

The engine reads the H1 `marketState`. If both H1 and M15 agree on direction (`Uptrend` = bullish, `Downtrend` = bearish), the overall bias is set. If they disagree, bias is `mixed` and no zone is surfaced — the engine waits.

**Step 2 — M15 draws the OTE zone**

Once bias is confirmed, the engine measures the most recent M15 impulse leg (swing low to swing high for a bullish leg, or swing high to swing low for a bearish leg) and applies Fibonacci retracement levels to that range:

```
structureLow  = 0%   (base of the impulse)
fib382        = 38.2%
equilibrium   = 50%
fib618        = 61.8%
fib705        = 70.5%
fib786        = 78.6%
structureHigh = 100%  (top of the impulse)
```

The **OTE (Optimal Trade Entry) zone** is the 61.8%–78.6% retracement of the impulse:

- Bullish leg: OTE sits near the low end of the range (price pulled back deep, 21.4%–38.2% from the structureLow). This is where a buy is considered.
- Bearish leg: OTE sits near the high end of the range (price rallied deep, 61.8%–78.6% from the structureLow). This is where a sell is considered.

The watch zone displayed in the SMC panel is always the current OTE range in price terms, e.g. `4801.87–4809.66`.

**Step 3 — M15 OB or FVG confirms the location inside the zone**

Price reaching the OTE zone is not enough on its own. The engine looks for a structural reason to enter inside that zone:

- **Order Block (OB)**: the last bearish candle before a bullish impulse (for buys), or the last bullish candle before a bearish impulse (for sells). The OB body is the precise entry area.
- **Fair Value Gap (FVG)**: a three-candle imbalance where the first candle's high and the third candle's low do not overlap (for buys), or vice versa (for sells). Price retesting this gap is the entry signal.

M15 OBs and FVGs are searched first. M5 OBs and FVGs are used if no M15 zone is found.

**Step 4 — M5 or M1 prints the confirmation candle**

Even with a valid OTE zone and an OB or FVG, the engine waits for a lower timeframe reaction:

- For buys: a bullish M5 or M1 close above the OB/FVG after touching it
- For sells: a bearish M5 or M1 close below the OB/FVG after touching it

This confirmation candle is the actual trigger. Without it, the engine outputs `no_trade` and waits.

**Step 5 — Additional trigger types**

Beyond OB and FVG retests, the engine also watches for:

- **Sell-side liquidity sweep and reclaim**: price sweeps a prior swing low (triggering stops), then closes back above it. Treated as a buy signal.
- **Buy-side liquidity sweep and reject**: price sweeps a prior swing high, then closes back below it. Treated as a sell signal.
- **Failed breakdown / failed breakout**: price breaks a level but cannot sustain it and reverses back inside. Confirms the original direction.
- **BOS retest**: after a Break of Structure, price retests the broken level from the new side.
- **Displacement**: a strong impulsive candle continuing through a broken level without retracement.

**Step 6 — Stop loss placement**

The stop loss is anchored to the OTE zone edge:

- Buy trades: SL is placed just below `oteLow` (the deeper OTE boundary, near the 78.6% retracement)
- Sell trades: SL is placed just above `oteHigh` (the shallower OTE boundary, near the 61.8% retracement)

The SL distance is clamped between 4.5 and 9.0 points for SMC setups.

**Step 7 — Take profit targeting**

The TP is the nearest Fibonacci level on the correct side of the entry:

- Buy trades: the next fib level above entry, chosen from `[fib382, equilibrium, fib618, fib705, fib786, structureHigh]`
- Sell trades: the next fib level below entry, chosen from `[fib786, fib705, fib618, equilibrium, fib382, structureLow]`

The TP uses the actual fib price as-is, without imposing a minimum RR floor. If the nearest fib is only a few points away, that is the target — the setup geometry determines the reward, not an artificial multiplier.

### Conditions that block a trade

Even if all five steps above are satisfied, the order will not be placed if:

- Auto-trade is disabled in the workspace panel
- A manual news block is active (45 min before and after any saved calendar event)
- A trade is already open
- The duplicate signal guard detects the same signal within the cooldown window
- The entry, stop, or target fails the final sanity check

### Example trade scenario

> H1 is in an uptrend. M15 shows a bullish impulse leg from 4750 to 4838. The OTE buy zone is 4800–4810 (61.8%–78.6% retracement from the high). A bullish OB sits at 4803–4807 inside that zone. Price pulls back into the zone and touches the OB. M5 prints a bullish engulfing candle closing above 4807. The engine fires a buy at the M5 close price, sets SL below the OTE low (~4800), and targets equilibrium (4794 + 44 × 0.5 = 4816) as TP1.

### What "No clear zone" means

The SMC panel shows `No clear zone` when:

- H1 and M15 do not both agree on a direction (bias is `mixed`)
- M15 structure direction is neutral (regime is `Transition` or `Range`)

This most commonly occurs after a large price move that changes the M15 structural regime before a new impulse leg has formed. The correct response is to wait for the new structure to establish and for H1 and M15 to re-align.

## Autonomous Behavior

As long as `server.py` is running, the system keeps working even if you are only on the dashboard.

### Autonomous loop

- interval: every 5 minutes
- symbol default: `XAUUSD`
- source of truth: MT5 data through the backend

### What still depends on the workspace page

- `snapshots/latest-board.png` is generated by the workspace render
- the live workspace charts are browser-rendered

The trading engine itself does not need the workspace tab open to keep evaluating and trading.

## Auto-Trade

The workspace includes an auto-trade control panel.

### Manual news block

The workspace includes a manual broker-time calendar above the Decision Layer.

- events are entered in MT5 broker time
- each saved event hard-blocks auto-trade for `45` minutes before and `45` minutes after
- applied days are highlighted in the calendar
- past broker-time dates cannot be added as new events
- saved events can be edited or removed from the selected day panel

### Auto-trade still validates

Even when the engine returns `buy` or `sell`, the server still checks:

- valid side
- valid entry / stop / target structure
- duplicate signal protection
- cooldown
- whether a trade is already active
- whether auto-trade is enabled
- whether a manual news block is active

## Logging And Analytics

The system logs enough detail to trace and tune the strategy later.

### `ai_trade_decisions.json`

Each decision can include:

- decision
- setup type
- phase
- bias
- location
- trigger state
- zone
- entry / stop / target
- RR
- `pattern_candidates`
- `entry_checks`
- reasoning text

### `ai_trade_reviews.json`

Executed trade reviews include:

- ticket
- setup type
- entry / stop / target
- RR
- execution summary
- result summary
- model

### `ai_logic_audit.json`

Audit events include:

- request
- decision
- auto-trade validation
- dispatch outcome

## Dashboard

The dashboard is focused on the current live strategy model only.

### Current history scope

- closed-trade history is filtered to the live bot magic number
- `All-Time Net` currently starts from `2026-04-01`

### Current useful panels

- `Daily Net`
- `Daily Trades`
- `All-Time Net`
- `Missed Trades`
- `Entry Readiness`
- `Entry Session Performance`
- `Cumulative Net Curve`
- `Setup Performance`
- `Setup x Session Heatmap`
- `Entry Quality & Families`
- `Closed MT5 Deals`

### What the dashboard highlights

- setup win rate
- setup expectancy
- session win rate
- session expectancy
- missed trades
- zone fit rate
- confirmation rate
- ready rate
- in-zone vs confirmation misses
- setup family behavior
- closed-deal performance

### Current session model

The dashboard uses 3 main sessions:

- `Asia`
- `London`
- `New York`

## Google Sheets Sync

Quantum can publish closed-trade results into Google Sheets directly from Python.

### Files

- `google_sheet_sync.py`
  Reads local MT5 history and writes the yearly calendar view
- `google_sheets_webhook.gs`
  Older optional Apps Script receiver kept in the repo as a fallback path

### Current sheet layout

The Google Sheet is now calendar-first.

- one yearly tab per year, for example:
  - `2026`
  - `2027`
- each yearly tab contains all 12 months stacked vertically
- each day cell shows:
  - day number
  - `P/L`
  - `T`
  - `W/L`

Calendar colors:

- green = positive day
- red = negative day
- gray = no-trade day

There is also an optional raw tab name, `profit_calendar`, used only as a legacy/utility label if an older summary tab already exists.

### Local usage

Print aggregated trade rows locally:

```powershell
python google_sheet_sync.py
```

Print JSON:

```powershell
python google_sheet_sync.py --json
```

Restrict to one broker date:

```powershell
python google_sheet_sync.py --date 2026-03-31
```

### Push directly to Google Sheets

Set:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON='C:\path\to\service-account.json'
$env:GOOGLE_SHEET_ID='your-google-sheet-id'
```

Optional legacy tab env:

```powershell
$env:GOOGLE_SHEET_TAB='profit_calendar'
```

Your Google Sheet must be shared with the service account email as `Editor`.

Then push:

```powershell
python google_sheet_sync.py --push
```

### Automatic sync on trade close

If direct Google Sheets credentials are available, `server.py` can auto-update the calendar when a new `Quantum Auto` trade closes.

Example:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON='C:\path\to\service-account.json'
$env:GOOGLE_SHEET_ID='1XDNo6mnh7IAxE7mLpuGpF8jAr-sZ43gL0jiB2c6meIo'
python server.py
```

When a new auto trade closes, the server will:

- detect the open -> closed lifecycle transition
- collect the latest local MT5 closed history
- refresh the correct yearly calendar tab automatically

### New year behavior

When trades appear in a new year, Quantum automatically creates a new yearly tab.

Example:

- current year trades update `2026`
- first `2027` trade automatically creates and updates `2027`

## API Endpoints

Useful local endpoints include:

- `/api/sync`
  Board/timeframe data
- `/api/tick`
  Latest tick
- `/api/ai/status`
  Latest local strategy output and autonomous status
- `/api/ai/trade`
  Manual on-demand setup evaluation
- `/api/ai/snapshot`
  Saves the current workspace board image
- `/api/autotrade/status`
- `/api/autotrade/config`
- `/api/autotrade/evaluate`
- `/api/history/dashboard`
  Dashboard analytics and MT5 closed history

## Run

### Start the server

```powershell
python server.py
```

or

```powershell
start_server.bat
```

Then open:

- workspace: `http://127.0.0.1:8090/index.html`
- dashboard: `http://127.0.0.1:8090/dashboard.html`

## Requirements

- Python
- MetaTrader 5 terminal
- `MetaTrader5` Python package
- MT5 account/session available locally

## Current State Of The Project

Quantum is now in a refinement phase, not an expansion phase.

The current priority is:

- better zone quality
- better confirmation quality
- better stop placement
- stronger analytics for expectancy and missed trades

The current setup list is intentionally limited to the main generic structure families to avoid overfitting.
