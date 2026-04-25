"""
DBB Backtest — uses yfinance historical data (no MT5 needed)
Matches server.py logic: SMA20, 1SD/2SD bands, expansion 1.02, min hold 5 bars
"""

import sys
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("Run: pip install yfinance")
    sys.exit(1)

# --- Strategy config (must match dbb_momentum_baseline.txt / server.py) ---
SYMBOL          = "GC=F"      # Gold futures (XAUUSD equivalent on yfinance)
INTERVAL        = "15m"       # M15
PERIOD          = "60d"       # yfinance free tier: max 60 days for 15m data
                              # For longer history change INTERVAL to "1h" and PERIOD to "2y"
LOT             = 0.1
SPREAD          = 0.30        # dollars per trade
LENGTH          = 20
MULT1           = 1.0
MULT2           = 2.0
MIN_HOLD_BARS   = 5
EXPAND_THRESH   = 1.02
LAST_ENTRY_HOUR = 22
INITIAL_CAPITAL = 10_000

# XAUUSD: 0.1 lot = 10 oz → $1 move = $10 P&L
POINT_VALUE = LOT * 100


def fetch_data() -> pd.DataFrame:
    print(f"Fetching {INTERVAL} data for {SYMBOL} ({PERIOD})...")
    df = yf.download(SYMBOL, interval=INTERVAL, period=PERIOD, auto_adjust=True, progress=False)
    if df.empty:
        print("No data returned. Check symbol or interval.")
        sys.exit(1)
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    print(f"Loaded {len(df):,} candles  |  {df.index[0].date()} → {df.index[-1].date()}")
    return df


