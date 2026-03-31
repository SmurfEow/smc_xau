from __future__ import annotations

import json
import os
import threading
import time
import base64
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
AI_REVIEW_LOG_PATH = ROOT / "ai_trade_reviews.json"
AI_DECISION_LOG_PATH = ROOT / "ai_trade_decisions.json"
AI_LOGIC_AUDIT_PATH = ROOT / "ai_logic_audit.json"
SNAPSHOT_DIR = ROOT / "snapshots"
LATEST_BOARD_IMAGE_PATH = SNAPSHOT_DIR / "latest-board.png"
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
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "local-setup-engine").strip() or "local-setup-engine"
OLLAMA_TIMEOUT_SECONDS = 45
TRADE_DECISION_ENGINE = os.environ.get("TRADE_DECISION_ENGINE", "local").strip().lower() or "local"
STRUCTURE_ENGINE_MODEL_NAME = "structure-engine"
AUTONOMOUS_AI_SYMBOL = os.environ.get("AUTONOMOUS_AI_SYMBOL", "XAUUSD").strip() or "XAUUSD"
AUTONOMOUS_AI_MODEL = os.environ.get("AUTONOMOUS_AI_MODEL", OLLAMA_DEFAULT_MODEL).strip() or OLLAMA_DEFAULT_MODEL
AUTONOMOUS_AI_INTERVAL_SECONDS = 5 * 60
AUTONOMOUS_AI_CONTEXT_BARS = 240
AUTONOMOUS_AI_STATE = {
    "enabled": True,
    "symbol": AUTONOMOUS_AI_SYMBOL,
    "model": AUTONOMOUS_AI_MODEL,
    "last_run_at": "",
    "last_signal_key": "",
    "last_result": None,
    "last_error": "",
}
AUTONOMOUS_AI_LOCK = threading.Lock()
CURRENT_STRATEGY_MODEL = "local-setup-engine"
AVAILABLE_SETUP_TYPES = [
    "buy_pullback",
    "sell_pullback",
    "breakout_buy",
    "breakdown_sell",
    "failed_breakout_sell",
    "failed_breakdown_buy",
    "range_buy",
    "range_sell",
]


def get_dashboard_now() -> datetime:
    return datetime.now(LOCAL_TZ) + DASHBOARD_TIME_OFFSET


