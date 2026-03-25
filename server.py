from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5


HOST = "127.0.0.1"
PORT = 8090
ROOT = Path(__file__).resolve().parent
LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc
DASHBOARD_TIME_OFFSET = timedelta(hours=-8)
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}
FULL_HISTORY_CHUNK = 1000
RECENT_SYNC_BARS = 8
AUTOTRADE_MAGIC = 20260324
AUTOTRADE_COMMENT = "Quantum Auto"
AUTOTRADE_STATE = {
    "enabled": False,
    "lot": 0.01,
    "last_signal_id": "",
    "last_attempt_at": 0.0,
    "last_trade_at": 0.0,
    "trade_active": False,
    "trade_seen_open": False,
    "active_trade": None,
}
AUTOTRADE_LOCK = threading.Lock()
MT5_LOCK = threading.Lock()
AUTOTRADE_COOLDOWN_SECONDS = 15 * 60
DEAL_ENTRY_OUT = getattr(mt5, "DEAL_ENTRY_OUT", 1)
DEAL_ENTRY_OUT_BY = getattr(mt5, "DEAL_ENTRY_OUT_BY", 3)
DEAL_ENTRY_INOUT = getattr(mt5, "DEAL_ENTRY_INOUT", 2)
DEAL_TYPE_BUY = getattr(mt5, "DEAL_TYPE_BUY", 0)
DEAL_TYPE_SELL = getattr(mt5, "DEAL_TYPE_SELL", 1)


def get_dashboard_now() -> datetime:
    return datetime.now(LOCAL_TZ) + DASHBOARD_TIME_OFFSET


def to_dashboard_time(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(int(unix_seconds or 0), LOCAL_TZ) + DASHBOARD_TIME_OFFSET


def parse_date_input(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)


def resolve_history_window(period: str, date_from: str | None, date_to: str | None) -> tuple[str, datetime | None, datetime | None]:
    normalized = str(period or "daily").strip().lower()
    now = get_dashboard_now()

    if normalized == "custom":
        start = parse_date_input(date_from)
        end = parse_date_input(date_to)
        if start is None or end is None:
            raise ValueError("Custom history requires both date_from and date_to in YYYY-MM-DD format.")
        if end < start:
            raise ValueError("date_to must be on or after date_from.")
        return "custom", start.replace(hour=0, minute=0, second=0, microsecond=0), end.replace(hour=23, minute=59, second=59, microsecond=999999)

    if normalized == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return normalized, start, end
    if normalized == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        return normalized, start, end
    if normalized == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return normalized, start, end
    if normalized == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        return normalized, start, end
    if normalized == "all":
        return normalized, None, None
    raise ValueError("Unsupported history period.")


def classify_trade_type(comment: str, symbol: str) -> str:
    text = str(comment or "").strip()
    if not text:
        return "Manual / Unlabeled"
    upper = text.upper()
    if upper.startswith("QBOT:"):
        return text
    if "QUANTUM AUTO" in upper:
        return "Quantum Auto"
    if "SL " in upper or "TP " in upper:
        return "Stop / Target Exit"
    return text


def classify_trade_source(comment: str) -> str:
    text = str(comment or "").strip()
    upper = text.upper()
    if upper.startswith("QBOT:"):
        return text
    if "QUANTUM AUTO" in upper:
        return "Quantum Auto"
    if "[TRADING IOS]" in upper:
        return "Trading iOS"
    if not text:
        return "Manual / Unlabeled"
    return "Manual / Other"


def classify_exit_reason(comment: str) -> str:
    text = str(comment or "").strip()
    upper = text.upper()
    if upper.startswith("[SL"):
        return "Stop Loss"
    if upper.startswith("[TP"):
        return "Take Profit"
    if "STOP OUT" in upper:
        return "Stop Out"
    if not text:
        return "Manual / Other Exit"
    return "Manual / Other Exit"


def build_error(status: int, message: str) -> bytes:
    return json.dumps({"detail": message}).encode("utf-8")


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    return value or "XAUUSD"


def resolve_symbol(symbol: str) -> str:
    requested = normalize_symbol(symbol)
    exact = mt5.symbol_info(requested)
    if exact:
        mt5.symbol_select(requested, True)
        return requested

    all_symbols = mt5.symbols_get()
    if not all_symbols:
        return requested

    requested_compact = requested.replace(".", "").replace("_", "")
    for item in all_symbols:
        name = str(item.name or "")
        compact = name.upper().replace(".", "").replace("_", "")
        if compact == requested_compact:
            mt5.symbol_select(name, True)
            return name

    for item in all_symbols:
        name = str(item.name or "")
        if name.upper().startswith(requested):
            mt5.symbol_select(name, True)
            return name

    return requested


def _copy_rates(symbol: str, timeframe: str, start_pos: int, count: int):
    return mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], start_pos, count)


