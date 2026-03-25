# Quantum Terminal

Quantum Terminal is a local MT5 trading workspace built around:

- a six-timeframe market board
- a live trade engine
- MT5 autotrade execution
- a separate MT5 profit dashboard

It is designed for intraday XAUUSD-style trading where:

- `HTF` gives the picture
- `MTF` gives the placement
- `LTF` gives the execution detail

## Pages

Run:

```powershell
python server.py
```

Open:

- main board: `http://127.0.0.1:8090`
- profit dashboard: `http://127.0.0.1:8090/dashboard.html`

## MT5 Requirements

Before using the app:

- open MetaTrader 5
- log into your broker account
- make sure the symbol is visible in Market Watch
- make sure account trading is allowed

## Main Board

The main board shows:

- `M1`
- `M5`
- `M15`
- `M30`
- `H1`
- `H4`

Core chart behavior:

- broker symbol auto-resolution such as `XAUUSD` -> `XAUUSD.m`
- live MT5 candle sync
- live tick updates on the latest candle
- drag-to-pan, wheel zoom, and hover OHLCV
- per-chart `Newest` reset
- collapsible chart cards

Each timeframe card displays:

- current price
- support and resistance
- regime
- trend
- range position

## Market State Logic

Each timeframe calculates:

- `Support`
- `Resistance`
- `Regime`
- `Trend`
- `Range Position`

The market-state layer uses fixed windows so it stays stable and does not change just because the chart is zoomed:

- `Regime`: last `200` bars
- `Trend`: last `50` bars
- `State / Range`: last `20` bars
- `Support / Resistance`: recent rolling structure window

Current regime set:

- `Uptrend`
- `Downtrend`
- `Range`
- `Compression`
- `Transition`

Range position:

- `Upper`
- `Middle`
- `Lower`

## Indicator Layer

The trade engine also calculates a live indicator stack:

- `EMA 9 / 20 / 50`
- `RSI 14`
- `ADX 14`
- `ATR 14`
- `VWAP`

These indicators are used as context and confirmation, not as a standalone strategy.

Their jobs:

- `EMA`: short trend alignment
- `RSI`: momentum bias
- `ADX`: trend strength
- `ATR`: volatility buffer
- `VWAP`: intraday positioning

## Strategy Structure

The system follows a multi-timeframe structure:

- `HTF` = `H4 + H1`
- `MTF` = `M30 + M15`
- `LTF` = `M5 + M1`

Meaning:

- `HTF` decides the picture
- `MTF` decides the placement
- `LTF` decides the execution detail

This means the engine does not require every timeframe to agree perfectly. It allows:

- trend-following trades
- countertrend trades if lower-timeframe momentum is strong enough

## Trade Engine

The trade engine has two live sides:

- `Long`
- `Short`

Each side is broken into:

- `Mandatory`
- `Context`
- `Confirmation`
- `Risk`

### Mandatory

These are the real must-pass checks for a side to become ready:

- `LTF detail`
- `Score threshold`
- `Score edge`
- `Quality gate`

Current score rules:

- score threshold: `>= 55`
- score edge: side must lead the opposite side by `>= 5`

### Context

These add directional and location quality:

- `HTF picture`
- `MTF placement`
- `M5 EMA alignment`

These help the score, but they are not all hard-required one by one.

### Confirmation

These improve setup quality:

- `H1 ADX`
- `M15 RSI`
- `M5 RSI`
- `M5 VWAP`

### Risk

This block only appears as a real executable plan after a live trigger exists.

It shows:

- `Entry`
- `SL`
- `TP`
- risk distance
- target distance

If there is no live trigger yet:

- `SL` stays blank
- `TP` stays blank

That is intentional, so the system does not invent a fake trade plan for a non-triggered setup.

## How A Trade Is Generated

The system does not trade just because context looks good.

A trade only becomes ready when:

1. the lower timeframe actually triggers
2. that side reaches the score threshold
3. that side beats the opposite side by the required score edge
4. the quality gate passes