def to_dashboard_time(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(int(unix_seconds or 0), LOCAL_TZ) + DASHBOARD_TIME_OFFSET


def classify_trading_session(dt: datetime | None) -> str:
    if dt is None:
        return "Unknown"
    hour = int(dt.hour)
    if 0 <= hour < 8:
        return "Asia"
    if 8 <= hour < 16:
        return "London"
    return "New York"


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


def load_ai_trade_reviews() -> list[dict[str, object]]:
    if not AI_REVIEW_LOG_PATH.exists():
        return []
    try:
        payload = json.loads(AI_REVIEW_LOG_PATH.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def load_ai_decision_log() -> list[dict[str, object]]:
    if not AI_DECISION_LOG_PATH.exists():
        return []
    try:
        payload = json.loads(AI_DECISION_LOG_PATH.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def load_ai_logic_audit() -> list[dict[str, object]]:
    if not AI_LOGIC_AUDIT_PATH.exists():
        return []
    try:
        payload = json.loads(AI_LOGIC_AUDIT_PATH.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_ai_trade_review(entry: dict[str, object]) -> None:
    rows = load_ai_trade_reviews()
    rows = [row for row in rows if isinstance(row, dict) and int(row.get("ticket", 0) or 0) != int(entry.get("ticket", 0) or 0)]
    rows.append(entry)
    rows = sorted(rows, key=lambda row: str(row.get("logged_at", "")), reverse=True)[:2000]
    AI_REVIEW_LOG_PATH.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def save_ai_decision_log(entry: dict[str, object]) -> None:
    rows = load_ai_decision_log()
    signal_key = str(entry.get("signal_key", "") or "").strip()
    if signal_key:
        rows = [row for row in rows if not (isinstance(row, dict) and str(row.get("signal_key", "") or "").strip() == signal_key)]
    rows.append(entry)
    rows = sorted(rows, key=lambda row: str(row.get("logged_at", "")), reverse=True)[:5000]
    AI_DECISION_LOG_PATH.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def append_ai_logic_audit(entry: dict[str, object]) -> None:
    rows = load_ai_logic_audit()
    rows.append(entry)
    rows = sorted(rows, key=lambda row: str(row.get("logged_at", "")), reverse=True)[:10000]
    AI_LOGIC_AUDIT_PATH.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def ai_review_map_by_ticket() -> dict[int, dict[str, object]]:
    mapped: dict[int, dict[str, object]] = {}
    for row in load_ai_trade_reviews():
        if not isinstance(row, dict):
            continue
        if str(row.get("model", "") or "").strip().lower() != CURRENT_STRATEGY_MODEL:
            continue
        ticket = int(row.get("ticket", 0) or 0)
        if ticket:
            mapped[ticket] = row
    return mapped


def parse_logged_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
    except ValueError:
        return None


def build_ai_signal_key(model: str, board: dict[str, object], result: dict[str, object]) -> str:
    market = board.get("market") if isinstance(board.get("market"), dict) else {}
    payload = {
        "model": str(model or "").strip(),
        "symbol": str(board.get("symbol", "") or "").strip(),
        "generated_at": str(board.get("generated_at", "") or "").strip(),
        "last_price": market.get("last_price"),
        "decision": str(result.get("decision", "") or "").strip(),
        "setup_type": str(result.get("setup_type", "") or "").strip(),
        "trigger_state": str(result.get("trigger_state", "") or "").strip(),
        "entry": result.get("entry"),
        "sl": result.get("sl"),
        "tp": result.get("tp"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def build_ai_logic_event(
    stage: str,
    status: str,
    symbol: str,
    detail: str = "",
    **extra: object,
) -> dict[str, object]:
    event = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "stage": str(stage or "").strip(),
        "status": str(status or "").strip(),
        "symbol": str(symbol or "").strip(),
        "detail": str(detail or "").strip(),
    }
    for key, value in extra.items():
        if value is None:
            continue
        event[str(key)] = value
    return event


def filter_ai_decision_rows(rows: list[dict[str, object]], window_start: datetime | None, window_end: datetime | None) -> list[dict[str, object]]:
    if window_start is None or window_end is None:
        return [row for row in rows if isinstance(row, dict)]
    filtered: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        logged_at = parse_logged_at(row.get("logged_at"))
        if logged_at is None:
            continue
        if window_start <= logged_at < window_end:
            filtered.append(row)
    return filtered


def build_ai_decision_analytics(rows: list[dict[str, object]], trade_reviews: dict[int, dict[str, object]] | None = None) -> dict[str, object]:
    entries = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("model", "") or "").strip().lower() == CURRENT_STRATEGY_MODEL
    ]
    total = len(entries)
    live_rows = [row for row in entries if str(row.get("trigger_state", "")).strip().lower() == "active_now"]
    executable_rows = [row for row in entries if bool(row.get("should_trade"))]
    no_trade_rows = [row for row in entries if str(row.get("decision", "")).strip().lower() == "no_trade"]
    decision_mix = {
        "buy": sum(1 for row in entries if str(row.get("decision", "")).strip().lower() == "buy"),
        "sell": sum(1 for row in entries if str(row.get("decision", "")).strip().lower() == "sell"),
        "no_trade": len(no_trade_rows),
    }
    phase_mix: dict[str, int] = {}
    bias_mix: dict[str, int] = {}
    location_mix: dict[str, int] = {}
    trigger_mix: dict[str, int] = {}
    entry_gate_stats = {
        "samples": 0,
        "zone_ok": 0,
        "zone_bad": 0,
        "confirmation_ok": 0,
        "confirmation_missing": 0,
        "fully_ready": 0,
    }
    missed_trade_stats = {
        "total": 0,
        "zone_missed": 0,
        "confirmation_missed": 0,
        "near_miss": 0,
        "blocked_in_zone": 0,
    }
    for row in entries:
        phase = str(row.get("market_phase") or "unknown").strip() or "unknown"
        bias = str(row.get("bias") or "unknown").strip() or "unknown"
        location = str(row.get("location") or "unknown").strip() or "unknown"
        trigger_state = str(row.get("trigger_state") or "unknown").strip() or "unknown"
        phase_mix[phase] = int(phase_mix.get(phase, 0)) + 1
        bias_mix[bias] = int(bias_mix.get(bias, 0)) + 1
        location_mix[location] = int(location_mix.get(location, 0)) + 1
        trigger_mix[trigger_state] = int(trigger_mix.get(trigger_state, 0)) + 1
        entry_checks = row.get("entry_checks") if isinstance(row.get("entry_checks"), dict) else {}
        if entry_checks:
            entry_gate_stats["samples"] = int(entry_gate_stats["samples"]) + 1
            zone_ok = bool(entry_checks.get("zone_ok"))
            confirmation_ok = bool(entry_checks.get("confirmation_ok"))
            entry_gate_stats["zone_ok" if zone_ok else "zone_bad"] = int(entry_gate_stats["zone_ok" if zone_ok else "zone_bad"]) + 1
            entry_gate_stats["confirmation_ok" if confirmation_ok else "confirmation_missing"] = int(entry_gate_stats["confirmation_ok" if confirmation_ok else "confirmation_missing"]) + 1
            if zone_ok and confirmation_ok:
                entry_gate_stats["fully_ready"] = int(entry_gate_stats["fully_ready"]) + 1
            if str(row.get("decision", "")).strip().lower() == "no_trade":
                missed_trade_stats["total"] = int(missed_trade_stats["total"]) + 1
                if not zone_ok:
                    missed_trade_stats["zone_missed"] = int(missed_trade_stats["zone_missed"]) + 1
                if not confirmation_ok:
                    missed_trade_stats["confirmation_missed"] = int(missed_trade_stats["confirmation_missed"]) + 1
                if zone_ok and not confirmation_ok:
                    missed_trade_stats["blocked_in_zone"] = int(missed_trade_stats["blocked_in_zone"]) + 1
                if zone_ok or confirmation_ok:
                    missed_trade_stats["near_miss"] = int(missed_trade_stats["near_miss"]) + 1

    blocked_rollups: dict[str, dict[str, int | str]] = {}
    blocker_setup_rollups: dict[str, dict[str, int]] = {}
    for row in entries:
        items = row.get("blocked_reasons", [])
        setup_label = str(row.get("setup_type") or "none").strip() or "none"
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            label = str(item or "").strip()
            if not label:
                continue
            bucket = blocked_rollups.setdefault(label, {"reason": label, "count": 0})
            bucket["count"] = int(bucket["count"]) + 1
            setup_bucket = blocker_setup_rollups.setdefault(label, {})
            setup_bucket[setup_label] = int(setup_bucket.get(setup_label, 0)) + 1

    setup_rollups: dict[str, dict[str, float | int | str]] = {
        setup: {"setup_type": setup, "decisions": 0, "live": 0, "executed": 0, "wins": 0, "losses": 0, "net": 0.0}
        for setup in AVAILABLE_SETUP_TYPES
    }
    for row in entries:
        setup = str(row.get("setup_type") or "none").strip() or "none"
        bucket = setup_rollups.setdefault(
            setup,
            {"setup_type": setup, "decisions": 0, "live": 0, "executed": 0, "wins": 0, "losses": 0, "net": 0.0},
        )
        bucket["decisions"] = int(bucket["decisions"]) + 1
        if str(row.get("trigger_state", "")).strip().lower() == "active_now":
            bucket["live"] = int(bucket["live"]) + 1

    if trade_reviews:
        for review in trade_reviews.values():
            if not isinstance(review, dict):
                continue
            setup = str(review.get("setup_type") or "none").strip() or "none"
            bucket = setup_rollups.setdefault(
                setup,
                {"setup_type": setup, "decisions": 0, "live": 0, "executed": 0, "wins": 0, "losses": 0, "net": 0.0},
            )
            bucket["executed"] = int(bucket["executed"]) + 1
            net = float(review.get("net", 0.0) or 0.0)
            bucket["net"] = float(bucket["net"]) + net
            if net >= 0:
                bucket["wins"] = int(bucket["wins"]) + 1
            else:
                bucket["losses"] = int(bucket["losses"]) + 1

    blocked_stats = [
        {"reason": str(bucket["reason"]), "count": int(bucket["count"])}
        for bucket in sorted(blocked_rollups.values(), key=lambda item: (-int(item["count"]), str(item["reason"])))
    ][:8]

    setup_stats = []
    for bucket in sorted(setup_rollups.values(), key=lambda item: (-int(item["decisions"]), str(item["setup_type"]))):
        executed = int(bucket["executed"])
        wins = int(bucket["wins"])
        setup_stats.append(
            {
                "setup_type": str(bucket["setup_type"]),
                "decisions": int(bucket["decisions"]),
                "live": int(bucket["live"]),
                "executed": executed,
                "wins": wins,
                "losses": int(bucket["losses"]),
                "net": round(float(bucket["net"]), 2),
                "activation_rate": round((int(bucket["live"]) / int(bucket["decisions"])) * 100, 2) if int(bucket["decisions"]) else 0.0,
                "win_rate": round((wins / executed) * 100, 2) if executed else 0.0,
                "expectancy": round(float(bucket["net"]) / executed, 2) if executed else 0.0,
            }
        )
    blocker_setup_stats = []
    top_reasons = [item["reason"] for item in blocked_stats[:6]]
    top_setups = [item["setup_type"] for item in setup_stats[:6]]
    for reason in top_reasons:
        setup_counts = blocker_setup_rollups.get(reason, {})
        blocker_setup_stats.append(
            {
                "reason": reason,
                "setups": [
                    {"setup_type": setup, "count": int(setup_counts.get(setup, 0))}
                    for setup in top_setups
                ],
            }
        )

    recent_rows = []
    for row in entries[:100]:
        recent_rows.append(
            {
                "logged_at": str(row.get("logged_at", "") or ""),
                "decision": str(row.get("decision", "") or ""),
                "setup_type": str(row.get("setup_type", "") or ""),
                "trigger_state": str(row.get("trigger_state", "") or ""),
                "market_phase": str(row.get("market_phase", "") or ""),
                "bias": str(row.get("bias", "") or ""),
                "location": str(row.get("location", "") or ""),
                "reason": str(row.get("reason", "") or ""),
                "blocked_reasons": row.get("blocked_reasons", []) if isinstance(row.get("blocked_reasons"), list) else [],
                "pattern_candidates": row.get("pattern_candidates", []) if isinstance(row.get("pattern_candidates"), list) else [],
                "entry_checks": row.get("entry_checks", {}) if isinstance(row.get("entry_checks"), dict) else {},
            }
        )

    family_map = {
        "continuation": {"buy_pullback", "sell_pullback"},
        "breakout": {"breakout_buy", "breakdown_sell"},
        "failure_reversal": {"failed_breakout_sell", "failed_breakdown_buy"},
        "range": {"range_buy", "range_sell"},
    }
    family_rollups: dict[str, dict[str, float | int | str]] = {
        family: {"family": family, "decisions": 0, "live": 0, "executed": 0, "wins": 0, "losses": 0, "net": 0.0}
        for family in family_map
    }
    for row in setup_stats:
        setup = str(row.get("setup_type") or "").strip()
        for family, members in family_map.items():
            if setup in members:
                bucket = family_rollups[family]
                bucket["decisions"] = int(bucket["decisions"]) + int(row.get("decisions", 0) or 0)
                bucket["live"] = int(bucket["live"]) + int(row.get("live", 0) or 0)
                bucket["executed"] = int(bucket["executed"]) + int(row.get("executed", 0) or 0)
                bucket["wins"] = int(bucket["wins"]) + int(row.get("wins", 0) or 0)
                bucket["losses"] = int(bucket["losses"]) + int(row.get("losses", 0) or 0)
                bucket["net"] = float(bucket["net"]) + float(row.get("net", 0.0) or 0.0)
                break
    family_stats = []
    for bucket in family_rollups.values():
        executed = int(bucket["executed"])
        wins = int(bucket["wins"])
        family_stats.append(
            {
                "family": str(bucket["family"]),
                "decisions": int(bucket["decisions"]),
                "live": int(bucket["live"]),
                "executed": executed,
                "wins": wins,
                "losses": int(bucket["losses"]),
                "net": round(float(bucket["net"]), 2),
                "activation_rate": round((int(bucket["live"]) / int(bucket["decisions"])) * 100, 2) if int(bucket["decisions"]) else 0.0,
                "win_rate": round((wins / executed) * 100, 2) if executed else 0.0,
                "expectancy": round(float(bucket["net"]) / executed, 2) if executed else 0.0,
            }
        )

    return {
        "summary": {
            "total": total,
            "live": len(live_rows),
            "executable": len(executable_rows),
            "no_trade": len(no_trade_rows),
            "activation_rate": round((len(live_rows) / total) * 100, 2) if total else 0.0,
            "execution_rate": round((len(executable_rows) / total) * 100, 2) if total else 0.0,
        },
        "decision_mix": decision_mix,
        "blocked_reasons": blocked_stats,
        "blocker_setup_stats": blocker_setup_stats,
        "setup_types": setup_stats[:8],
        "family_stats": sorted(family_stats, key=lambda item: (-int(item["executed"]), -float(item["win_rate"]), -int(item["decisions"]), str(item["family"]))),
        "entry_gate_stats": entry_gate_stats,
        "missed_trade_stats": missed_trade_stats,
        "phase_mix": phase_mix,
        "bias_mix": bias_mix,
        "location_mix": location_mix,
        "trigger_mix": trigger_mix,
        "latest": recent_rows[0] if recent_rows else None,
        "recent": recent_rows,
    }


def distance_to_level(price: float | None, level: float | None) -> float | None:
    if price is None or level is None:
        return None
    return round(abs(float(price) - float(level)), 2)


def infer_location_label(price: float | None, support: float | None, resistance: float | None, atr_value: float | None, market_state: dict[str, str]) -> str:
    if price is None:
        return "middle"
    support_gap = distance_to_level(price, support)
    resistance_gap = distance_to_level(price, resistance)
    threshold = max(float(atr_value or 0.0) * 1.2, 6.0)
    if support_gap is not None and support_gap <= threshold:
        return "support"
    if resistance_gap is not None and resistance_gap <= threshold:
        return "resistance"
    range_position = str(market_state.get("rangePosition", "")).strip().lower()
    if range_position == "upper":
        return "resistance"
    if range_position == "lower":
        return "support"
    return "middle"


def build_server_board_snapshot(symbol: str, limit: int = AUTONOMOUS_AI_CONTEXT_BARS) -> dict[str, object]:
    normalized_symbol = normalize_symbol(symbol)
    tick = fetch_tick(normalized_symbol)
    resolved_symbol = str(tick.get("symbol", normalized_symbol) or normalized_symbol)
    last_price = float(tick.get("last") or tick.get("bid") or tick.get("ask") or 0.0)
    bid = float(tick.get("bid") or 0.0)
    ask = float(tick.get("ask") or 0.0)
    spread = round(abs(ask - bid), 2) if bid and ask else None
    tick_time = int(tick.get("time") or 0)
    session = classify_trading_session(to_dashboard_time(tick_time)) if tick_time else "Unknown"

    board: dict[str, object] = {
        "symbol": resolved_symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": {
            "last_price": round(last_price, 2) if last_price else None,
            "bid": round(bid, 2) if bid else None,
            "ask": round(ask, 2) if ask else None,
            "spread": spread,
            "tick_time": tick_time,
            "session": session,
        },
        "timeframes": {},
    }

    timeframe_data: dict[str, object] = {}
    for timeframe in TIMEFRAME_MAP:
        _, candles = fetch_candles(resolved_symbol, timeframe, limit)
        summary = trend_summary(candles)
        levels = calculate_levels(candles)
        market_state = calculate_market_state(candles)
        atr_value = calculate_atr(candles)
        support = levels.get("support")
        resistance = levels.get("resistance")
        location = {
            "label": infer_location_label(last_price, support, resistance, atr_value, market_state),
            "distanceToSupport": distance_to_level(last_price, support),
            "distanceToResistance": distance_to_level(last_price, resistance),
        }
        timeframe_data[timeframe] = {
            "candles": candles,
            "summary": summary,
            "levels": {
                "support": round(float(support), 2) if support is not None else None,
                "resistance": round(float(resistance), 2) if resistance is not None else None,
                "swingHighs": get_swing_candidates(candles, "high"),
                "swingLows": get_swing_candidates(candles, "low"),
                "liquidityHighs": get_liquidity_pools(candles, "high"),
                "liquidityLows": get_liquidity_pools(candles, "low"),
            },
            "marketState": market_state,
            "location": location,
            "structure": classify_structure(candles),
            "volatility": {
                "atr": round(float(atr_value), 2) if atr_value is not None else None,
            },
        }
    board["timeframes"] = timeframe_data
    return board


def save_latest_board_image(image_b64: str) -> None:
    try:
        image_bytes = base64.b64decode(str(image_b64).encode("utf-8"), validate=True)
    except Exception as error:
        raise RuntimeError("Board snapshot image is not valid base64 PNG data.") from error
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_BOARD_IMAGE_PATH.write_bytes(image_bytes)


def load_latest_board_image_b64() -> str | None:
    try:
        if not LATEST_BOARD_IMAGE_PATH.exists():
            return None
        image_bytes = LATEST_BOARD_IMAGE_PATH.read_bytes()
        if not image_bytes:
            return None
        return base64.b64encode(image_bytes).decode("utf-8")
    except Exception:
        return None


def get_frame(board: dict[str, object], timeframe: str) -> dict[str, object]:
    timeframes = board.get("timeframes") if isinstance(board.get("timeframes"), dict) else {}
    frame = timeframes.get(timeframe) if isinstance(timeframes.get(timeframe), dict) else {}
    return frame


def latest_close(frame: dict[str, object]) -> float | None:
    candles = frame.get("candles") if isinstance(frame.get("candles"), list) else []
    if not candles:
        return None
    try:
        return float(candles[-1]["close"])
    except (TypeError, ValueError, KeyError):
        return None


def classify_frame_bias(frame: dict[str, object]) -> str:
    market_state = frame.get("marketState") if isinstance(frame.get("marketState"), dict) else {}
    regime = str(market_state.get("regime", "") or "").strip()
    trend = str(market_state.get("trend", "") or "").strip()
    if regime == "Uptrend" or trend == "Bullish":
        return "bullish"
    if regime == "Downtrend" or trend == "Bearish":
        return "bearish"
    return "mixed"


def infer_board_bias(board: dict[str, object]) -> str:
    h4_bias = classify_frame_bias(get_frame(board, "H4"))
    h1_bias = classify_frame_bias(get_frame(board, "H1"))
    bullish = sum(1 for value in (h4_bias, h1_bias) if value == "bullish")
    bearish = sum(1 for value in (h4_bias, h1_bias) if value == "bearish")
    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "mixed"


def infer_board_phase(board: dict[str, object], bias: str) -> str:
    h4 = get_frame(board, "H4")
    h1 = get_frame(board, "H1")
    h4_state = h4.get("marketState") if isinstance(h4.get("marketState"), dict) else {}
    h1_state = h1.get("marketState") if isinstance(h1.get("marketState"), dict) else {}
    regimes = {str(h4_state.get("regime", "") or "").strip(), str(h1_state.get("regime", "") or "").strip()}
    if "Range" in regimes and len(regimes) == 1:
        return "range"
    if bias == "bullish" and ("Uptrend" in regimes or str(h1_state.get("trend", "") or "").strip() == "Bullish"):
        return "uptrend"
    if bias == "bearish" and ("Downtrend" in regimes or str(h1_state.get("trend", "") or "").strip() == "Bearish"):
        return "downtrend"
    if "Compression" in regimes and len(regimes) == 1:
        return "range"
    return "transition"


def infer_setup_location(board: dict[str, object]) -> str:
    m30 = get_frame(board, "M30")
    m15 = get_frame(board, "M15")
    for frame in (m15, m30):
        location = frame.get("location") if isinstance(frame.get("location"), dict) else {}
        label = str(location.get("label", "") or "").strip().lower()
        if label in {"support", "resistance"}:
            return label
    m15_state = m15.get("marketState") if isinstance(m15.get("marketState"), dict) else {}
    m30_state = m30.get("marketState") if isinstance(m30.get("marketState"), dict) else {}
    for state in (m15_state, m30_state):
        range_position = str(state.get("rangePosition", "") or "").strip().lower()
        if range_position == "upper":
            return "resistance"
        if range_position == "lower":
            return "support"
    return "middle"


def infer_ltf_tone(board: dict[str, object]) -> str:
    m5 = get_frame(board, "M5")
    m1 = get_frame(board, "M1")
    m5_candles = m5.get("candles") if isinstance(m5.get("candles"), list) else []
    m1_candles = m1.get("candles") if isinstance(m1.get("candles"), list) else []
    if len(m5_candles) < 2 or len(m1_candles) < 2:
        return "mixed"
    m5_last = m5_candles[-1]
    m5_prev = m5_candles[-2]
    m1_last = m1_candles[-1]
    m1_prev = m1_candles[-2]
    m5_up = float(m5_last["close"]) >= float(m5_prev["close"]) and float(m5_last["close"]) >= float(m5_last["open"])
    m5_down = float(m5_last["close"]) <= float(m5_prev["close"]) and float(m5_last["close"]) <= float(m5_last["open"])
    m1_up = float(m1_last["close"]) >= float(m1_prev["close"])
    m1_down = float(m1_last["close"]) <= float(m1_prev["close"])
    if m5_up and m1_up:
        return "bullish"
    if m5_down and m1_down:
        return "bearish"
    return "mixed"


def choose_target(entry: float, side: str, candidates: list[float]) -> float | None:
    filtered = sorted({round(float(value), 2) for value in candidates if value is not None})
    if side == "buy":
        above = [value for value in filtered if value > entry]
        return above[0] if above else None
    below = [value for value in filtered if value < entry]
    return below[-1] if below else None


def clamp_target(entry: float, raw_target: float | None, side: str) -> float | None:
    if raw_target is None:
        return None
    distance = abs(raw_target - entry)
    if distance < 5:
        distance = 5
    if distance > 15:
        distance = 15
    return round(entry + distance, 2) if side == "buy" else round(entry - distance, 2)


def build_zone_text(levels: list[float], entry: float, side: str, atr_value: float) -> str:
    unique_levels = sorted({round(float(value), 2) for value in levels if value is not None})
    if not unique_levels:
        return "No clear zone"
    width = max(min(float(atr_value or 0.0) * 0.6, 3.0), 1.2)
    anchor = min(unique_levels, key=lambda value: abs(value - entry))
    if side == "buy":
        return f"{anchor - width:.2f}-{anchor:.2f}"
    return f"{anchor:.2f}-{anchor + width:.2f}"


def parse_zone_bounds(zone_text: str) -> tuple[float, float] | None:
    text = str(zone_text or "").strip()
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    try:
        low = float(left.strip())
        high = float(right.strip())
    except ValueError:
        return None
    return (min(low, high), max(low, high))


def price_within_entry_tolerance(
    *,
    price: float,
    side: str,
    zone_text: str,
    atr_value: float,
    location_label: str,
) -> bool:
    bounds = parse_zone_bounds(zone_text)
    tolerance = max(min(float(atr_value or 0.0) * 0.35, 2.5), 0.8)
    if bounds is None:
        return True
    low, high = bounds
    if low - tolerance <= price <= high + tolerance:
        return True
    if side == "buy" and location_label in {"support", "range_support"}:
        return price <= high + tolerance
    if side == "sell" and location_label in {"resistance", "range_resistance"}:
        return price >= low - tolerance
    return False


def entry_matches_setup_zone(
    *,
    entry: float,
    side: str,
    setup_type: str,
    zone_text: str,
    atr_value: float,
) -> bool:
    tolerance = max(min(float(atr_value or 0.0) * 0.35, 2.0), 0.6)
    bounds = parse_zone_bounds(zone_text)
    if bounds is not None:
      low, high = bounds
      return low - tolerance <= entry <= high + tolerance
    text = str(zone_text or "").strip().lower()
    if text.startswith("above "):
        try:
            level = float(text.replace("above", "", 1).strip())
        except ValueError:
            return True
        return entry >= level - tolerance
    if text.startswith("below "):
        try:
            level = float(text.replace("below", "", 1).strip())
        except ValueError:
            return True
        return entry <= level + tolerance
    if text.startswith("retest around "):
        try:
            level = float(text.replace("retest around", "", 1).strip())
        except ValueError:
            return True
        return abs(entry - level) <= max(tolerance * 2, 1.5)
    return True


def local_breakout_stop(
    *,
    side: str,
    entry: float,
    level: float | None,
    m5_candles: list[dict[str, float | int]],
    buffer: float,
    atr_value: float,
) -> float:
    recent = m5_candles[-4:] if len(m5_candles) >= 4 else m5_candles
    if side == "buy":
        recent_low = min((float(c["low"]) for c in recent), default=(entry - max(4.0, atr_value * 0.8)))
        anchor = min(level if level is not None else entry, recent_low)
        return round(min(anchor - buffer, entry - max(3.0, atr_value * 0.45)), 2)
    recent_high = max((float(c["high"]) for c in recent), default=(entry + max(4.0, atr_value * 0.8)))
    anchor = max(level if level is not None else entry, recent_high)
    return round(max(anchor + buffer, entry + max(3.0, atr_value * 0.45)), 2)


def local_pullback_stop(
    *,
    side: str,
    entry: float,
    m5_candles: list[dict[str, float | int]],
    nearby_level: float | None,
    buffer: float,
    atr_value: float,
) -> float:
    recent = m5_candles[-5:] if len(m5_candles) >= 5 else m5_candles
    min_distance = max(4.5, atr_value * 0.7)
    if side == "buy":
        recent_low = min((float(c["low"]) for c in recent), default=(entry - min_distance))
        structural_anchor = min([value for value in [recent_low, nearby_level] if value is not None], default=(entry - min_distance))
        return round(min(structural_anchor - buffer, entry - min_distance), 2)
    recent_high = max((float(c["high"]) for c in recent), default=(entry + min_distance))
    structural_anchor = max([value for value in [recent_high, nearby_level] if value is not None], default=(entry + min_distance))
    return round(max(structural_anchor + buffer, entry + min_distance), 2)


def has_entry_confirmation(
    *,
    side: str,
    m5_candles: list[dict[str, float | int]],
    m1_candles: list[dict[str, float | int]],
) -> bool:
    if len(m5_candles) < 2 or len(m1_candles) < 2:
        return False
    m5_last = m5_candles[-1]
    m5_prev = m5_candles[-2]
    m1_last = m1_candles[-1]
    m1_prev = m1_candles[-2]
    if side == "buy":
        m5_confirm = float(m5_last["close"]) >= float(m5_last["open"]) and float(m5_last["close"]) >= float(m5_prev["close"])
        m1_confirm = float(m1_last["close"]) >= float(m1_last["open"]) and float(m1_last["close"]) >= float(m1_prev["close"])
        return m5_confirm and m1_confirm
    m5_confirm = float(m5_last["close"]) <= float(m5_last["open"]) and float(m5_last["close"]) <= float(m5_prev["close"])
    m1_confirm = float(m1_last["close"]) <= float(m1_last["open"]) and float(m1_last["close"]) <= float(m1_prev["close"])
    return m5_confirm and m1_confirm


def recent_candles(frame: dict[str, object], count: int) -> list[dict[str, float | int]]:
    candles = frame.get("candles") if isinstance(frame.get("candles"), list) else []
    return candles[-min(count, len(candles)):] if candles else []


def safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def detect_breakout_hold(side: str, level: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 3 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer breakout hold"}
    last = m5_candles[-1]
    prev = m5_candles[-2]
    m1_last = m1_candles[-1]
    if side == "buy":
        passed = float(prev["close"]) > level and float(last["low"]) >= level and float(last["close"]) >= float(prev["close"]) and float(m1_last["close"]) >= float(m1_last["open"])
        return {"passed": passed, "detail": f"Hold above {level:.2f}" if passed else f"No clean hold above {level:.2f}"}
    passed = float(prev["close"]) < level and float(last["high"]) <= level and float(last["close"]) <= float(prev["close"]) and float(m1_last["close"]) <= float(m1_last["open"])
    return {"passed": passed, "detail": f"Hold below {level:.2f}" if passed else f"No clean hold below {level:.2f}"}


def detect_failed_break(side: str, level: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 3 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer failed break"}
    last = m5_candles[-1]
    prev = m5_candles[-2]
    m1_last = m1_candles[-1]
    if side == "sell":
        swept = float(prev["high"]) > level or float(last["high"]) > level
        failed = float(last["close"]) < level and float(m1_last["close"]) <= float(m1_last["open"])
        return {"passed": swept and failed, "detail": f"Failed breakout above {level:.2f}" if swept and failed else f"No failed breakout above {level:.2f}"}
    swept = float(prev["low"]) < level or float(last["low"]) < level
    failed = float(last["close"]) > level and float(m1_last["close"]) >= float(m1_last["open"])
    return {"passed": swept and failed, "detail": f"Failed breakdown below {level:.2f}" if swept and failed else f"No failed breakdown below {level:.2f}"}


def detect_pullback_continuation(side: str, support: float | None, resistance: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if len(m5_candles) < 3 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer continuation"}
    last = m5_candles[-1]
    prev = m5_candles[-2]
    m1_last = m1_candles[-1]
    m1_prev = m1_candles[-2]
    if side == "buy":
        near_support = support is not None and min(float(last["low"]), float(prev["low"])) <= support + 3.0
        turn = float(last["close"]) >= float(last["open"]) and float(last["close"]) >= float(prev["close"]) and float(m1_last["close"]) >= float(m1_prev["close"])
        return {"passed": near_support and turn, "detail": "Support held and M5/M1 turned up" if near_support and turn else "No clean bullish continuation"}
    near_resistance = resistance is not None and max(float(last["high"]), float(prev["high"])) >= resistance - 3.0
    turn = float(last["close"]) <= float(last["open"]) and float(last["close"]) <= float(prev["close"]) and float(m1_last["close"]) <= float(m1_prev["close"])
    return {"passed": near_resistance and turn, "detail": "Resistance held and M5/M1 turned down" if near_resistance and turn else "No clean bearish continuation"}


def detect_range_edge_reversal(side: str, support: float | None, resistance: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if len(m5_candles) < 3 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer range reversal"}
    last = m5_candles[-1]
    prev = m5_candles[-2]
    if side == "buy":
        touched = support is not None and min(float(last["low"]), float(prev["low"])) <= support + 2.0
        reclaimed = float(last["close"]) > float(prev["close"]) and float(last["close"]) > float(last["open"])
        return {"passed": touched and reclaimed, "detail": "Range low held and reclaimed" if touched and reclaimed else "No clean range-low reversal"}
    touched = resistance is not None and max(float(last["high"]), float(prev["high"])) >= resistance - 2.0
    rejected = float(last["close"]) < float(prev["close"]) and float(last["close"]) < float(last["open"])
    return {"passed": touched and rejected, "detail": "Range high rejected" if touched and rejected else "No clean range-high reversal"}


def detect_retest_hold(side: str, level: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 4 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer retest hold/fail"}
    first = m5_candles[-4]
    second = m5_candles[-3]
    third = m5_candles[-2]
    last = m5_candles[-1]
    if side == "buy":
        broke = float(first["close"]) > level or float(second["close"]) > level
        retested = float(third["low"]) <= level + 1.5 and float(last["low"]) >= level
        held = float(last["close"]) >= float(third["close"])
        return {"passed": broke and retested and held, "detail": f"Retest hold above {level:.2f}" if broke and retested and held else f"No clean retest hold above {level:.2f}"}
    broke = float(first["close"]) < level or float(second["close"]) < level
    retested = float(third["high"]) >= level - 1.5 and float(last["high"]) <= level
    held = float(last["close"]) <= float(third["close"])
    return {"passed": broke and retested and held, "detail": f"Retest fail below {level:.2f}" if broke and retested and held else f"No clean retest fail below {level:.2f}"}


def detect_liquidity_sweep_reversal(side: str, level: float | None, m5_candles: list[dict[str, float | int]], m1_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 3 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer liquidity sweep"}
    prev = m5_candles[-2]
    last = m5_candles[-1]
    if side == "buy":
        swept = float(prev["low"]) < level or float(last["low"]) < level
        reclaimed = float(last["close"]) > level and float(last["close"]) > float(last["open"])
        return {"passed": swept and reclaimed, "detail": f"Liquidity sweep below {level:.2f} reclaimed" if swept and reclaimed else f"No bullish liquidity sweep at {level:.2f}"}
    swept = float(prev["high"]) > level or float(last["high"]) > level
    rejected = float(last["close"]) < level and float(last["close"]) < float(last["open"])
    return {"passed": swept and rejected, "detail": f"Liquidity sweep above {level:.2f} rejected" if swept and rejected else f"No bearish liquidity sweep at {level:.2f}"}


def detect_compression_breakout(side: str, level: float | None, m5_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 5:
        return {"passed": False, "detail": "Need clearer compression breakout"}
    compression = m5_candles[-5:-2]
    latest = m5_candles[-1]
    avg_range = sum(abs(float(c["high"]) - float(c["low"])) for c in compression) / len(compression)
    last_range = abs(float(latest["high"]) - float(latest["low"]))
    if side == "buy":
        passed = last_range > avg_range * 1.35 and float(latest["close"]) > level
        return {"passed": passed, "detail": f"Compression breakout above {level:.2f}" if passed else f"No bullish compression breakout above {level:.2f}"}
    passed = last_range > avg_range * 1.35 and float(latest["close"]) < level
    return {"passed": passed, "detail": f"Compression breakdown below {level:.2f}" if passed else f"No bearish compression breakdown below {level:.2f}"}


def detect_double_level_reaction(side: str, level: float | None, m5_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 6:
        return {"passed": False, "detail": "Need clearer double reaction"}
    recent = m5_candles[-6:]
    touches = 0
    for candle in recent:
        if side == "buy" and float(candle["low"]) <= level + 1.5:
            touches += 1
        if side == "sell" and float(candle["high"]) >= level - 1.5:
            touches += 1
    last = recent[-1]
    if side == "buy":
        passed = touches >= 2 and float(last["close"]) > float(last["open"])
        return {"passed": passed, "detail": f"Double bottom reclaim near {level:.2f}" if passed else f"No double-bottom reclaim near {level:.2f}"}
    passed = touches >= 2 and float(last["close"]) < float(last["open"])
    return {"passed": passed, "detail": f"Double top rejection near {level:.2f}" if passed else f"No double-top rejection near {level:.2f}"}


def detect_shallow_pullback_continuation(side: str, level: float | None, m5_candles: list[dict[str, float | int]]) -> dict[str, object]:
    if level is None or len(m5_candles) < 4:
        return {"passed": False, "detail": "Need clearer shallow continuation"}
    last = m5_candles[-1]
    prev = m5_candles[-2]
    prior = m5_candles[-4:-1]
    if side == "buy":
        held = min(float(c["low"]) for c in prior) > level - 2.0
        continued = float(last["close"]) > float(prev["high"])
        return {"passed": held and continued, "detail": f"Shallow bullish continuation above {level:.2f}" if held and continued else f"No shallow bullish continuation above {level:.2f}"}
    held = max(float(c["high"]) for c in prior) < level + 2.0
    continued = float(last["close"]) < float(prev["low"])
    return {"passed": held and continued, "detail": f"Shallow bearish continuation below {level:.2f}" if held and continued else f"No shallow bearish continuation below {level:.2f}"}


def build_trade_payload(
    *,
    board: dict[str, object],
    side: str,
    market_phase: str,
    bias: str,
    setup_type: str,
    location: str,
    zone_text: str,
    reason: str,
    why: list[str],
    conflicts: list[str],
    trigger_text: str,
    execution_plan: str,
    entry: float | None,
    sl: float | None,
    tp1: float | None,
    tp2: float | None,
    model: str = "local-setup-engine",
    pattern_candidates: list[dict[str, object]] | None = None,
    entry_checks: dict[str, object] | None = None,
) -> dict[str, object]:
    decision = side if entry is not None and sl is not None and tp1 is not None else "no_trade"
    active = decision in {"buy", "sell"}
    rr = round(abs(tp1 - entry) / abs(entry - sl), 2) if active and tp1 is not None and entry is not None and sl is not None and entry != sl else None
    return {
        "decision": decision,
        "should_trade": active,
        "market_phase": market_phase,
        "bias": bias,
        "setup_type": setup_type if active else "none",
        "trigger_state": "active_now" if active else "waiting",
        "location": location,
        "entry_zone": zone_text,
        "entry": round(entry, 2) if entry is not None else None,
        "sl": round(sl, 2) if sl is not None else None,
        "tp": round(tp1, 2) if tp1 is not None else None,
        "stop_loss": round(sl, 2) if sl is not None else None,
        "take_profit_1": round(tp1, 2) if tp1 is not None else None,
        "take_profit_2": round(tp2, 2) if tp2 is not None else None,
        "rr": rr,
        "invalidation": f"{'Break below' if side == 'buy' else 'Break above'} {sl:.2f} invalidates the setup." if active and sl is not None else "Wait for a clean trigger and invalidation.",
        "reason": reason,
        "why": why[:4],
        "conflicts": conflicts[:4],
        "execution_plan": execution_plan,
        "do_not_do": [
            "Do not force the trade in the middle",
            "Do not widen the stop after entry",
            "Do not chase if price extends too far",
        ],
        "setup": ("BUY THE PULLBACK" if setup_type == "buy_pullback" else
                  "SELL THE RALLY" if setup_type == "sell_pullback" else
                  "BUY THE BREAKOUT HOLD" if setup_type == "breakout_buy" else
                  "SELL THE BREAKDOWN HOLD" if setup_type == "breakdown_sell" else
                  "SELL THE FAILED BREAKOUT" if setup_type == "failed_breakout_sell" else
                  "BUY THE FAILED BREAKDOWN" if setup_type == "failed_breakdown_buy" else
                  "BUY RANGE SUPPORT" if setup_type == "range_buy" else
                  "SELL RANGE RESISTANCE" if setup_type == "range_sell" else
                  "WAIT FOR CLEANER STRUCTURE"),
        "zone": zone_text,
        "wait_for": [] if active else ["Better location", "Cleaner M5/M1 timing", "Clear invalidation"],
        "entry_note": "Entry is valid now." if active else "No clean trade right now.",
        "tp_plan": ([f"TP1: {tp1:.2f}"] if tp1 is not None else []) + ([f"TP2: {tp2:.2f}"] if tp2 is not None else []),
        "plan": execution_plan,
        "trigger": trigger_text,
        "room": f"Target near {tp1:.2f}" if tp1 is not None else "",
        "context_summary": f"Phase {market_phase}; Bias {bias}; Location {location}",
        "trigger_summary": trigger_text,
        "execution_summary": execution_plan,
        "analysis": reason if active else f"Decision: NO TRADE. {reason}",
        "model": model,
        "blocked_reasons": conflicts[:4] if not active else [],
        "pattern_candidates": pattern_candidates[:5] if isinstance(pattern_candidates, list) else [],
        "entry_checks": entry_checks if isinstance(entry_checks, dict) else {},
        "classifier_state": {
            "market_phase": market_phase,
            "bias": bias,
            "location": location,
            "trigger_state": "active_now" if active else "waiting",
            "decision": decision,
        },
    }


def build_local_trade_setup(board: dict[str, object]) -> dict[str, object]:
    market = board.get("market") if isinstance(board.get("market"), dict) else {}
    current_price = float(market.get("last_price") or latest_close(get_frame(board, "M5")) or 0.0)
    bias = infer_board_bias(board)
    market_phase = infer_board_phase(board, bias)
    location = infer_setup_location(board)
    ltf_tone = infer_ltf_tone(board)

    h1 = get_frame(board, "H1")
    m30 = get_frame(board, "M30")
    m15 = get_frame(board, "M15")
    m5 = get_frame(board, "M5")
    h1_levels = h1.get("levels") if isinstance(h1.get("levels"), dict) else {}
    m30_levels = m30.get("levels") if isinstance(m30.get("levels"), dict) else {}
    m15_levels = m15.get("levels") if isinstance(m15.get("levels"), dict) else {}
    m5_levels = m5.get("levels") if isinstance(m5.get("levels"), dict) else {}
    m5_atr = ((m5.get("volatility") if isinstance(m5.get("volatility"), dict) else {}) or {}).get("atr")
    atr_value = float(m5_atr) if m5_atr is not None else (calculate_atr(m5.get("candles") if isinstance(m5.get("candles"), list) else []) or 6.0)
    buffer = max(atr_value * 0.25, 0.8)

    support_candidates = [m5_levels.get("support"), m15_levels.get("support"), m30_levels.get("support"), h1_levels.get("support")]
    resistance_candidates = [m5_levels.get("resistance"), m15_levels.get("resistance"), m30_levels.get("resistance"), h1_levels.get("resistance")]
    near_support_candidates = [m5_levels.get("support"), m15_levels.get("support")]
    near_resistance_candidates = [m5_levels.get("resistance"), m15_levels.get("resistance")]
    support_zone = [float(value) for value in support_candidates if value is not None]
    resistance_zone = [float(value) for value in resistance_candidates if value is not None]
    near_support_zone = [float(value) for value in near_support_candidates if value is not None]
    near_resistance_zone = [float(value) for value in near_resistance_candidates if value is not None]
    pullback_buy_zone = build_zone_text(near_support_zone, current_price, "buy", atr_value) if near_support_zone else (build_zone_text(support_zone, current_price, "buy", atr_value) if support_zone else "No clear support zone")
    rally_sell_zone = build_zone_text(near_resistance_zone, current_price, "sell", atr_value) if near_resistance_zone else (build_zone_text(resistance_zone, current_price, "sell", atr_value) if resistance_zone else "No clear resistance zone")

    why: list[str] = []
    conflicts: list[str] = []
    location_note = ""

    if bias == "bullish":
        why.append("H1/H4 still lean bullish")
    elif bias == "bearish":
        why.append("H1/H4 still lean bearish")
    else:
        conflicts.append("Higher timeframe bias is mixed")

    if location == "resistance":
        location_note = "Price is pressing into upper structure / resistance"
    elif location == "support":
        location_note = "Price is near support / pullback value"
    else:
        location_note = "Price is in the middle of structure"

    if ltf_tone == "bullish":
        why.append("M5/M1 timing is turning up")
    elif ltf_tone == "bearish":
        why.append("M5/M1 timing is turning down")
    else:
        conflicts.append("M5/M1 timing is mixed")

    if location_note:
        if location == "middle":
            conflicts.append(location_note)
        else:
            why.append(location_note)

    m5_candles = recent_candles(m5, 10)
    m1_candles = recent_candles(get_frame(board, "M1"), 8)
    nearest_support = max([value for value in support_zone if value <= current_price], default=(min(support_zone) if support_zone else None))
    nearest_resistance = min([value for value in resistance_zone if value >= current_price], default=(max(resistance_zone) if resistance_zone else None))
    buy_zone_ok = price_within_entry_tolerance(
        price=current_price,
        side="buy",
        zone_text=pullback_buy_zone,
        atr_value=atr_value,
        location_label="support",
    )
    sell_zone_ok = price_within_entry_tolerance(
        price=current_price,
        side="sell",
        zone_text=rally_sell_zone,
        atr_value=atr_value,
        location_label="resistance",
    )
    buy_confirmation_ok = has_entry_confirmation(side="buy", m5_candles=m5_candles, m1_candles=m1_candles)
    sell_confirmation_ok = has_entry_confirmation(side="sell", m5_candles=m5_candles, m1_candles=m1_candles)

    pattern_candidates: list[dict[str, object]] = []

    def add_candidate(
        side: str,
        setup_type: str,
        detector: dict[str, object],
        base_score: int,
        reason_text: str,
        zone_text: str,
        location_label: str,
    ) -> None:
        if not detector.get("passed"):
            return
        if not price_within_entry_tolerance(
            price=current_price,
            side=side,
            zone_text=zone_text,
            atr_value=atr_value,
            location_label=location_label,
        ):
            return
        if not has_entry_confirmation(side=side, m5_candles=m5_candles, m1_candles=m1_candles):
            return
        entry = round(current_price, 2)
        if side == "buy":
            if setup_type == "breakout_buy":
                sl = local_breakout_stop(side="buy", entry=entry, level=nearest_resistance, m5_candles=m5_candles, buffer=buffer, atr_value=atr_value)
            else:
                sl = local_pullback_stop(side="buy", entry=entry, m5_candles=m5_candles, nearby_level=nearest_support, buffer=buffer, atr_value=atr_value)
            raw_target = choose_target(entry, "buy", resistance_zone + [value for value in support_zone if value > entry])
            tp1 = clamp_target(entry, raw_target if raw_target is not None else entry + max(6.0, abs(entry - sl) * 1.2), "buy")
            tp2 = round(entry + min(max(abs(tp1 - entry) * 1.35, 7.0), 15.0), 2) if tp1 is not None else None
        else:
            if setup_type == "breakdown_sell":
                sl = local_breakout_stop(side="sell", entry=entry, level=nearest_support, m5_candles=m5_candles, buffer=buffer, atr_value=atr_value)
            else:
                sl = local_pullback_stop(side="sell", entry=entry, m5_candles=m5_candles, nearby_level=nearest_resistance, buffer=buffer, atr_value=atr_value)
            raw_target = choose_target(entry, "sell", support_zone + [value for value in resistance_zone if value < entry])
            tp1 = clamp_target(entry, raw_target if raw_target is not None else entry - max(6.0, abs(entry - sl) * 1.2), "sell")
            tp2 = round(entry - min(max(abs(entry - tp1) * 1.35, 7.0), 15.0), 2) if tp1 is not None else None
        if not entry_matches_setup_zone(entry=entry, side=side, setup_type=setup_type, zone_text=zone_text, atr_value=atr_value):
            return
        rr = round(abs(tp1 - entry) / abs(entry - sl), 2) if tp1 is not None and entry != sl else 0.0
        pattern_candidates.append({
            "side": side,
            "setup_type": setup_type,
            "score": base_score + (8 if rr >= 1.0 else 0) + (4 if location_label in {"support", "resistance", "breakout_zone", "breakdown_zone"} else 0),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rr": rr,
            "zone_text": zone_text,
            "reason": reason_text,
            "trigger": str(detector.get("detail", "") or "Pattern confirmed"),
            "location_label": location_label,
        })

    if bias == "bullish":
        add_candidate("buy", "buy_pullback", detect_pullback_continuation("buy", nearest_support, nearest_resistance, m5_candles, m1_candles), 78, "Bullish context, support-side pullback, and lower timeframe recovery are aligned.", pullback_buy_zone, "support")
        add_candidate("buy", "breakout_buy", detect_breakout_hold("buy", nearest_resistance, m5_candles, m1_candles), 72, "Bullish breakout is holding above resistance.", f"Above {nearest_resistance:.2f}" if nearest_resistance is not None else "Breakout zone", "breakout_zone")
        add_candidate("buy", "failed_breakdown_buy", detect_failed_break("buy", nearest_support, m5_candles, m1_candles), 74, "Support was swept and reclaimed with bullish timing.", pullback_buy_zone, "support")
        add_candidate("buy", "breakout_buy", detect_retest_hold("buy", nearest_resistance, m5_candles, m1_candles), 76, "Resistance broke, retested, and held as support.", f"Retest around {nearest_resistance:.2f}" if nearest_resistance is not None else "Retest zone", "breakout_zone")
        add_candidate("buy", "failed_breakdown_buy", detect_liquidity_sweep_reversal("buy", nearest_support, m5_candles, m1_candles), 75, "Sell-side liquidity was swept and price reclaimed support.", pullback_buy_zone, "support")
        add_candidate("buy", "breakout_buy", detect_compression_breakout("buy", nearest_resistance, m5_candles), 70, "Compression released into a bullish breakout.", f"Above {nearest_resistance:.2f}" if nearest_resistance is not None else "Compression breakout", "breakout_zone")
        add_candidate("buy", "range_buy", detect_double_level_reaction("buy", nearest_support, m5_candles), 67, "Double-bottom style reclaim formed at support.", pullback_buy_zone, "support")
        add_candidate("buy", "buy_pullback", detect_shallow_pullback_continuation("buy", nearest_support, m5_candles), 71, "Trend stayed firm and resumed after a shallow pullback.", pullback_buy_zone, "support")
        if market_phase == "range":
            add_candidate("buy", "range_buy", detect_range_edge_reversal("buy", nearest_support, nearest_resistance, m5_candles, m1_candles), 68, "Range low is holding with bullish reversal timing.", pullback_buy_zone, "support")

    if bias == "bearish":
        add_candidate("sell", "sell_pullback", detect_pullback_continuation("sell", nearest_support, nearest_resistance, m5_candles, m1_candles), 78, "Bearish context, resistance-side rally, and lower timeframe weakness are aligned.", rally_sell_zone, "resistance")
        add_candidate("sell", "breakdown_sell", detect_breakout_hold("sell", nearest_support, m5_candles, m1_candles), 72, "Bearish breakdown is holding below support.", f"Below {nearest_support:.2f}" if nearest_support is not None else "Breakdown zone", "breakdown_zone")
        add_candidate("sell", "failed_breakout_sell", detect_failed_break("sell", nearest_resistance, m5_candles, m1_candles), 74, "Resistance was swept and rejected with bearish timing.", rally_sell_zone, "resistance")
        add_candidate("sell", "breakdown_sell", detect_retest_hold("sell", nearest_support, m5_candles, m1_candles), 76, "Support broke, retested, and failed from below.", f"Retest around {nearest_support:.2f}" if nearest_support is not None else "Retest zone", "breakdown_zone")
        add_candidate("sell", "failed_breakout_sell", detect_liquidity_sweep_reversal("sell", nearest_resistance, m5_candles, m1_candles), 75, "Buy-side liquidity was swept and rejected from resistance.", rally_sell_zone, "resistance")
        add_candidate("sell", "breakdown_sell", detect_compression_breakout("sell", nearest_support, m5_candles), 70, "Compression released into a bearish breakdown.", f"Below {nearest_support:.2f}" if nearest_support is not None else "Compression breakdown", "breakdown_zone")
        add_candidate("sell", "range_sell", detect_double_level_reaction("sell", nearest_resistance, m5_candles), 67, "Double-top style rejection formed at resistance.", rally_sell_zone, "resistance")
        add_candidate("sell", "sell_pullback", detect_shallow_pullback_continuation("sell", nearest_resistance, m5_candles), 71, "Trend stayed weak and resumed after a shallow rally.", rally_sell_zone, "resistance")
        if market_phase == "range":
            add_candidate("sell", "range_sell", detect_range_edge_reversal("sell", nearest_support, nearest_resistance, m5_candles, m1_candles), 68, "Range high is rejecting with bearish reversal timing.", rally_sell_zone, "resistance")

    ranked_candidates = sorted(pattern_candidates, key=lambda item: float(item["score"]), reverse=True)
    compact_candidates = [
        {
            "setup_type": str(item.get("setup_type", "") or ""),
            "side": str(item.get("side", "") or ""),
            "score": round(float(item.get("score", 0.0) or 0.0), 2),
            "rr": round(float(item.get("rr", 0.0) or 0.0), 2),
            "zone": str(item.get("zone_text", "") or ""),
            "trigger": str(item.get("trigger", "") or ""),
        }
        for item in ranked_candidates[:5]
    ]
    best = ranked_candidates[0] if ranked_candidates else None
    if best is not None:
        return build_trade_payload(
            board=board,
            side=str(best["side"]),
            market_phase=market_phase,
            bias=bias,
            setup_type=str(best["setup_type"]),
            location=str(best.get("location_label", location)),
            zone_text=str(best["zone_text"]),
            reason=str(best["reason"]),
            why=why + [f"Setup location is {str(best.get('location_label', location)).replace('_', ' ')}", str(best["trigger"])],
            conflicts=conflicts,
            trigger_text=str(best["trigger"]),
            execution_plan=f"{str(best['side']).upper()} from {str(best['zone_text'])} with local invalidation and practical target.",
            entry=safe_float(best["entry"]),
            sl=safe_float(best["sl"]),
            tp1=safe_float(best["tp1"]),
            tp2=safe_float(best["tp2"]),
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": buy_zone_ok if str(best["side"]) == "buy" else sell_zone_ok,
                "confirmation_ok": buy_confirmation_ok if str(best["side"]) == "buy" else sell_confirmation_ok,
                "zone_text": str(best["zone_text"]),
                "price": round(current_price, 2),
            },
        )

    if bias == "bullish":
        if not buy_zone_ok:
            conflicts.append("Price has moved away from the buy zone")
        if not buy_confirmation_ok:
            conflicts.append("M5/M1 confirmation candle is missing")
        return build_trade_payload(
            board=board,
            side="none",
            market_phase=market_phase,
            bias=bias,
            setup_type="none",
            location=location,
            zone_text=pullback_buy_zone,
            reason="Bullish context is clear, but none of the pullback, breakout-hold, failed-breakdown, or range-buy patterns are active yet.",
            why=why,
            conflicts=conflicts,
            trigger_text="Waiting for M5/M1 bullish confirmation.",
            execution_plan="Wait for a pullback hold, breakout hold, or failed breakdown reclaim before buying.",
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": buy_zone_ok,
                "confirmation_ok": buy_confirmation_ok,
                "zone_text": pullback_buy_zone,
                "price": round(current_price, 2),
            },
        )

    if bias == "bearish":
        if not sell_zone_ok:
            conflicts.append("Price has moved away from the sell zone")
        if not sell_confirmation_ok:
            conflicts.append("M5/M1 confirmation candle is missing")
        return build_trade_payload(
            board=board,
            side="none",
            market_phase=market_phase,
            bias=bias,
            setup_type="none",
            location=location,
            zone_text=rally_sell_zone,
            reason="Bearish context is clear, but none of the rally-sell, breakdown-hold, failed-breakout, or range-sell patterns are active yet.",
            why=why,
            conflicts=conflicts,
            trigger_text="Waiting for M5/M1 bearish confirmation.",
            execution_plan="Wait for a rally rejection, breakdown hold, or failed breakout before selling.",
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": sell_zone_ok,
                "confirmation_ok": sell_confirmation_ok,
                "zone_text": rally_sell_zone,
                "price": round(current_price, 2),
            },
        )

    return build_trade_payload(
        board=board,
        side="none",
        market_phase=market_phase,
        bias=bias,
        setup_type="none",
        location=location,
        zone_text="No clear zone",
        reason="Context is mixed and no clean pattern has activated.",
        why=why,
        conflicts=conflicts,
        trigger_text="No trigger is active now.",
        execution_plan="Wait for clearer H4/H1 bias and cleaner M5/M1 timing.",
        entry=None,
        sl=None,
        tp1=None,
        tp2=None,
        pattern_candidates=compact_candidates,
        entry_checks={
            "zone_ok": False,
            "confirmation_ok": False,
            "zone_text": "No clear zone",
            "price": round(current_price, 2),
        },
    )

def model_safe_token(model: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in str(model or "model"))
    trimmed = normalized.strip("-")
    return trimmed or "model"


def http_json(url: str, method: str = "GET", payload: dict[str, object] | None = None, timeout: int = OLLAMA_TIMEOUT_SECONDS) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore") if hasattr(error, "read") else ""
        raise RuntimeError(detail or f"Upstream request failed with HTTP {error.code}.") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach upstream service at {url}.") from error

    try:
        parsed = json.loads(body or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("Upstream service returned invalid JSON.") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("Upstream service returned an unexpected payload.")
    return parsed


def get_decision_engine_status() -> dict[str, object]:
    return {
        "available": True,
        "base_url": "",
        "default_model": "local-setup-engine",
        "models": ["local-setup-engine"],
        "decision_engine": TRADE_DECISION_ENGINE,
    }



def execute_ai_trade_decision(model: str, board: dict[str, object], image: str | None = None) -> dict[str, object]:
    effective_model = "local-setup-engine"
    symbol = normalize_symbol(str(board.get("symbol", "XAUUSD")))
    append_ai_logic_audit(
        build_ai_logic_event(
            "ai_request",
            "started",
            symbol,
            model=effective_model,
            board_generated_at=str(board.get("generated_at", "") or ""),
            has_image=False,
        )
    )
    result = build_local_trade_setup(board)
    signal_key = build_ai_signal_key(effective_model, board, result)
    result["signal_key"] = signal_key
    save_ai_decision_log(
        {
            "signal_key": signal_key,
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "symbol": str(board.get("symbol", "") or ""),
            "generated_at": str(board.get("generated_at", "") or ""),
            "model": str(result.get("model", effective_model) or effective_model),
            "decision": str(result.get("decision", "") or ""),
            "should_trade": bool(result.get("should_trade")),
            "market_phase": str(result.get("market_phase", "") or ""),
            "bias": str(result.get("bias", "") or ""),
            "setup_type": str(result.get("setup_type", "") or ""),
            "trigger_state": str(result.get("trigger_state", "") or ""),
            "location": str(result.get("location", "") or ""),
            "reason": str(result.get("reason", "") or ""),
            "blocked_reasons": result.get("blocked_reasons", []) if isinstance(result.get("blocked_reasons"), list) else [],
            "context_summary": str(result.get("context_summary", "") or ""),
            "trigger_summary": str(result.get("trigger_summary", "") or ""),
            "execution_summary": str(result.get("execution_summary", "") or ""),
            "pattern_candidates": result.get("pattern_candidates", []) if isinstance(result.get("pattern_candidates"), list) else [],
            "entry_checks": result.get("entry_checks", {}) if isinstance(result.get("entry_checks"), dict) else {},
            "entry": result.get("entry"),
            "sl": result.get("sl"),
            "tp": result.get("tp"),
            "rr": result.get("rr"),
        }
    )
    append_ai_logic_audit(
        build_ai_logic_event(
            "ai_decision",
            "completed",
            symbol,
            model=str(result.get("model", effective_model) or effective_model),
            signal_key=signal_key,
            board_generated_at=str(board.get("generated_at", "") or ""),
            decision=str(result.get("decision", "") or ""),
            should_trade=bool(result.get("should_trade")),
            trigger_state=str(result.get("trigger_state", "") or ""),
            setup_type=str(result.get("setup_type", "") or ""),
            market_phase=str(result.get("market_phase", "") or ""),
            bias=str(result.get("bias", "") or ""),
            location=str(result.get("location", "") or ""),
            blocked_reasons=result.get("blocked_reasons", []) if isinstance(result.get("blocked_reasons"), list) else [],
            entry=result.get("entry"),
            sl=result.get("sl"),
            tp=result.get("tp"),
            rr=result.get("rr"),
        )
    )
    return result


def evaluate_autotrade_signal(
    *,
    symbol: str,
    side: str,
    lot: float,
    entry: object,
    sl: object,
    tp: object,
    signal_id: str = "",
    decision_key: str = "",
    action: str = "",
    ai_trade: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    normalized_symbol = normalize_symbol(symbol)
    normalized_side = str(side or "").strip().lower()
    ai_trade_payload = ai_trade if isinstance(ai_trade, dict) else {}

    def log_autotrade(stage: str, status: str, detail: str = "", **extra: object) -> None:
        append_ai_logic_audit(
            build_ai_logic_event(
                stage,
                status,
                normalized_symbol,
                detail=detail,
                signal_id=signal_id,
                decision_key=decision_key or str(ai_trade_payload.get("signal_key", "") or ""),
                side=normalized_side,
                action=action,
                **extra,
            )
        )

    if normalized_side not in {"buy", "sell"}:
        detail = "Auto trade evaluate requires buy or sell side."
        log_autotrade("autotrade_validation", "error", detail)
        return HTTPStatus.BAD_REQUEST.value, {"detail": detail}

    try:
        lot_value = max(0.01, float(lot or 0.01))
        entry_value = None if entry in {None, "", "--"} else float(entry)
        sl_value = None if sl in {None, "", "--"} else float(sl)
        tp_value = None if tp in {None, "", "--"} else float(tp)
    except (TypeError, ValueError):
        detail = "Entry, SL, TP, and lot must be numeric when provided."
        log_autotrade("autotrade_validation", "error", detail)
        return HTTPStatus.BAD_REQUEST.value, {"detail": detail}

    if entry_value is None or sl_value is None or tp_value is None:
        detail = "Auto trade requires entry, SL, and TP."
        log_autotrade("autotrade_validation", "error", detail)
        return HTTPStatus.BAD_REQUEST.value, {"detail": detail}

    valid_plan = sl_value < entry_value < tp_value if normalized_side == "buy" else tp_value < entry_value < sl_value
    if not valid_plan:
        detail = "Trade plan is invalid for the selected side."
        log_autotrade("autotrade_validation", "error", detail, entry=entry_value, sl=sl_value, tp=tp_value)
        return HTTPStatus.BAD_REQUEST.value, {"detail": detail}

    risk_distance = abs(entry_value - sl_value)
    target_distance = abs(tp_value - entry_value)
    rr = (target_distance / risk_distance) if risk_distance > 0 else 0.0

    log_autotrade(
        "autotrade_validation",
        "passed",
        "Trade plan passed validation.",
        entry=entry_value,
        sl=sl_value,
        tp=tp_value,
        rr=round(rr, 4),
        lot=lot_value,
        model=str(ai_trade_payload.get("model", "") or ""),
        trigger_state=str(ai_trade_payload.get("trigger_state", "") or ""),
        setup_type=str(ai_trade_payload.get("setup_type", "") or ""),
    )

    with AUTOTRADE_LOCK:
        sync_autotrade_lifecycle()
        if not AUTOTRADE_STATE["enabled"]:
            detail = "Auto trade is disabled."
            log_autotrade("autotrade_dispatch", "disabled", detail)
            return HTTPStatus.OK.value, {"status": "disabled", "detail": detail}
        now = time.time()
        if signal_id and signal_id == AUTOTRADE_STATE["last_signal_id"]:
            detail = "Signal already processed."
            log_autotrade("autotrade_dispatch", "duplicate", detail)
            return HTTPStatus.OK.value, {"status": "duplicate", "detail": detail}
        if (not AUTOTRADE_STATE["trade_active"]) and now - float(AUTOTRADE_STATE["last_trade_at"]) < AUTOTRADE_COOLDOWN_SECONDS:
            detail = "Auto trade cooldown active."
            remaining = get_cooldown_remaining_seconds()
            log_autotrade("autotrade_dispatch", "cooldown", detail, cooldown_remaining_seconds=remaining)
            return HTTPStatus.OK.value, {"status": "cooldown", "detail": detail, "cooldown_remaining_seconds": remaining}
        AUTOTRADE_STATE["last_attempt_at"] = now

    result = place_market_order(normalized_symbol, normalized_side, lot_value, sl_value, tp_value)
    log_autotrade(
        "autotrade_dispatch",
        str(result.get("status", "") or "unknown"),
        str(result.get("detail", "") or ""),
        ticket=result.get("ticket"),
        entry=entry_value,
        sl=sl_value,
        tp=tp_value,
        rr=round(rr, 4),
        lot=lot_value,
    )

    with AUTOTRADE_LOCK:
        if result.get("status") == "placed" and signal_id:
            AUTOTRADE_STATE["last_signal_id"] = signal_id
            AUTOTRADE_STATE["trade_active"] = True
            AUTOTRADE_STATE["trade_seen_open"] = False
            if ai_trade_payload:
                save_ai_trade_review(
                    {
                        "ticket": int(result.get("ticket", 0) or 0),
                        "logged_at": datetime.now(timezone.utc).isoformat(),
                        "symbol": result.get("symbol", normalized_symbol),
                        "side": normalized_side,
                        "entry": entry_value,
                        "sl": sl_value,
                        "tp": tp_value,
                        "rr": round(rr, 2),
                        "decision": str(ai_trade_payload.get("decision", normalized_side)).strip(),
                        "reason": str(ai_trade_payload.get("reason", "")).strip(),
                        "analysis": str(ai_trade_payload.get("analysis", "")).strip(),
                        "timeframe_alignment": str(ai_trade_payload.get("timeframe_alignment", "")).strip(),
                        "market_phase": str(ai_trade_payload.get("market_phase", "")).strip(),
                        "bias": str(ai_trade_payload.get("bias", "")).strip(),
                        "location": str(ai_trade_payload.get("location", "")).strip(),
                        "setup_type": str(ai_trade_payload.get("setup_type", "")).strip(),
                        "trigger_state": str(ai_trade_payload.get("trigger_state", "")).strip(),
                        "context_summary": str(ai_trade_payload.get("context_summary", "")).strip(),
                        "trigger_summary": str(ai_trade_payload.get("trigger_summary", "")).strip(),
                        "execution_summary": str(ai_trade_payload.get("execution_summary", "")).strip(),
                        "model": str(ai_trade_payload.get("model", "")).strip(),
                    }
                )
    return HTTPStatus.OK.value, result


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


def classify_structure(candles: list[dict[str, float | int]]) -> dict[str, int | str]:
    if len(candles) < 8:
        return {"trend": "Neutral", "score": 0}

    pivots_high: list[float] = []
    pivots_low: list[float] = []
    for index in range(2, len(candles) - 2):
        candle = candles[index]
        prev1 = candles[index - 1]
        prev2 = candles[index - 2]
        next1 = candles[index + 1]
        next2 = candles[index + 2]
        high = float(candle["high"])
        low = float(candle["low"])
        is_pivot_high = high >= float(prev1["high"]) and high >= float(prev2["high"]) and high >= float(next1["high"]) and high >= float(next2["high"])
        is_pivot_low = low <= float(prev1["low"]) and low <= float(prev2["low"]) and low <= float(next1["low"]) and low <= float(next2["low"])
        if is_pivot_high:
            pivots_high.append(high)
        if is_pivot_low:
            pivots_low.append(low)

    last_highs = pivots_high[-3:]
    last_lows = pivots_low[-3:]
    if len(last_highs) < 2 or len(last_lows) < 2:
        return {"trend": "Neutral", "score": 0}

    rising_highs = last_highs[-1] > last_highs[0]
    rising_lows = last_lows[-1] > last_lows[0]
    falling_highs = last_highs[-1] < last_highs[0]
    falling_lows = last_lows[-1] < last_lows[0]
    if rising_highs and rising_lows:
        return {"trend": "Bullish", "score": 2}
    if falling_highs and falling_lows:
        return {"trend": "Bearish", "score": 2}
    if rising_highs or rising_lows:
        return {"trend": "Bullish", "score": 1}
    if falling_highs or falling_lows:
        return {"trend": "Bearish", "score": 1}
    return {"trend": "Neutral", "score": 0}


def calculate_levels(candles: list[dict[str, float | int]], lookback: int = 120) -> dict[str, float | None]:
    if not candles:
        return {"support": None, "resistance": None}
    window = candles[-min(lookback, len(candles)):]
    support = None
    resistance = None
    for index in range(2, len(window) - 2):
        candle = window[index]
        prev1 = window[index - 1]
        prev2 = window[index - 2]
        next1 = window[index + 1]
        next2 = window[index + 2]
        high = float(candle["high"])
        low = float(candle["low"])
        is_pivot_high = high >= float(prev1["high"]) and high >= float(prev2["high"]) and high >= float(next1["high"]) and high >= float(next2["high"])
        is_pivot_low = low <= float(prev1["low"]) and low <= float(prev2["low"]) and low <= float(next1["low"]) and low <= float(next2["low"])
        if is_pivot_high:
            resistance = high
        if is_pivot_low:
            support = low
    fallback_window = window[-min(40, len(window)):]
    if resistance is None:
        resistance = max(float(candle["high"]) for candle in fallback_window)
    if support is None:
        support = min(float(candle["low"]) for candle in fallback_window)
    return {"support": support, "resistance": resistance}


def get_liquidity_pools(candles: list[dict[str, float | int]], kind: str, lookback: int = 60) -> list[float]:
    window = candles[-min(lookback, len(candles)):]
    if len(window) < 8:
        return []
    pivots: list[dict[str, float]] = []
    for index in range(2, len(window) - 2):
        candle = window[index]
        prev1 = window[index - 1]
        prev2 = window[index - 2]
        next1 = window[index + 1]
        next2 = window[index + 2]
        price = float(candle["high"] if kind == "high" else candle["low"])
        is_pivot = (
            price >= float(prev1["high"]) and price >= float(prev2["high"]) and price >= float(next1["high"]) and price >= float(next2["high"])
            if kind == "high"
            else price <= float(prev1["low"]) and price <= float(prev2["low"]) and price <= float(next1["low"]) and price <= float(next2["low"])
        )
        if is_pivot:
            pivots.append({"price": price, "time": float(candle["time"])})
    if not pivots:
        return []
    average_range = sum(abs(float(candle["high"]) - float(candle["low"])) for candle in window) / len(window)
    tolerance = max(average_range * 0.35, 1.0)
    clusters: list[dict[str, object]] = []
    for pivot in pivots:
        existing = next((cluster for cluster in clusters if abs(float(cluster["price"]) - float(pivot["price"])) <= tolerance), None)
        if existing is not None:
            points = existing["points"]
            assert isinstance(points, list)
            points.append(pivot)
            existing["price"] = sum(float(point["price"]) for point in points) / len(points)
            existing["latest_time"] = max(float(existing["latest_time"]), float(pivot["time"]))
        else:
            clusters.append({"price": float(pivot["price"]), "latest_time": float(pivot["time"]), "points": [pivot]})
    return [
        round(float(cluster["price"]), 2)
        for cluster in sorted(clusters, key=lambda item: float(item["latest_time"]))
        if len(cluster["points"]) >= 2
    ]


def get_swing_candidates(candles: list[dict[str, float | int]], kind: str, lookback: int = 80) -> list[float]:
    window = candles[-min(lookback, len(candles)):]
    if len(window) < 6:
        return []
    swings: list[float] = []
    for index in range(2, len(window) - 2):
        candle = window[index]
        prev1 = window[index - 1]
        prev2 = window[index - 2]
        next1 = window[index + 1]
        next2 = window[index + 2]
        price = float(candle["high"] if kind == "high" else candle["low"])
        is_swing = (
            price >= float(prev1["high"]) and price >= float(prev2["high"]) and price >= float(next1["high"]) and price >= float(next2["high"])
            if kind == "high"
            else price <= float(prev1["low"]) and price <= float(prev2["low"]) and price <= float(next1["low"]) and price <= float(next2["low"])
        )
        if is_swing:
            swings.append(round(price, 2))
    unique: list[float] = []
    seen: set[float] = set()
    for value in swings:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def calculate_market_state(candles: list[dict[str, float | int]]) -> dict[str, str]:
    if not candles:
        return {"regime": "--", "trend": "--", "rangePosition": "--"}
    regime_window = candles[-min(200, len(candles)):]
    trend_window = candles[-min(50, len(candles)):]
    state_window = candles[-min(20, len(candles)):]
    closes = [float(candle["close"]) for candle in regime_window]
    highs = [float(candle["high"]) for candle in regime_window]
    lows = [float(candle["low"]) for candle in regime_window]
    latest = closes[-1]
    visible_high = max(highs)
    visible_low = min(lows)
    visible_range = max(visible_high - visible_low, 0.00001)
    recent = state_window
    prior = trend_window[: max(0, len(trend_window) - len(state_window))]
    recent_closes = [float(candle["close"]) for candle in recent]
    prior_source = prior if prior else recent
    prior_closes = [float(candle["close"]) for candle in prior_source]
    recent_avg = sum(recent_closes) / len(recent_closes)
    prior_avg = sum(prior_closes) / len(prior_closes)
    avg_body = sum(abs(float(candle["close"]) - float(candle["open"])) for candle in recent) / len(recent)
    recent_high = max(float(candle["high"]) for candle in recent)
    recent_low = min(float(candle["low"]) for candle in recent)
    recent_range = max(recent_high - recent_low, 0.00001)
    prior_high = max(float(candle["high"]) for candle in prior_source)
    prior_low = min(float(candle["low"]) for candle in prior_source)
    prior_range = max(prior_high - prior_low, 0.00001)
    recent_slope = recent_avg - prior_avg
    structure = classify_structure(regime_window)
    compression_threshold = prior_range * 0.55
    body_threshold = visible_range * 0.02
    strong_slope_threshold = visible_range * 0.05
    transition_slope_threshold = visible_range * 0.025
    compression_slope_threshold = visible_range * 0.012
    range_threshold = visible_range * 0.018

    trend = str(structure["trend"])
    if trend == "Neutral":
        if recent_slope > transition_slope_threshold:
            trend = "Bullish"
        elif recent_slope < -transition_slope_threshold:
            trend = "Bearish"

    regime = "Range"
    if trend == "Bullish" and int(structure["score"]) >= 1 and recent_slope > transition_slope_threshold and latest > recent_avg:
        regime = "Uptrend"
        trend = "Bullish"
    elif trend == "Bearish" and int(structure["score"]) >= 1 and recent_slope < -transition_slope_threshold and latest < recent_avg:
        regime = "Downtrend"
        trend = "Bearish"
    elif recent_range < compression_threshold and avg_body < body_threshold and abs(recent_slope) < compression_slope_threshold and int(structure["score"]) == 0:
        regime = "Compression"
        if trend == "Neutral":
            trend = "Bullish" if latest >= recent_avg else "Bearish"
    elif abs(recent_slope) > strong_slope_threshold or int(structure["score"]) == 1:
        regime = "Transition"
    elif abs(recent_slope) < range_threshold and int(structure["score"]) == 0:
        regime = "Range"

    range_ratio = (latest - visible_low) / visible_range
    range_position = "Middle"
    if range_ratio >= 0.67:
        range_position = "Upper"
    elif range_ratio <= 0.33:
        range_position = "Lower"
    return {"regime": regime, "trend": trend, "rangePosition": range_position}


def calculate_atr(candles: list[dict[str, float | int]], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        previous_close = float(candles[index - 1]["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[:period]) / period
    for index in range(period, len(true_ranges)):
        atr = ((atr * (period - 1)) + true_ranges[index]) / period
    return atr



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
            ai_reviews = ai_review_map_by_ticket()
            ai_decision_rows = load_ai_decision_log()
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
                ai_meta = ai_reviews.get(int(row["ticket"] or 0)) or ai_reviews.get(int(row["position_id"] or 0))
                if ai_meta:
                    decision = str(ai_meta.get("decision", "") or "").strip().lower()
                    if decision in {"buy", "sell"}:
                        verdict = "acceptable"
                    else:
                        verdict = str(ai_meta.get("verdict", "") or "").strip().lower()
                    row["ai_verdict"] = verdict
                    row["ai_decision"] = decision
                    row["ai_execution"] = str(ai_meta.get("analysis", "") or "")
                    row["ai_summary"] = str(ai_meta.get("reason", "") or "").strip()
                    row["ai_logged_at"] = str(ai_meta.get("logged_at", "") or "")
                    row["ai_setup_type"] = str(ai_meta.get("setup_type", "") or "").strip()
                    row["ai_market_phase"] = str(ai_meta.get("market_phase", "") or "").strip()
                    row["ai_bias"] = str(ai_meta.get("bias", "") or "").strip()
                    row["ai_location"] = str(ai_meta.get("location", "") or "").strip()
                else:
                    row["ai_verdict"] = ""
                    row["ai_decision"] = ""
                    row["ai_execution"] = ""
                    row["ai_summary"] = ""
                    row["ai_logged_at"] = ""
                    row["ai_setup_type"] = ""
                    row["ai_market_phase"] = ""
                    row["ai_bias"] = ""
                    row["ai_location"] = ""
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
            session_rollups: dict[str, dict[str, float | int | str]] = {}
            for item in filtered_rows:
                open_dt = to_dashboard_time(int(item.get("open_time", 0) or 0)) if int(item.get("open_time", 0) or 0) else None
                session_label = classify_trading_session(open_dt)
                bucket = session_rollups.setdefault(
                    session_label,
                    {"session": session_label, "trades": 0, "wins": 0, "losses": 0, "net": 0.0},
                )
                bucket["trades"] = int(bucket["trades"]) + 1
                bucket["net"] = float(bucket["net"]) + float(item["net"])
                if float(item["net"]) >= 0:
                    bucket["wins"] = int(bucket["wins"]) + 1
                else:
                    bucket["losses"] = int(bucket["losses"]) + 1
            session_stats = []
            for label in ["Asia", "London", "New York"]:
                bucket = session_rollups.get(label)
                trades = int(bucket["trades"]) if bucket else 0
                wins = int(bucket["wins"]) if bucket else 0
                session_stats.append(
                    {
                        "session": label,
                        "trades": trades,
                        "wins": wins,
                        "losses": int(bucket["losses"]) if bucket else 0,
                        "net": round(float(bucket["net"]), 2) if bucket else 0.0,
                        "win_rate": round((wins / trades) * 100, 2) if trades else 0.0,
                        "expectancy": round((float(bucket["net"]) / trades), 2) if bucket and trades else 0.0,
                    }
                )
            setup_session_rollups: dict[str, dict[str, dict[str, float | int | str]]] = {}
            for item in filtered_rows:
                setup_label = str(item.get("ai_setup_type") or "").strip()
                if not setup_label:
                    continue
                open_dt = to_dashboard_time(int(item.get("open_time", 0) or 0)) if int(item.get("open_time", 0) or 0) else None
                session_label = classify_trading_session(open_dt)
                setup_bucket = setup_session_rollups.setdefault(setup_label, {})
                bucket = setup_bucket.setdefault(
                    session_label,
                    {"trades": 0, "wins": 0, "losses": 0, "net": 0.0},
                )
                bucket["trades"] = int(bucket["trades"]) + 1
                bucket["net"] = float(bucket["net"]) + float(item["net"])
                if float(item["net"]) >= 0:
                    bucket["wins"] = int(bucket["wins"]) + 1
                else:
                    bucket["losses"] = int(bucket["losses"]) + 1
            setup_session_stats = []
            for setup_label in AVAILABLE_SETUP_TYPES:
                sessions = setup_session_rollups.get(setup_label, {})
                cells = []
                for session_label in ["Asia", "London", "New York", "Unknown"]:
                    bucket = sessions.get(session_label)
                    trades = int(bucket["trades"]) if bucket else 0
                    wins = int(bucket["wins"]) if bucket else 0
                    cells.append(
                        {
                            "session": session_label,
                            "trades": trades,
                            "wins": wins,
                            "losses": int(bucket["losses"]) if bucket else 0,
                            "net": round(float(bucket["net"]), 2) if bucket else 0.0,
                            "win_rate": round((wins / trades) * 100, 2) if trades else 0.0,
                        }
                    )
                setup_session_stats.append({"setup_type": setup_label, "sessions": cells})
            filtered_ai_decisions = filter_ai_decision_rows(ai_decision_rows, window_start, window_end)
            executed_ai_rows = {
                int(item["ticket"]): {
                    "setup_type": item.get("ai_setup_type"),
                    "net": item.get("net"),
                }
                for item in filtered_rows
                if str(item.get("ai_setup_type", "")).strip() and int(item.get("ticket", 0) or 0)
            }
            ai_analytics = build_ai_decision_analytics(filtered_ai_decisions, executed_ai_rows)

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
                "session_stats": session_stats,
                "setup_session_stats": setup_session_stats,
                "breakdowns": {
                    "direction": direction_stats,
                    "source": source_stats,
                    "exit": exit_stats,
                },
                "ai_analytics": ai_analytics,
            }
        finally:
            mt5.shutdown()


def get_autonomous_ai_status_snapshot() -> dict[str, object]:
    status_model = str(AUTONOMOUS_AI_STATE.get("model", AUTONOMOUS_AI_MODEL) or AUTONOMOUS_AI_MODEL)
    with AUTONOMOUS_AI_LOCK:
        return {
            "enabled": bool(AUTONOMOUS_AI_STATE.get("enabled", False)),
            "symbol": str(AUTONOMOUS_AI_STATE.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL),
            "model": status_model,
            "decision_engine": TRADE_DECISION_ENGINE,
            "interval_seconds": AUTONOMOUS_AI_INTERVAL_SECONDS,
            "last_run_at": str(AUTONOMOUS_AI_STATE.get("last_run_at", "") or ""),
            "last_signal_key": str(AUTONOMOUS_AI_STATE.get("last_signal_key", "") or ""),
            "last_error": str(AUTONOMOUS_AI_STATE.get("last_error", "") or ""),
            "last_result": AUTONOMOUS_AI_STATE.get("last_result"),
        }


def run_autonomous_ai_cycle() -> dict[str, object]:
    with AUTONOMOUS_AI_LOCK:
        symbol = str(AUTONOMOUS_AI_STATE.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL)
        model = str(AUTONOMOUS_AI_STATE.get("model", AUTONOMOUS_AI_MODEL) or AUTONOMOUS_AI_MODEL)
    board = build_server_board_snapshot(symbol, AUTONOMOUS_AI_CONTEXT_BARS)
    image_b64 = load_latest_board_image_b64()
    result = execute_ai_trade_decision(model, board, image_b64)
    outcome: dict[str, object] = dict(result)
    outcome["board_generated_at"] = str(board.get("generated_at", "") or "")
    outcome["autotrade"] = None
    if bool(result.get("should_trade")) and str(result.get("decision", "")).strip().lower() in {"buy", "sell"}:
        status_code, autotrade_result = evaluate_autotrade_signal(
            symbol=str(board.get("symbol", symbol) or symbol),
            side=str(result.get("decision", "") or ""),
            lot=float(AUTOTRADE_STATE.get("lot", 0.01) or 0.01),
            entry=result.get("entry"),
            sl=result.get("sl"),
            tp=result.get("tp"),
            signal_id=str(result.get("signal_key", "") or ""),
            decision_key=str(result.get("signal_key", "") or ""),
            action="autonomous_cycle",
            ai_trade=result,
        )
        outcome["autotrade"] = {"status_code": status_code, **autotrade_result}
    with AUTONOMOUS_AI_LOCK:
        AUTONOMOUS_AI_STATE["last_run_at"] = datetime.now(timezone.utc).isoformat()
        AUTONOMOUS_AI_STATE["last_signal_key"] = str(result.get("signal_key", "") or "")
        AUTONOMOUS_AI_STATE["last_result"] = outcome
        AUTONOMOUS_AI_STATE["last_error"] = ""
    return outcome


def seconds_until_next_autonomous_boundary() -> float:
    now = time.time()
    return AUTONOMOUS_AI_INTERVAL_SECONDS - (now % AUTONOMOUS_AI_INTERVAL_SECONDS)


def autonomous_ai_worker() -> None:
    while True:
        try:
            with AUTONOMOUS_AI_LOCK:
                enabled = bool(AUTONOMOUS_AI_STATE.get("enabled", False))
            if enabled:
                run_autonomous_ai_cycle()
        except Exception as error:
            append_ai_logic_audit(
                build_ai_logic_event(
                    "autonomous_cycle",
                    "error",
                    str(AUTONOMOUS_AI_STATE.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL),
                    detail=str(error),
                    model=str(AUTONOMOUS_AI_STATE.get("model", AUTONOMOUS_AI_MODEL) or AUTONOMOUS_AI_MODEL),
                )
            )
            with AUTONOMOUS_AI_LOCK:
                AUTONOMOUS_AI_STATE["last_run_at"] = datetime.now(timezone.utc).isoformat()
                AUTONOMOUS_AI_STATE["last_error"] = str(error)
        time.sleep(max(1.0, seconds_until_next_autonomous_boundary()))


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

        if parsed.path == "/api/ai/status":
            self.handle_ai_status()
            return

        if parsed.path == "/api/ai/audit/recent":
            self.handle_ai_audit_recent()
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
        if parsed.path == "/api/ai/snapshot":
            self.handle_ai_snapshot()
            return

        if parsed.path == "/api/ai/trade":
            self.handle_ai_trade()
            return

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

    def handle_ai_status(self) -> None:
        payload = get_decision_engine_status()
        payload["autonomous"] = get_autonomous_ai_status_snapshot()
        self.respond_json(HTTPStatus.OK, payload)

    def handle_ai_audit_recent(self) -> None:
        rows = load_ai_logic_audit()
        self.respond_json(HTTPStatus.OK, {"events": rows[:100]})

    def handle_ai_trade(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return

        model = str(payload.get("model", OLLAMA_DEFAULT_MODEL) or OLLAMA_DEFAULT_MODEL).strip() or OLLAMA_DEFAULT_MODEL
        board = payload.get("board")
        if not isinstance(board, dict):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "AI trade requires a board snapshot object."})
            return

        image_b64 = None
        raw_image = payload.get("image")
        if isinstance(raw_image, str) and raw_image.strip():
            image_b64 = raw_image.strip()
        else:
            image_b64 = load_latest_board_image_b64()

        try:
            result = execute_ai_trade_decision(model, board, image_b64)
        except RuntimeError as error:
            symbol = normalize_symbol(str(board.get("symbol", "XAUUSD")))
            append_ai_logic_audit(build_ai_logic_event("ai_request", "error", symbol, detail=str(error), model=model, board_generated_at=str(board.get("generated_at", "") or "")))
            self.respond_json(HTTPStatus.BAD_GATEWAY, {"detail": str(error)})
            return

        self.respond_json(HTTPStatus.OK, result)

    def handle_ai_snapshot(self) -> None:
        try:
            payload = self.read_json_body()
        except ValueError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return

        image = payload.get("image")
        if not isinstance(image, str) or not image.strip():
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "AI snapshot requires a base64 image string."})
            return

        try:
            save_latest_board_image(image.strip())
        except RuntimeError as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
            return

        self.respond_json(
            HTTPStatus.OK,
            {
                "status": "saved",
                "path": str(LATEST_BOARD_IMAGE_PATH.name),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

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

        status_code, result = evaluate_autotrade_signal(
            symbol=str(payload.get("symbol", "XAUUSD") or "XAUUSD"),
            side=str(payload.get("side", "")).strip().lower(),
            lot=payload.get("lot", 0.01),
            entry=payload.get("entry"),
            sl=payload.get("sl"),
            tp=payload.get("tp"),
            signal_id=str(payload.get("signal_id", "")).strip(),
            decision_key=str(payload.get("decision_key", "")).strip(),
            action=str(payload.get("action", "")).strip(),
            ai_trade=payload.get("ai_trade") if isinstance(payload.get("ai_trade"), dict) else {},
        )
        self.respond_json(HTTPStatus(status_code), result)

    def respond_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The browser may cancel an in-flight request during rapid refresh/sync cycles.
            return


def main() -> None:
    autonomous_thread = threading.Thread(target=autonomous_ai_worker, name="autonomous-ai-worker", daemon=True)
    autonomous_thread.start()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Serving Quantum workspace at http://{HOST}:{PORT}")
    print(f"Autonomous AI loop active for {AUTONOMOUS_AI_SYMBOL} every {AUTONOMOUS_AI_INTERVAL_SECONDS // 60} minutes.")
    print("Keep this terminal window open while using the site.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