def compute_dbb(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["basis"] = df["close"].rolling(LENGTH).mean()
    df["std"]   = df["close"].rolling(LENGTH).std(ddof=0)  # population std like Pine Script
    df["u1"]    = df["basis"] + MULT1 * df["std"]
    df["u2"]    = df["basis"] + MULT2 * df["std"]
    df["l1"]    = df["basis"] - MULT1 * df["std"]
    df["l2"]    = df["basis"] - MULT2 * df["std"]
    df["width"] = df["u1"] - df["l1"]
    df["expanding"] = df["width"] > df["width"].shift(1) * EXPAND_THRESH
    return df


def run_backtest(df: pd.DataFrame) -> list[dict]:
    trades     = []
    position   = None
    bars_held  = 0

    for i in range(LENGTH + 1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]

        broker_hour       = row.name.hour
        entry_window_open = broker_hour < LAST_ENTRY_HOUR

        if position is not None:
            bars_held += 1

        # --- Exit logic ---
        if position is not None and bars_held >= MIN_HOLD_BARS:
            if position["side"] == "long":
                if prev["close"] >= prev["u1"] and row["close"] < row["u1"]:
                    exit_price = row["close"] - SPREAD / 2
                    pnl = (exit_price - position["entry"]) * POINT_VALUE
                    trades.append(_make_trade(position, row.name, exit_price, bars_held, pnl))
                    position = None
                    bars_held = 0

            elif position["side"] == "short":
                if prev["close"] <= prev["l1"] and row["close"] > row["l1"]:
                    exit_price = row["close"] + SPREAD / 2
                    pnl = (position["entry"] - exit_price) * POINT_VALUE
                    trades.append(_make_trade(position, row.name, exit_price, bars_held, pnl))
                    position = None
                    bars_held = 0

        # --- Entry logic ---
        if position is None and entry_window_open and bool(row["expanding"]):
            if prev["close"] <= prev["u1"] and row["close"] > row["u1"]:
                position = {"side": "long",  "entry": row["close"] + SPREAD / 2, "entry_time": row.name}
                bars_held = 0

            elif prev["close"] >= prev["l1"] and row["close"] < row["l1"]:
                position = {"side": "short", "entry": row["close"] - SPREAD / 2, "entry_time": row.name}
                bars_held = 0

    # Close any open position at last bar
    if position is not None:
        last       = df.iloc[-1]
        exit_price = last["close"]
        pnl = (exit_price - position["entry"]) * POINT_VALUE if position["side"] == "long" \
              else (position["entry"] - exit_price) * POINT_VALUE
        trades.append(_make_trade(position, last.name, exit_price, bars_held, pnl, open_trade=True))

    return trades


def _make_trade(pos, exit_time, exit_price, bars, pnl, open_trade=False):
    return {
        "side":       pos["side"],
        "entry_time": pos["entry_time"],
        "exit_time":  exit_time,
        "entry":      pos["entry"],
        "exit":       exit_price,
        "bars":       bars,
        "pnl":        pnl,
        "open":       open_trade,
    }


def print_results(trades: list[dict]):
    if not trades:
        print("No trades found.")
        return

    df     = pd.DataFrame(trades)
    closed = df[~df["open"]]

    if closed.empty:
        print("No closed trades.")
        return

    total_pnl    = closed["pnl"].sum()
    wins         = closed[closed["pnl"] > 0]
    losses       = closed[closed["pnl"] <= 0]
    gross_profit = wins["pnl"].sum()        if len(wins)   > 0 else 0.0
    gross_loss   = abs(losses["pnl"].sum()) if len(losses) > 0 else 0.0
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate     = len(wins) / len(closed) * 100

    cum    = closed["pnl"].cumsum()
    max_dd = (cum.cummax() - cum).max()

    longs  = closed[closed["side"] == "long"]
    shorts = closed[closed["side"] == "short"]
    long_pf  = longs["pnl"].sum()  / abs(longs[longs["pnl"]   <= 0]["pnl"].sum())  if len(longs[longs["pnl"]   <= 0]) > 0 else float("inf")
    short_pf = shorts["pnl"].sum() / abs(shorts[shorts["pnl"]  <= 0]["pnl"].sum()) if len(shorts[shorts["pnl"] <= 0]) > 0 else float("inf")

    print(f"\n{'='*54}")
    print(f"  DBB M15 Backtest  |  Gold  |  Lot {LOT}  |  Spread ${SPREAD}")
    print(f"{'='*54}")
    print(f"  Period:           {closed['entry_time'].min().date()} → {closed['exit_time'].max().date()}")
    print(f"{'─'*54}")
    print(f"  Total trades:     {len(closed)}")
    print(f"  Win rate:         {win_rate:.2f}%  ({len(wins)}/{len(closed)})")
    print(f"  Total P&L:        ${total_pnl:>10,.2f}")
    print(f"  Gross profit:     ${gross_profit:>10,.2f}")
    print(f"  Gross loss:       ${gross_loss:>10,.2f}")
    print(f"  Profit factor:    {pf:.3f}")
    print(f"  Max drawdown:     ${max_dd:>10,.2f}")
    print(f"{'─'*54}")
    print(f"  Avg win:          ${wins['pnl'].mean():>10,.2f}"   if len(wins)   > 0 else "  Avg win:          N/A")
    print(f"  Avg loss:         ${losses['pnl'].mean():>10,.2f}" if len(losses) > 0 else "  Avg loss:         N/A")
    print(f"  Avg bars held:    {closed['bars'].mean():.1f}")
    print(f"{'─'*54}")
    print(f"  Long  trades:     {len(longs)}  |  PF: {long_pf:.3f}")
    print(f"  Short trades:     {len(shorts)}  |  PF: {short_pf:.3f}")
    print(f"{'─'*54}")
    if df["open"].any():
        op = df[df["open"]].iloc[0]
        print(f"  Open position:    {op['side']}  entry {op['entry']:.2f}  unrealised ${op['pnl']:,.2f}")
        print(f"{'─'*54}")
    print()
    print("  NOTE: yfinance free tier limits 15m data to last 60 days.")
    print("  For longer history, set INTERVAL='1h' and PERIOD='2y' at the top.")
    print()


if __name__ == "__main__":
    df     = fetch_data()
    df     = compute_dbb(df)
    trades = run_backtest(df)
    print_results(trades)