This means:

- a setup can be favored but still remain `No Trade`
- a setup becomes `... Ready` only when the live execution conditions are present

Possible top actions:

- `Trend Buy Ready`
- `Countertrend Buy Ready`
- `Trend Sell Ready`
- `Countertrend Sell Ready`
- `No Trade`

## LTF Trigger Logic

The trade engine uses `M5` as the actual execution trigger timeframe.

Long trigger can come from things like:

- M5 close back above support
- M5 bullish candle above previous high
- M5 break above recent local high
- M5 bullish continuation above VWAP
- M5 bullish push with bullish EMA stack and RSI support

Short trigger can come from things like:

- M5 close back below resistance
- M5 bearish candle below previous low
- M5 break below recent local low
- M5 bearish continuation below VWAP
- M5 bearish push with bearish EMA stack and RSI support

`M1` is only a micro confirmation layer, not the main trigger timeframe.

## TP / SL Logic

The current risk model is based on:

- `SL`: M5 execution structure / liquidity
- `TP`: M15 target structure / liquidity

Current stop behavior:

- stop begins from nearby `M5` liquidity or trigger/swing structure
- ATR is used as a buffer
- stop distance is normalized so it does not become unrealistically tiny or absurdly wide

Current target behavior:

- target prefers `M15` liquidity / swing structure
- target currently uses a reduced target model so it is not as far as the full destination
- `M30` acts as fallback if `M15` has no usable target

Design reasoning:

- `M5` is the execution timeframe, so invalidation should come from execution structure
- `M15` is the setup timeframe, so the target should come from a more meaningful reaction area

## Autotrade Execution

Autotrade is controlled from the `Auto Trade Control` panel above the trade engine.

It shows:

- auto on/off
- lot size
- trade state
- active ticket
- active trade details:
  - symbol
  - side
  - volume
  - open price
  - `SL`
  - `TP`

Execution rules:

- autotrade only sends an order if `Entry`, `SL`, and `TP` are all present
- only one trade or pending order is allowed at a time
- duplicate rapid-fire sends are blocked

MT5-specific notes:

- execution uses broker-compatible filling modes
- the bridge was adjusted so symbols like `XAUUSD.m` work correctly

## Cooldown Logic

Cooldown does not begin when a trade opens.

Instead:

1. trade opens
2. system marks it as active
3. top label shows `In Trade`
4. when the live MT5 trade is no longer open, the trade is treated as closed
5. only then does the cooldown start

Current cooldown:

- `15 minutes`

Top cooldown states:

- `In Trade`
- countdown timer
- `Ready`

## Profit Dashboard

The profit dashboard is a separate page focused on closed MT5 history.

It reads closed trade history from MT5 and supports:

- `Daily`
- `Weekly`
- `Monthly`
- `Yearly`
- `Entire History`
- `Custom Date`

Time handling:

- dashboard times are rendered as `local time - 8 hours`

The dashboard includes:

- summary cards
- realized net values
- trade counts
- latest result
- cumulative net visualization
- performance breakdowns
- scrollable closed-deal history

## Dashboard Visuals

The dashboard is designed to use more visualization and less text.

Current visual sections include:

- cumulative net curve
- outcome/performance snapshots
- performance lenses
- close-time rhythm
- scrollable MT5 history table

Performance breakdowns are intended to help answer:

- which side performs better
- which exit behavior performs better
- which source performs better
- what time windows may need filtering

## History Data

The dashboard reads closed MT5 history and groups raw deals into trade records.

That means:

- raw MT5 deals can be more numerous
- grouped dashboard trades will usually be fewer

The history table shows:

- open time
- symbol
- ticket
- type
- volume
- open price
- `SL`
- `TP`
- close time
- close price
- realized profit
- price change

## Notes

- chart display time is shifted for broker alignment
- the main board and dashboard both depend on MT5 being open
- no live trigger means no executable TP/SL plan
- the autotrader is designed to be strict enough to avoid noise, but not so strict that it never trades