def _normalize_rates(rates) -> list[dict[str, float | int]]:
    return [
        {
            "time": int(rate["time"]),
            "open": float(rate["open"]),
            "high": float(rate["high"]),
            "low": float(rate["low"]),
            "close": float(rate["close"]),
            "tick_volume": int(rate["tick_volume"]),
        }
        for rate in rates
    ]


def fetch_candles(symbol: str, timeframe: str, limit: int | None) -> tuple[str, list[dict[str, float | int]]]:
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe '{timeframe}'.")

    with MT5_LOCK:
        if not mt5.initialize():
            raise RuntimeError(
                "Could not connect to MetaTrader 5. Make sure MT5 is open and logged in."
            )

        try:
            resolved_symbol = resolve_symbol(symbol)
            if limit is None:
                collected = []
                start_pos = 0
                while True:
                    rates = _copy_rates(resolved_symbol, timeframe, start_pos, FULL_HISTORY_CHUNK)
                    if rates is None:
                        code, description = mt5.last_error()
                        if collected:
                            break
                        raise RuntimeError(
                            f"Could not load {timeframe} candles for {resolved_symbol}. "
                            f"Make sure the symbol exists and is visible in MT5 Market Watch. "
                            f"MT5 error {code}: {description}"
                        )
                    if len(rates) == 0:
                        break
                    normalized = _normalize_rates(rates)
                    collected.extend(normalized)
                    if len(rates) < FULL_HISTORY_CHUNK:
                        break
                    start_pos += FULL_HISTORY_CHUNK

                seen: set[int] = set()
                candles_reversed = []
                for candle in reversed(collected):
                    candle_time = int(candle["time"])
                    if candle_time in seen:
                        continue
                    seen.add(candle_time)
                    candles_reversed.append(candle)
                candles = list(reversed(candles_reversed))
            else:
                rates = _copy_rates(resolved_symbol, timeframe, 0, limit)
                if rates is None:
                    code, description = mt5.last_error()
                    raise RuntimeError(
                        f"Could not load {timeframe} candles for {resolved_symbol}. "
                        f"Make sure the symbol exists and is visible in MT5 Market Watch. "
                        f"MT5 error {code}: {description}"
                    )
                candles = _normalize_rates(rates)
            if not candles:
                raise RuntimeError(f"MT5 returned no candles for {resolved_symbol} on {timeframe}.")
            return resolved_symbol, candles
        finally:
            mt5.shutdown()


def trend_summary(candles: list[dict[str, float | int]]) -> dict[str, str | float]:
    closes = [float(candle["close"]) for candle in candles]
    lookback = min(12, len(closes))
    anchor_lookback = min(24, len(closes))
    recent = closes[-lookback:]
    anchor = closes[-anchor_lookback:-lookback] or closes[-lookback:]
    recent_avg = sum(recent) / len(recent)
    anchor_avg = sum(anchor) / len(anchor)
    latest = closes[-1]
    first = closes[0]
    low = min(closes)
    high = max(closes)

    if recent_avg > anchor_avg:
        tone = "Bullish"
    elif recent_avg < anchor_avg:
        tone = "Bearish"
    else:
        tone = "Neutral"

    return {
        "tone": tone,
        "last_close": latest,
        "range_low": low,
        "range_high": high,
        "change": latest - first,
    }


def fetch_tick(symbol: str) -> dict[str, float | int | None]:
    with MT5_LOCK:
        if not mt5.initialize():
            raise RuntimeError(
                "Could not connect to MetaTrader 5. Make sure MT5 is open and logged in."
            )

        try:
            resolved_symbol = resolve_symbol(symbol)
            tick = mt5.symbol_info_tick(resolved_symbol)
            if tick is None:
                code, description = mt5.last_error()
                raise RuntimeError(
                    f"Could not load live tick for {resolved_symbol}. MT5 error {code}: {description}"
                )

            return {
                "symbol": resolved_symbol,
                "time": int(getattr(tick, "time", 0) or 0),
                "bid": float(getattr(tick, "bid", 0.0) or 0.0),
                "ask": float(getattr(tick, "ask", 0.0) or 0.0),
                "last": float(getattr(tick, "last", 0.0) or 0.0),
            }
        finally:
            mt5.shutdown()


def normalize_volume(symbol_info, requested_lot: float) -> float:
    volume_min = float(getattr(symbol_info, "volume_min", 0.01) or 0.01)
    volume_max = float(getattr(symbol_info, "volume_max", requested_lot) or requested_lot)
    volume_step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
    clamped = max(volume_min, min(volume_max, float(requested_lot)))
    steps = round((clamped - volume_min) / volume_step)
    normalized = volume_min + steps * volume_step
    return round(normalized, 2)


