from __future__ import annotations

import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5


HOST = "127.0.0.1"
PORT = 8090
ROOT = Path(__file__).resolve().parent
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

        if parsed.path == "/api/board":
            self.handle_board(parsed.query)
            return

        if parsed.path == "/api/sync":
            self.handle_sync(parsed.query)
            return

        if parsed.path == "/api/tick":
            self.handle_tick(parsed.query)
            return

        super().do_GET()

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

    def handle_tick(self, query: str) -> None:
        params = parse_qs(query)
        symbol = normalize_symbol(params.get("symbol", ["XAUUSD"])[0])

        try:
            payload = fetch_tick(symbol)
        except RuntimeError as error:
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, payload)

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