def has_open_trade(symbol: str) -> tuple[bool, str]:
    positions = mt5.positions_get() or []
    if positions:
        return True, "An open position already exists, so only one trade is allowed at a time."
    orders = mt5.orders_get() or []
    if orders:
        return True, "A pending order already exists, so only one trade is allowed at a time."
    return False, ""


def place_market_order(symbol: str, side: str, lot: float, sl: float | None, tp: float | None) -> dict[str, object]:
    with MT5_LOCK:
        if not mt5.initialize():
            raise RuntimeError("Could not connect to MetaTrader 5. Make sure MT5 is open and logged in.")

        try:
            resolved_symbol = resolve_symbol(symbol)
            symbol_info = mt5.symbol_info(resolved_symbol)
            if symbol_info is None:
                raise RuntimeError(f"Could not resolve MT5 symbol for {symbol}.")
            if not symbol_info.visible:
                mt5.symbol_select(resolved_symbol, True)

            open_trade, message = has_open_trade(resolved_symbol)
            if open_trade:
                return {"status": "blocked", "detail": message, "symbol": resolved_symbol}

            tick = mt5.symbol_info_tick(resolved_symbol)
            if tick is None:
                code, description = mt5.last_error()
                raise RuntimeError(f"Could not load live tick for {resolved_symbol}. MT5 error {code}: {description}")

            digits = int(getattr(symbol_info, "digits", 2) or 2)
            volume = normalize_volume(symbol_info, lot)
            price = float(tick.ask if side == "buy" else tick.bid)
            order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "magic": AUTOTRADE_MAGIC,
                "symbol": resolved_symbol,
                "volume": volume,
                "type": order_type,
                "price": round(price, digits),
                "deviation": 20,
                "type_time": mt5.ORDER_TIME_GTC,
                "comment": AUTOTRADE_COMMENT,
            }
            if sl is not None:
                request["sl"] = round(float(sl), digits)
            if tp is not None:
                request["tp"] = round(float(tp), digits)
            filling_modes = [
                ("FOK", mt5.ORDER_FILLING_FOK),
                ("IOC", mt5.ORDER_FILLING_IOC),
                ("RETURN", mt5.ORDER_FILLING_RETURN),
            ]
            result = None
            selected_mode = None
            for mode_name, mode_value in filling_modes:
                trial_request = dict(request)
                trial_request["type_filling"] = mode_value
                result = mt5.order_send(trial_request)
                selected_mode = mode_name
                if result is None:
                    continue
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    request = trial_request
                    break
                if result.retcode != 10030:
                    request = trial_request
                    break

            if result is None:
                code, description = mt5.last_error()
                raise RuntimeError(f"MT5 order_send returned nothing. Error {code}: {description}")
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                detail = getattr(result, "comment", "") or f"Retcode {result.retcode}"
                return {
                    "status": "rejected",
                    "detail": f"MT5 rejected order: {detail}",
                    "symbol": resolved_symbol,
                    "retcode": int(result.retcode),
                    "comment": getattr(result, "comment", ""),
                    "filling_mode": selected_mode,
                }
            return {
                "status": "placed",
                "detail": "Order sent to MT5.",
                "symbol": resolved_symbol,
                "side": side,
                "volume": volume,
                "price": round(price, digits),
                "sl": request.get("sl"),
                "tp": request.get("tp"),
                "ticket": int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0),
                "retcode": int(result.retcode),
                "comment": getattr(result, "comment", ""),
                "filling_mode": selected_mode,
            }
        finally:
            mt5.shutdown()


def get_cooldown_remaining_seconds() -> int:
    last_trade_at = float(AUTOTRADE_STATE.get("last_trade_at", 0.0) or 0.0)
    if last_trade_at <= 0:
        return 0
    remaining = AUTOTRADE_COOLDOWN_SECONDS - (time.time() - last_trade_at)
    return max(0, int(remaining))


def sync_autotrade_lifecycle() -> None:
    with MT5_LOCK:
        if not mt5.initialize():
            raise RuntimeError("Could not connect to MetaTrader 5. Make sure MT5 is open and logged in.")

        try:
            positions = mt5.positions_get() or []
            orders = mt5.orders_get() or []
            active = bool(positions or orders)
            was_active = bool(AUTOTRADE_STATE.get("trade_active", False))
            seen_open = bool(AUTOTRADE_STATE.get("trade_seen_open", False))
            active_trade = None
            if positions:
                position = positions[0]
                active_trade = {
                    "kind": "position",
                    "ticket": int(getattr(position, "ticket", 0) or 0),
                    "symbol": str(getattr(position, "symbol", "") or ""),
                    "side": "buy" if int(getattr(position, "type", -1)) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "sell",
                    "volume": float(getattr(position, "volume", 0.0) or 0.0),
                    "price": float(getattr(position, "price_open", 0.0) or 0.0),
                    "sl": float(getattr(position, "sl", 0.0) or 0.0),
                    "tp": float(getattr(position, "tp", 0.0) or 0.0),
                }
            elif orders:
                order = orders[0]
                active_trade = {
                    "kind": "order",
                    "ticket": int(getattr(order, "ticket", 0) or 0),
                    "symbol": str(getattr(order, "symbol", "") or ""),
                    "side": "buy" if int(getattr(order, "type", -1)) in {getattr(mt5, "ORDER_TYPE_BUY", 0), getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2), getattr(mt5, "ORDER_TYPE_BUY_STOP", 4), getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", 6)} else "sell",
                    "volume": float(getattr(order, "volume_current", 0.0) or getattr(order, "volume_initial", 0.0) or 0.0),
                    "price": float(getattr(order, "price_open", 0.0) or 0.0),
                    "sl": float(getattr(order, "sl", 0.0) or 0.0),
                    "tp": float(getattr(order, "tp", 0.0) or 0.0),
                }
            if active:
                AUTOTRADE_STATE["trade_seen_open"] = True
            if was_active and not active and seen_open:
                AUTOTRADE_STATE["last_trade_at"] = time.time()
                AUTOTRADE_STATE["trade_seen_open"] = False
            AUTOTRADE_STATE["trade_active"] = active
            AUTOTRADE_STATE["active_trade"] = active_trade
        finally:
            mt5.shutdown()


def fetch_closed_deals_history(period: str = "daily", date_from: str | None = None, date_to: str | None = None) -> dict[str, object]:
    with MT5_LOCK:
        if not mt5.initialize():
            raise RuntimeError("Could not connect to MetaTrader 5. Make sure MT5 is open and logged in.")

        try:
            utc_from = datetime(2000, 1, 1, tzinfo=timezone.utc)
            utc_to = datetime.now(timezone.utc) + timedelta(days=1)
            deals = mt5.history_deals_get(utc_from, utc_to)
            orders = mt5.history_orders_get(utc_from, utc_to)
            if deals is None:
                code, description = mt5.last_error()
                raise RuntimeError(f"Could not load MT5 history deals. MT5 error {code}: {description}")
            if orders is None:
                orders = []

            selected_period, window_start, window_end = resolve_history_window(period, date_from, date_to)
            order_meta_by_key: dict[int, dict[str, float | int | str | None]] = {}
            for order in orders:
                order_key = int(getattr(order, "position_id", 0) or getattr(order, "ticket", 0) or 0)
                if not order_key:
                    continue
                sl_value = getattr(order, "sl", None)
                tp_value = getattr(order, "tp", None)
                existing = order_meta_by_key.get(order_key) or {}
                if sl_value not in (None, 0, 0.0):
                    existing["sl"] = float(sl_value)
                if tp_value not in (None, 0, 0.0):
                    existing["tp"] = float(tp_value)
                order_meta_by_key[order_key] = existing

            closed_entries = {DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY, DEAL_ENTRY_INOUT}
            grouped: dict[int, dict[str, object]] = {}
            dashboard_now = get_dashboard_now()
            today_start = dashboard_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

            total_net = 0.0
            total_profit = 0.0
            total_loss = 0.0
            today_net = 0.0
            today_profit = 0.0
            today_loss = 0.0
            win_count = 0
            loss_count = 0
            today_wins = 0
            today_losses = 0

            for deal in deals:
                entry = int(getattr(deal, "entry", -1))
                profit = float(getattr(deal, "profit", 0.0) or 0.0)
                commission = float(getattr(deal, "commission", 0.0) or 0.0)
                swap = float(getattr(deal, "swap", 0.0) or 0.0)
                fee = float(getattr(deal, "fee", 0.0) or 0.0)
                net = profit + commission + swap + fee
                deal_time = to_dashboard_time(int(getattr(deal, "time", 0) or 0))
                deal_type = int(getattr(deal, "type", -1))
                side = "buy" if deal_type == DEAL_TYPE_BUY else "sell" if deal_type == DEAL_TYPE_SELL else "other"
                position_id = int(getattr(deal, "position_id", 0) or 0)
                fallback_key = int(getattr(deal, "order", 0) or 0) or int(getattr(deal, "ticket", 0) or 0)
                group_key = position_id or fallback_key
                group = grouped.setdefault(
                    group_key,
                    {
                        "ticket": int(getattr(deal, "order", 0) or getattr(deal, "ticket", 0) or 0),
                        "position_id": position_id,
                        "symbol": str(getattr(deal, "symbol", "") or ""),
                        "side": side,
                        "volume": float(getattr(deal, "volume", 0.0) or 0.0),
                        "open_time": None,
                        "open_time_label": "--",
                        "open_price": None,
                        "close_time": None,
                        "close_time_label": "--",
                        "close_price": None,
                        "sl": None,
                        "tp": None,
                        "profit": 0.0,
                        "commission": 0.0,
                        "swap": 0.0,
                        "fee": 0.0,
                        "net": 0.0,
                        "comment": str(getattr(deal, "comment", "") or ""),
                        "open_comment": "",
                        "close_comment": "",
                        "has_close": False,
                    },
                )

                group["side"] = side if group["side"] == "other" else group["side"]
                group["symbol"] = group["symbol"] or str(getattr(deal, "symbol", "") or "")
                group["volume"] = max(float(group["volume"] or 0.0), float(getattr(deal, "volume", 0.0) or 0.0))
                current_comment = str(getattr(deal, "comment", "") or "")
                if current_comment:
                    group["comment"] = current_comment
                if getattr(deal, "sl", None) not in (None, 0, 0.0):
                    group["sl"] = float(getattr(deal, "sl"))
                if getattr(deal, "tp", None) not in (None, 0, 0.0):
                    group["tp"] = float(getattr(deal, "tp"))

                if entry in closed_entries:
                    group["has_close"] = True
                    close_time = int(getattr(deal, "time", 0) or 0)
                    if not group["close_time"] or close_time >= int(group["close_time"]):
                        group["close_time"] = close_time
                        group["close_time_label"] = deal_time.strftime("%Y-%m-%d %H:%M:%S")
                        group["close_price"] = float(getattr(deal, "price", 0.0) or 0.0)
                        group["close_comment"] = current_comment
                    group["profit"] = float(group["profit"]) + profit
                    group["commission"] = float(group["commission"]) + commission
                    group["swap"] = float(group["swap"]) + swap
                    group["fee"] = float(group["fee"]) + fee
                    group["net"] = float(group["net"]) + net
                else:
                    open_time = int(getattr(deal, "time", 0) or 0)
                    if not group["open_time"] or open_time <= int(group["open_time"]):
                        group["open_time"] = open_time
                        group["open_time_label"] = deal_time.strftime("%Y-%m-%d %H:%M:%S")
                        group["open_price"] = float(getattr(deal, "price", 0.0) or 0.0)
                        group["open_comment"] = current_comment

            rows: list[dict[str, object]] = []
            for group in grouped.values():
                if not group["has_close"]:
                    continue
                order_meta = order_meta_by_key.get(int(group["position_id"] or 0)) or order_meta_by_key.get(int(group["ticket"] or 0)) or {}
                if group["sl"] in (None, 0, 0.0) and order_meta.get("sl") not in (None, 0, 0.0):
                    group["sl"] = float(order_meta["sl"])
                if group["tp"] in (None, 0, 0.0) and order_meta.get("tp") not in (None, 0, 0.0):
                    group["tp"] = float(order_meta["tp"])

                close_timestamp = int(group["close_time"] or 0)
                close_dt = to_dashboard_time(close_timestamp)
                is_today = today_start <= close_dt < today_end
                open_price = float(group["open_price"] or 0.0)
                close_price = float(group["close_price"] or 0.0)
                side = str(group["side"] or "other")
                change = close_price - open_price if side == "buy" else open_price - close_price if side == "sell" else close_price - open_price

                row = {
                    "ticket": int(group["ticket"] or 0),
                    "position_id": int(group["position_id"] or 0),
                    "symbol": str(group["symbol"] or ""),
                    "side": side,
                    "trade_type": classify_trade_type(str(group["comment"] or ""), str(group["symbol"] or "")),
                    "trade_source": classify_trade_source(str(group["open_comment"] or group["comment"] or "")),
                    "exit_reason": classify_exit_reason(str(group["close_comment"] or "")),
                    "volume": float(group["volume"] or 0.0),
                    "open_time": int(group["open_time"] or 0),
                    "open_time_label": str(group["open_time_label"]),
                    "open_price": open_price if open_price else None,
                    "sl": float(group["sl"]) if group["sl"] not in (None, 0, 0.0) else None,
                    "tp": float(group["tp"]) if group["tp"] not in (None, 0, 0.0) else None,
                    "close_time": close_timestamp,
                    "close_time_label": str(group["close_time_label"]),
                    "close_price": close_price if close_price else None,
                    "profit": round(float(group["profit"]), 2),
                    "commission": round(float(group["commission"]), 2),
                    "swap": round(float(group["swap"]), 2),
                    "fee": round(float(group["fee"]), 2),
                    "net": round(float(group["net"]), 2),
                    "change": round(change, 2),
                    "comment": str(group["comment"] or ""),
                    "open_comment": str(group["open_comment"] or ""),
                    "close_comment": str(group["close_comment"] or ""),
                    "is_today": is_today,
                }
                rows.append(row)

                total_net += float(group["net"])
                if float(group["net"]) >= 0:
                    total_profit += float(group["net"])
                    win_count += 1
                else:
                    total_loss += float(group["net"])
                    loss_count += 1

                if is_today:
                    today_net += float(group["net"])
                    if float(group["net"]) >= 0:
                        today_profit += float(group["net"])
                        today_wins += 1
                    else:
                        today_loss += float(group["net"])
                        today_losses += 1

            filtered_rows = [
                row for row in rows
                if window_start is None
                or window_end is None
                or (window_start <= to_dashboard_time(int(row["close_time"])) < window_end)
            ]
            filtered_rows.sort(key=lambda item: int(item["close_time"]), reverse=True)

            selected_net = sum(float(item["net"]) for item in filtered_rows)
            selected_profit = sum(float(item["net"]) for item in filtered_rows if float(item["net"]) >= 0)
            selected_loss = sum(float(item["net"]) for item in filtered_rows if float(item["net"]) < 0)
            selected_wins = sum(1 for item in filtered_rows if float(item["net"]) >= 0)
            selected_losses = sum(1 for item in filtered_rows if float(item["net"]) < 0)
            cumulative = 0.0
            equity_curve = []
            for item in sorted(filtered_rows, key=lambda row: int(row["close_time"])):
                cumulative += float(item["net"])
                equity_curve.append({
                    "time": int(item["close_time"]),
                    "label": str(item["close_time_label"]),
                    "net": round(float(item["net"]), 2),
                    "cumulative": round(cumulative, 2),
                })

            type_rollups: dict[str, dict[str, float | int | str]] = {}
            for item in filtered_rows:
                trade_type = str(item.get("trade_type") or "Manual / Unlabeled")
                bucket = type_rollups.setdefault(
                    trade_type,
                    {"type": trade_type, "trades": 0, "wins": 0, "losses": 0, "net": 0.0},
                )
                bucket["trades"] = int(bucket["trades"]) + 1
                bucket["net"] = float(bucket["net"]) + float(item["net"])
                if float(item["net"]) >= 0:
                    bucket["wins"] = int(bucket["wins"]) + 1
                else:
                    bucket["losses"] = int(bucket["losses"]) + 1

            type_stats = []
            for bucket in sorted(type_rollups.values(), key=lambda entry: (-int(entry["trades"]), str(entry["type"]))):
                trades = int(bucket["trades"])
                wins = int(bucket["wins"])
                type_stats.append({
                    "type": str(bucket["type"]),
                    "trades": trades,
                    "wins": wins,
                    "losses": int(bucket["losses"]),
                    "net": round(float(bucket["net"]), 2),
                    "win_rate": round((wins / trades) * 100, 2) if trades else 0.0,
                })

            def build_breakdown(items: list[dict[str, object]], key: str, label_key: str) -> list[dict[str, object]]:
                rollups: dict[str, dict[str, float | int | str]] = {}
                for item in items:
                    group_label = str(item.get(key) or "Unknown")
                    bucket = rollups.setdefault(
                        group_label,
                        {label_key: group_label, "trades": 0, "wins": 0, "losses": 0, "net": 0.0},
                    )
                    bucket["trades"] = int(bucket["trades"]) + 1
                    bucket["net"] = float(bucket["net"]) + float(item["net"])
                    if float(item["net"]) >= 0:
                        bucket["wins"] = int(bucket["wins"]) + 1
                    else:
                        bucket["losses"] = int(bucket["losses"]) + 1
                rows = []
                for bucket in sorted(rollups.values(), key=lambda entry: (-int(entry["trades"]), str(entry[label_key]))):
                    trades = int(bucket["trades"])
                    wins = int(bucket["wins"])
                    rows.append({
                        label_key: str(bucket[label_key]),
                        "trades": trades,
                        "wins": wins,
                        "losses": int(bucket["losses"]),
                        "net": round(float(bucket["net"]), 2),
                        "win_rate": round((wins / trades) * 100, 2) if trades else 0.0,
                    })
                return rows

            direction_stats = build_breakdown(filtered_rows, "side", "direction")
            source_stats = build_breakdown(filtered_rows, "trade_source", "source")
            exit_stats = build_breakdown(filtered_rows, "exit_reason", "exit")

            return {
                "timezone": "Local - 8h",
                "period": selected_period,
                "date_from": window_start.strftime("%Y-%m-%d") if window_start else None,
                "date_to": (window_end - timedelta(days=1)).strftime("%Y-%m-%d") if window_start and window_end and selected_period != "custom" else (window_end.strftime("%Y-%m-%d") if window_end and selected_period == "custom" else None),
                "today": {
                    "date": today_start.strftime("%Y-%m-%d"),
                    "net": round(today_net, 2),
                    "gross_profit": round(today_profit, 2),
                    "gross_loss": round(today_loss, 2),
                    "trade_count": today_wins + today_losses,
                    "wins": today_wins,
                    "losses": today_losses,
                },
                "all_time": {
                    "net": round(total_net, 2),
                    "gross_profit": round(total_profit, 2),
                    "gross_loss": round(total_loss, 2),
                    "trade_count": win_count + loss_count,
                    "wins": win_count,
                    "losses": loss_count,
                },
                "selected": {
                    "label": selected_period.title(),
                    "net": round(selected_net, 2),
                    "gross_profit": round(selected_profit, 2),
                    "gross_loss": round(selected_loss, 2),
                    "trade_count": selected_wins + selected_losses,
                    "wins": selected_wins,
                    "losses": selected_losses,
                },
                "deals": filtered_rows,
                "all_deals_count": len(rows),
                "equity_curve": equity_curve,
                "type_stats": type_stats,
                "breakdowns": {
                    "direction": direction_stats,
                    "source": source_stats,
                    "exit": exit_stats,
                },
            }
        finally:
            mt5.shutdown()


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.respond_json(HTTPStatus.OK, {"status": "ok", "message": "MT5 bridge ready."})
            return

        if parsed.path == "/api/history/dashboard":
            self.handle_history_dashboard(parsed.query)
            return

        if parsed.path == "/api/autotrade/status":
            self.handle_autotrade_status()
            return

        if parsed.path == "/api/board":
            self.handle_board(parsed.query)
            return

        if parsed.path == "/api/timeframe":
            self.handle_timeframe(parsed.query)
            return

        if parsed.path == "/api/sync":
            self.handle_sync(parsed.query)
            return

        if parsed.path == "/api/tick":
            self.handle_tick(parsed.query)
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/autotrade/config":
            self.handle_autotrade_config()
            return

        if parsed.path == "/api/autotrade/evaluate":
            self.handle_autotrade_evaluate()
            return

        self.respond_json(HTTPStatus.NOT_FOUND, {"detail": "Unknown endpoint."})

    def handle_board(self, query: str) -> None:
        params = parse_qs(query)
        symbol = normalize_symbol(params.get("symbol", ["XAUUSD"])[0])
        raw_limit = str(params.get("limit", ["ALL"])[0]).strip().upper()
        limit: int | None
        if raw_limit in {"ALL", ""}:
            limit = None
        else:
            try:
                limit = max(80, min(99999, int(raw_limit)))
            except ValueError:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Limit must be a number or ALL."})
                return

        try:
            board: dict[str, object] = {"symbol": symbol, "timeframes": {}}
            for timeframe in TIMEFRAME_MAP:
                resolved_symbol, candles = fetch_candles(symbol, timeframe, limit)
                board["symbol"] = resolved_symbol
                board["timeframes"][timeframe] = {
                    "candles": candles,
                    "summary": trend_summary(candles),
                }
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, board)

    def handle_sync(self, query: str) -> None:
        params = parse_qs(query)
        symbol = normalize_symbol(params.get("symbol", ["XAUUSD"])[0])
        try:
            board: dict[str, object] = {"symbol": symbol, "timeframes": {}}
            for timeframe in TIMEFRAME_MAP:
                resolved_symbol, candles = fetch_candles(symbol, timeframe, RECENT_SYNC_BARS)
                board["symbol"] = resolved_symbol
                board["timeframes"][timeframe] = {
                    "candles": candles,
                    "summary": trend_summary(candles),
                }
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, board)

    def handle_timeframe(self, query: str) -> None:
        params = parse_qs(query)
        symbol = normalize_symbol(params.get("symbol", ["XAUUSD"])[0])
        timeframe = str(params.get("timeframe", ["M1"])[0]).strip().upper()
        raw_limit = str(params.get("limit", ["ALL"])[0]).strip().upper()
        limit: int | None
        if raw_limit in {"ALL", ""}:
            limit = None
        else:
            try:
                limit = max(80, min(99999, int(raw_limit)))
            except ValueError:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Limit must be a number or ALL."})
                return

        try:
            resolved_symbol, candles = fetch_candles(symbol, timeframe, limit)
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(
            HTTPStatus.OK,
            {
                "symbol": resolved_symbol,
                "timeframe": timeframe,
                "candles": candles,
                "summary": trend_summary(candles),
            },
        )

    def handle_tick(self, query: str) -> None:
        params = parse_qs(query)
        symbol = normalize_symbol(params.get("symbol", ["XAUUSD"])[0])

        try:
            payload = fetch_tick(symbol)
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, payload)

    def handle_history_dashboard(self, query: str = "") -> None:
        params = parse_qs(query)
        period = str(params.get("period", ["daily"])[0]).strip().lower()
        date_from = str(params.get("date_from", [""])[0]).strip() or None
        date_to = str(params.get("date_to", [""])[0]).strip() or None
        try:
            payload = fetch_closed_deals_history(period=period, date_from=date_from, date_to=date_to)
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, payload)

    def read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def handle_autotrade_status(self) -> None:
        with AUTOTRADE_LOCK:
            try:
                sync_autotrade_lifecycle()
            except RuntimeError as error:
                self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
                return
            payload = {
                "enabled": AUTOTRADE_STATE["enabled"],
                "lot": AUTOTRADE_STATE["lot"],
                "one_trade_only": True,
                "last_signal_id": AUTOTRADE_STATE["last_signal_id"],
                "trade_active": AUTOTRADE_STATE["trade_active"],
                "trade_seen_open": AUTOTRADE_STATE["trade_seen_open"],
                "active_trade": AUTOTRADE_STATE["active_trade"],
                "cooldown_remaining_seconds": get_cooldown_remaining_seconds(),
                "cooldown_seconds": AUTOTRADE_COOLDOWN_SECONDS,
            }
        self.respond_json(HTTPStatus.OK, payload)

    def handle_autotrade_config(self) -> None:
        try:
            payload = self.read_json_body()
            enabled = bool(payload.get("enabled", False))
            lot = max(0.01, float(payload.get("lot", 0.01) or 0.01))
        except (ValueError, TypeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Auto trade config requires valid enabled and lot values."})
            return

        with AUTOTRADE_LOCK:
            AUTOTRADE_STATE["enabled"] = enabled
            AUTOTRADE_STATE["lot"] = lot
            response = {
                "enabled": AUTOTRADE_STATE["enabled"],
                "lot": AUTOTRADE_STATE["lot"],
                "one_trade_only": True,
            }
        self.respond_json(HTTPStatus.OK, response)

    def handle_autotrade_evaluate(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return

        symbol = normalize_symbol(str(payload.get("symbol", "XAUUSD")))
        side = str(payload.get("side", "")).strip().lower()
        signal_id = str(payload.get("signal_id", "")).strip()
        action = str(payload.get("action", "")).strip()
        if side not in {"buy", "sell"} or "Ready" not in action:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Auto trade evaluate requires a ready trade signal."})
            return

        try:
            lot = max(0.01, float(payload.get("lot", 0.01) or 0.01))
            sl = payload.get("sl")
            tp = payload.get("tp")
            sl_value = None if sl in {None, "", "--"} else float(sl)
            tp_value = None if tp in {None, "", "--"} else float(tp)
        except (TypeError, ValueError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "SL, TP, and lot must be numeric when provided."})
            return

        if sl_value is None or tp_value is None:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Auto trade requires both SL and TP."})
            return

        with AUTOTRADE_LOCK:
            try:
                sync_autotrade_lifecycle()
            except RuntimeError as error:
                self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
                return
            if not AUTOTRADE_STATE["enabled"]:
                self.respond_json(HTTPStatus.OK, {"status": "disabled", "detail": "Auto trade is disabled."})
                return
            now = time.time()
            if signal_id and signal_id == AUTOTRADE_STATE["last_signal_id"]:
                self.respond_json(HTTPStatus.OK, {"status": "duplicate", "detail": "Signal already processed."})
                return
            if (not AUTOTRADE_STATE["trade_active"]) and now - float(AUTOTRADE_STATE["last_trade_at"]) < AUTOTRADE_COOLDOWN_SECONDS:
                self.respond_json(
                    HTTPStatus.OK,
                    {
                        "status": "cooldown",
                        "detail": "Auto trade cooldown active.",
                        "cooldown_remaining_seconds": get_cooldown_remaining_seconds(),
                    },
                )
                return
            AUTOTRADE_STATE["last_attempt_at"] = now

        try:
            result = place_market_order(symbol, side, lot, sl_value, tp_value)
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        with AUTOTRADE_LOCK:
            if result.get("status") == "placed" and signal_id:
                AUTOTRADE_STATE["last_signal_id"] = signal_id
                AUTOTRADE_STATE["trade_active"] = True
                AUTOTRADE_STATE["trade_seen_open"] = False

        self.respond_json(HTTPStatus.OK, result)

    def respond_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Serving Quantum workspace at http://{HOST}:{PORT}")
    print("Keep this terminal window open while using the site.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
