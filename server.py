from __future__ import annotations

import json
import os
import threading
import time
import base64
import uuid
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
MARKET_TIMEZONE = timezone.utc
BROKER_OFFSET_FALLBACK_SECONDS = 3 * 60 * 60
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
AUTOTRADE_MAGIC = int(os.environ.get("AUTOTRADE_MAGIC", "20260324") or 20260324)
AUTOTRADE_COMMENT = "Quantum Auto"
AI_REVIEW_LOG_PATH = ROOT / "ai_trade_reviews.json"
AI_DECISION_LOG_PATH = ROOT / "ai_trade_decisions.json"
AI_LOGIC_AUDIT_PATH = ROOT / "ai_logic_audit.json"
SHEET_SYNC_STATE_PATH = ROOT / "google_sheet_sync_state.json"
SNAPSHOT_DIR = ROOT / "snapshots"
LATEST_BOARD_IMAGE_PATH = SNAPSHOT_DIR / "latest-board.png"
MANUAL_NEWS_CALENDAR_PATH = ROOT / "manual_news_calendar.json"
DEFAULT_NEWS_BLOCK_BEFORE_MINUTES = 20
DEFAULT_NEWS_BLOCK_AFTER_MINUTES = 20
MANUAL_NEWS_BLOCK_MINUTES = 45
AUTOTRADE_STATE = {
    "enabled": False,
    "lot": 0.01,
    "last_signal_id": "",
    "last_attempt_at": 0.0,
    "last_trade_at": 0.0,
    "trade_active": False,
    "trade_seen_open": False,
    "active_trade": None,
    "last_sheet_sync_ticket": 0,
}
AUTOTRADE_LOCK = threading.RLock()
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
GOOGLE_SHEET_SYNC_INTERVAL_SECONDS = 60
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
    "smc_buy",
    "smc_sell",
]
HISTORY_ALL_TIME_BASELINE = datetime(2026, 4, 1, tzinfo=MARKET_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)


def infer_broker_offset_seconds(symbol_hint: str = "XAUUSD.m") -> int:
    candidate_symbols = [symbol_hint, AUTONOMOUS_AI_SYMBOL, "XAUUSD.m", "XAUUSD"]
    seen: set[str] = set()
    for raw_symbol in candidate_symbols:
        symbol = str(raw_symbol or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
        tick_time = int(getattr(tick, "time", 0) or 0)
        if tick_time <= 0:
            continue
        current_utc = int(datetime.now(timezone.utc).timestamp())
        offset_seconds = tick_time - current_utc
        if abs(offset_seconds) > 12 * 60 * 60:
            continue
        return int(round(offset_seconds / 3600.0) * 3600)
    return BROKER_OFFSET_FALLBACK_SECONDS


def get_dashboard_now() -> datetime:
    offset_seconds = infer_broker_offset_seconds()
    return datetime.now(MARKET_TIMEZONE) + timedelta(seconds=offset_seconds)


def to_dashboard_time(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(int(unix_seconds or 0), MARKET_TIMEZONE)


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
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=MARKET_TIMEZONE)


def default_manual_news_calendar() -> dict[str, object]:
    return {
        "before_minutes": MANUAL_NEWS_BLOCK_MINUTES,
        "after_minutes": MANUAL_NEWS_BLOCK_MINUTES,
        "events": [],
        "updated_at": "",
    }


def _normalize_news_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def normalize_manual_news_event(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    date_text = str(payload.get("date", "") or "").strip()
    title = str(payload.get("title", "") or "").strip()
    time_text = _normalize_news_time(payload.get("time"))
    if not date_text or not title or not time_text:
        return None
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return None
    event_id = str(payload.get("id", "") or "").strip() or uuid.uuid4().hex
    return {
        "id": event_id,
        "date": date_text,
        "time": time_text,
        "title": title[:120],
    }


def normalize_manual_news_calendar(payload: object) -> dict[str, object]:
    baseline = default_manual_news_calendar()
    if not isinstance(payload, dict):
        return baseline
    events = []
    for item in payload.get("events", []) if isinstance(payload.get("events"), list) else []:
        normalized = normalize_manual_news_event(item)
        if normalized:
            events.append(normalized)
    events.sort(key=lambda item: (item["date"], item["time"], item["title"].lower(), item["id"]))
    return {
        "before_minutes": MANUAL_NEWS_BLOCK_MINUTES,
        "after_minutes": MANUAL_NEWS_BLOCK_MINUTES,
        "events": events,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def load_manual_news_calendar() -> dict[str, object]:
    try:
        raw = MANUAL_NEWS_CALENDAR_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_manual_news_calendar()
    except OSError:
        return default_manual_news_calendar()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return default_manual_news_calendar()
    return normalize_manual_news_calendar(payload)


def save_manual_news_calendar(payload: object) -> dict[str, object]:
    normalized = normalize_manual_news_calendar(payload)
    current = get_dashboard_now()
    existing_calendar = load_manual_news_calendar()
    existing_ids = {
        str(item.get("id", "") or "").strip()
        for item in existing_calendar.get("events", [])
        if isinstance(item, dict) and str(item.get("id", "") or "").strip()
    }
    normalized["events"] = [
        item
        for item in normalized["events"]
        if parse_manual_news_event_dt(str(item["date"]), str(item["time"])) >= current or str(item.get("id", "") or "").strip() in existing_ids
    ]
    MANUAL_NEWS_CALENDAR_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def parse_manual_news_event_dt(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M").replace(tzinfo=MARKET_TIMEZONE)


def build_manual_news_calendar_status(payload: object, now: datetime | None = None) -> dict[str, object]:
    calendar_state = normalize_manual_news_calendar(payload)
    current = now or get_dashboard_now()
    before_minutes = int(calendar_state["before_minutes"] or 0)
    after_minutes = int(calendar_state["after_minutes"] or 0)
    active_event = None
    upcoming_event = None
    days_with_events = sorted({str(item["date"]) for item in calendar_state["events"]})

    for item in calendar_state["events"]:
        event_dt = parse_manual_news_event_dt(str(item["date"]), str(item["time"]))
        block_start = event_dt - timedelta(minutes=before_minutes)
        block_end = event_dt + timedelta(minutes=after_minutes)
        event_payload = {
            **item,
            "event_at": event_dt.strftime("%Y-%m-%d %H:%M"),
            "block_start": block_start.strftime("%Y-%m-%d %H:%M"),
            "block_end": block_end.strftime("%Y-%m-%d %H:%M"),
            "minutes_until": int((event_dt - current).total_seconds() // 60),
        }
        if block_start <= current <= block_end:
            active_event = event_payload
            break
        if current < block_start and upcoming_event is None:
            upcoming_event = event_payload

    return {
        "before_minutes": before_minutes,
        "after_minutes": after_minutes,
        "events": calendar_state["events"],
        "days_with_events": days_with_events,
        "event_count": len(calendar_state["events"]),
        "updated_at": str(calendar_state.get("updated_at", "") or ""),
        "broker_now": current.strftime("%Y-%m-%d %H:%M"),
        "blocked": active_event is not None,
        "active_event": active_event,
        "upcoming_event": upcoming_event,
    }




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
        return normalized, HISTORY_ALL_TIME_BASELINE, None
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


def load_last_sheet_sync_ticket() -> int:
    if not SHEET_SYNC_STATE_PATH.exists():
        return 0
    try:
        payload = json.loads(SHEET_SYNC_STATE_PATH.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return 0
    return int(payload.get("last_sheet_sync_ticket", 0) or 0) if isinstance(payload, dict) else 0


def save_last_sheet_sync_ticket(ticket: int) -> None:
    SHEET_SYNC_STATE_PATH.write_text(
        json.dumps({"last_sheet_sync_ticket": int(ticket or 0)}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


AUTOTRADE_STATE["last_sheet_sync_ticket"] = load_last_sheet_sync_ticket()


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
        "smc_context": {"smc_buy", "smc_sell"},
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
        structure_map = calculate_structure_map(candles)
        atr_value = calculate_atr(candles)
        support = levels.get("support")
        resistance = levels.get("resistance")
        location = {
            "label": infer_location_label(last_price, support, resistance, atr_value, market_state),
            "distanceToSupport": distance_to_level(last_price, support),
            "distanceToResistance": distance_to_level(last_price, resistance),
        }
        ob_bull = detect_order_blocks(candles, "buy",  atr_value=float(atr_value or 6.0))
        ob_bear = detect_order_blocks(candles, "sell", atr_value=float(atr_value or 6.0))
        fvg_bull = detect_fvg(candles, "buy")
        fvg_bear = detect_fvg(candles, "sell")
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
            "structureMap": structure_map,
            "location": location,
            "structure": classify_structure(candles),
            "volatility": {
                "atr": round(float(atr_value), 2) if atr_value is not None else None,
            },
            # SMC layers — top 3 nearest unmitigated OBs and unfilled FVGs per side.
            # Used by the trade engine for precise entry/TP; available to the UI for charting.
            "orderBlocks": {
                "bullish": ob_bull[:3],
                "bearish": ob_bear[:3],
            },
            "fairValueGaps": {
                "bullish": fvg_bull[:3],
                "bearish": fvg_bear[:3],
            },
        }
    board["timeframes"] = timeframe_data
    return board


def build_timeframe_payload(symbol: str, timeframe: str, candles: list[dict[str, float | int]]) -> dict[str, object]:
    summary = trend_summary(candles)
    levels = calculate_levels(candles)
    market_state = calculate_market_state(candles)
    structure_map = calculate_structure_map(candles)
    atr_value = calculate_atr(candles)
    last_price = latest_close(candles)
    support = levels.get("support")
    resistance = levels.get("resistance")
    location = {
        "label": infer_location_label(last_price, support, resistance, atr_value, market_state),
        "distanceToSupport": distance_to_level(last_price, support),
        "distanceToResistance": distance_to_level(last_price, resistance),
    }
    ob_bull = detect_order_blocks(candles, "buy", atr_value=float(atr_value or 6.0))
    ob_bear = detect_order_blocks(candles, "sell", atr_value=float(atr_value or 6.0))
    fvg_bull = detect_fvg(candles, "buy")
    fvg_bear = detect_fvg(candles, "sell")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
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
        "structureMap": structure_map,
        "location": location,
        "structure": classify_structure(candles),
        "volatility": {
            "atr": round(float(atr_value), 2) if atr_value is not None else None,
        },
        "orderBlocks": {
            "bullish": ob_bull[:3],
            "bearish": ob_bear[:3],
        },
        "fairValueGaps": {
            "bullish": fvg_bull[:3],
            "bearish": fvg_bear[:3],
        },
    }


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
    h1_bias = classify_frame_bias(get_frame(board, "H1"))
    m15_bias = classify_frame_bias(get_frame(board, "M15"))
    if h1_bias == "bullish" and m15_bias == "bullish":
        return "bullish"
    if h1_bias == "bearish" and m15_bias == "bearish":
        return "bearish"
    return "mixed"


def infer_board_phase(board: dict[str, object], bias: str) -> str:
    h1 = get_frame(board, "H1")
    m15 = get_frame(board, "M15")
    h1_state = h1.get("marketState") if isinstance(h1.get("marketState"), dict) else {}
    m15_state = m15.get("marketState") if isinstance(m15.get("marketState"), dict) else {}
    regimes = {
        str(h1_state.get("regime", "") or "").strip(),
        str(m15_state.get("regime", "") or "").strip(),
    }
    if "Range" in regimes and len(regimes) == 1:
        return "range"
    if bias == "bullish" and (
        "Uptrend" in regimes
        or str(h1_state.get("trend", "") or "").strip() == "Bullish"
        or str(m15_state.get("trend", "") or "").strip() == "Bullish"
    ):
        return "uptrend"
    if bias == "bearish" and (
        "Downtrend" in regimes
        or str(h1_state.get("trend", "") or "").strip() == "Bearish"
        or str(m15_state.get("trend", "") or "").strip() == "Bearish"
    ):
        return "downtrend"
    if "Compression" in regimes and len(regimes) == 1:
        return "range"
    return "transition"


def infer_setup_location(board: dict[str, object]) -> str:
    m15 = get_frame(board, "M15")
    structure_map = m15.get("structureMap") if isinstance(m15.get("structureMap"), dict) else {}
    pd_position = str(structure_map.get("rangePosition", "") or "").strip().lower()
    if pd_position == "premium":
        return "resistance"
    if pd_position == "discount":
        return "support"
    location = m15.get("location") if isinstance(m15.get("location"), dict) else {}
    label = str(location.get("label", "") or "").strip().lower()
    if label in {"support", "resistance"}:
        return label
    m15_state = m15.get("marketState") if isinstance(m15.get("marketState"), dict) else {}
    range_position = str(m15_state.get("rangePosition", "") or "").strip().lower()
    if range_position == "upper":
        return "resistance"
    if range_position == "lower":
        return "support"
    return "middle"


def calculate_structure_map(candles: list[dict[str, float | int]], lookback: int = 10) -> dict[str, float | str | None]:
    if not candles:
        return {
            "structureHigh": None,
            "structureLow": None,
            "equilibrium": None,
            "premiumLow": None,
            "premiumHigh": None,
            "discountLow": None,
            "discountHigh": None,
            "buyZoneLow": None,
            "buyZoneHigh": None,
            "sellZoneLow": None,
            "sellZoneHigh": None,
            "structureDirection": 0,
            "rangePosition": "middle",
            "rangeRatio": None,
            "fib382": None,
            "fib618": None,
            "fib786": None,
            "oteLow": None,
            "oteHigh": None,
        }

    window = candles[-min(len(candles), 160):]
    if not window:
        window = candles

    def get_highest_bar(end_index: int, span: int) -> int:
        start = max(0, end_index - span + 1)
        max_idx = start
        max_high = float(window[start]["high"])
        for idx in range(start + 1, end_index + 1):
            value = float(window[idx]["high"])
            if value >= max_high:
                max_high = value
                max_idx = idx
        candidate = max_idx
        loop_max = min(span - 1, end_index - 2)
        for offset in range(loop_max + 1):
            idx = end_index - (offset + 1)
            if idx - 1 < 0 or idx + 1 > end_index:
                continue
            h_prev = float(window[idx - 1]["high"])
            h_curr = float(window[idx]["high"])
            h_next = float(window[idx + 1]["high"])
            if h_curr > h_prev and h_next <= h_curr and idx >= max_idx:
                candidate = idx
        return candidate

    def get_lowest_bar(end_index: int, span: int) -> int:
        start = max(0, end_index - span + 1)
        min_idx = start
        min_low = float(window[start]["low"])
        for idx in range(start + 1, end_index + 1):
            value = float(window[idx]["low"])
            if value <= min_low:
                min_low = value
                min_idx = idx
        candidate = min_idx
        loop_max = min(span - 1, end_index - 2)
        for offset in range(loop_max + 1):
            idx = end_index - (offset + 1)
            if idx - 1 < 0 or idx + 1 > end_index:
                continue
            l_prev = float(window[idx - 1]["low"])
            l_curr = float(window[idx]["low"])
            l_next = float(window[idx + 1]["low"])
            if l_curr < l_prev and l_next >= l_curr and idx >= min_idx:
                candidate = idx
        return candidate

    n = len(window)
    structure_high_start_index = 0
    structure_low_start_index = 0
    structure_high = float(window[0]["high"])
    structure_low = float(window[0]["low"])
    structure_direction = 0

    for idx in range(1, n):
        close_price = float(window[idx]["close"])
        high_broken = (
            close_price > structure_high
            and idx - 1 > structure_high_start_index
            and float(window[idx - 1]["close"] if idx - 1 >= 0 else -10**9) <= structure_high
            and float(window[idx - 2]["close"] if idx - 2 >= 0 else -10**9) <= structure_high
            and float(window[idx - 3]["close"] if idx - 3 >= 0 else -10**9) <= structure_high
        ) or (structure_direction == 1 and close_price > structure_high)

        low_broken = (
            close_price < structure_low
            and idx - 1 > structure_low_start_index
            and float(window[idx - 1]["close"] if idx - 1 >= 0 else 10**9) >= structure_low
            and float(window[idx - 2]["close"] if idx - 2 >= 0 else 10**9) >= structure_low
            and float(window[idx - 3]["close"] if idx - 3 >= 0 else 10**9) >= structure_low
        ) or (structure_direction == 2 and close_price < structure_low)

        if low_broken:
            structure_max_bar = get_highest_bar(idx, lookback)
            structure_direction = 1
            structure_high_start_index = structure_max_bar
            structure_low_start_index = idx
            structure_high = float(window[structure_max_bar]["high"])
            structure_low = float(window[idx]["low"])
            continue

        if high_broken:
            structure_min_bar = get_lowest_bar(idx, lookback)
            structure_direction = 2
            structure_high_start_index = idx
            structure_low_start_index = structure_min_bar
            structure_high = float(window[idx]["high"])
            structure_low = float(window[structure_min_bar]["low"])
            continue

        if structure_direction in {0, 2} and float(window[idx]["high"]) > structure_high:
            structure_high = float(window[idx]["high"])
            structure_high_start_index = idx
        elif structure_direction in {0, 1} and float(window[idx]["low"]) < structure_low:
            structure_low = float(window[idx]["low"])
            structure_low_start_index = idx

    range_size = max(structure_high - structure_low, 0.01)
    equilibrium = round(structure_low + range_size * 0.5, 2)
    fib382 = round(structure_low + range_size * 0.382, 2)
    fib618 = round(structure_low + range_size * 0.618, 2)
    fib705 = round(structure_low + range_size * 0.705, 2)
    fib786 = round(structure_low + range_size * 0.786, 2)
    last_price = float(window[-1]["close"])
    range_ratio = min(max((last_price - structure_low) / range_size, 0.0), 1.0)
    if range_ratio >= 0.62:
        range_position = "premium"
    elif range_ratio <= 0.38:
        range_position = "discount"
    else:
        range_position = "equilibrium"

    # OTE zone — 0.618 to 0.786 retracement of the active impulse leg.
    # Bullish leg (dir=2): buy the pullback from HIGH, so OTE sits near the LOW end.
    # Bearish leg (dir=1): sell the rally from LOW, so OTE sits near the HIGH end.
    if structure_direction == 2:
        ote_low  = round(structure_low + range_size * (1 - 0.786), 2)   # ≈ 0.214 of range
        ote_high = round(structure_low + range_size * (1 - 0.618), 2)   # ≈ 0.382 of range
    elif structure_direction == 1:
        ote_low  = fib618
        ote_high = fib786
    else:
        ote_low  = fib382
        ote_high = fib618

    return {
        "structureHigh": round(structure_high, 2),
        "structureLow": round(structure_low, 2),
        "structureDirection": structure_direction,
        "equilibrium": equilibrium,
        "premiumLow": equilibrium,
        "premiumHigh": round(structure_high, 2),
        "discountLow": round(structure_low, 2),
        "discountHigh": equilibrium,
        "buyZoneLow": fib382,
        "buyZoneHigh": equilibrium,
        "sellZoneLow": equilibrium,
        "sellZoneHigh": fib618 if fib618 >= equilibrium else round(structure_high, 2),
        "sellExtremeHigh": fib705,
        "fib382": fib382,
        "fib618": fib618,
        "fib786": fib786,
        "oteLow": ote_low,
        "oteHigh": ote_high,
        "rangePosition": range_position,
        "rangeRatio": round(range_ratio, 3),
    }


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


def setup_family_limits(setup_type: str) -> dict[str, float]:
    if setup_type in {"smc_buy", "smc_sell"}:
        return {"stop_min": 4.5, "stop_max": 9.0, "tp_max": 30.0}
    return {"stop_min": 5.0, "stop_max": 9.0, "tp_max": 30.0}


def clamp_stop_distance(entry: float, sl: float, side: str, setup_type: str) -> float:
    # Use the structurally-derived SL distance, clamped between stop_min and stop_max.
    # Previously always used stop_max regardless of the calculated sl — that was wrong.
    limits = setup_family_limits(setup_type)
    actual_distance = abs(entry - sl)
    clamped = min(max(actual_distance, limits["stop_min"]), limits["stop_max"])
    return round(entry - clamped, 2) if side == "buy" else round(entry + clamped, 2)


def clamp_target_by_setup(entry: float, raw_target: float | None, side: str, setup_type: str) -> float | None:
    if raw_target is None:
        return None
    limits = setup_family_limits(setup_type)
    distance = min(abs(raw_target - entry), limits["tp_max"])
    return round(entry + distance, 2) if side == "buy" else round(entry - distance, 2)


def smc_structural_stop(
    *,
    side: str,
    setup_type: str,
    entry: float,
    m5_candles: list[dict[str, float | int]],
    buffer: float,
    atr_value: float,
    support_level: float | None,
    resistance_level: float | None,
    ob: dict[str, object] | None = None,
    fvg: dict[str, object] | None = None,
    structure_level: float | None = None,  # M5 structureLow (buy) / structureHigh (sell)
) -> float:
    recent = m5_candles[-6:] if len(m5_candles) >= 6 else m5_candles
    min_distance = max(4.5, atr_value * 0.65)
    if side == "buy":
        # structure_level = M5 structureLow — the swing low the Pine draws as the active low line.
        # SL goes below this level. OB wick_low and recent swing low are fallbacks.
        anchors = [structure_level, support_level]
        if recent:
            anchors.append(min(float(c["low"]) for c in recent))
        if ob is not None:
            anchors.append(float(ob.get("wick_low") or ob.get("low") or entry))
            anchors.append(float(ob.get("low") or entry))
        if fvg is not None:
            anchors.append(float(fvg.get("low") or entry))
        anchor = min([value for value in anchors if value is not None], default=(entry - min_distance))
        # Only cap at resistance when it is *below* entry (breakout scenario — anchor SL near the broken level).
        if setup_type == "smc_buy" and resistance_level is not None and float(resistance_level) < entry:
            anchor = min(anchor, float(resistance_level))
        raw_stop = anchor - buffer
        return round(min(raw_stop, entry - min_distance), 2)
    # structure_level = M5 structureHigh — the swing high the Pine draws as the active high line.
    # SL goes above this level. OB wick_high and recent swing high are fallbacks.
    anchors = [structure_level, resistance_level]
    if recent:
        anchors.append(max(float(c["high"]) for c in recent))
    if ob is not None:
        anchors.append(float(ob.get("wick_high") or ob.get("high") or entry))
        anchors.append(float(ob.get("high") or entry))
    if fvg is not None:
        anchors.append(float(fvg.get("high") or entry))
    anchor = max([value for value in anchors if value is not None], default=(entry + min_distance))
    # Only cap at support when it is *above* entry (breakdown scenario — anchor SL near the broken level).
    if setup_type == "smc_sell" and support_level is not None and float(support_level) > entry:
        anchor = max(anchor, float(support_level))
    raw_stop = anchor + buffer
    return round(max(raw_stop, entry + min_distance), 2)


def choose_smc_targets(
    *,
    entry: float,
    side: str,
    setup_type: str,
    target_candidates: list[float],
    risk_distance: float,
) -> tuple[float | None, float | None]:
    limits = setup_family_limits(setup_type)
    pool = sorted({round(float(v), 2) for v in target_candidates if v is not None})
    # Pick the nearest fib level on the correct side of entry — no RR floor.
    # Using the actual fib price (even if <1:1 RR) is correct because it IS the next structure level.
    structural_tp1 = choose_target(entry, side, pool)
    fallback_tp1 = entry + risk_distance * 1.5 if side == "buy" else entry - risk_distance * 1.5
    raw_tp1 = structural_tp1 if structural_tp1 is not None else fallback_tp1
    # Only clamp to tp_max (no tp_min floor — don't push TP off the real fib price).
    dist1 = abs(raw_tp1 - entry)
    dist1 = min(dist1, limits["tp_max"])
    tp1 = round(entry + dist1, 2) if side == "buy" else round(entry - dist1, 2)
    if side == "buy":
        tp2_raw = next((value for value in pool if value > tp1), None)
        if tp2_raw is None:
            tp2_raw = tp1 + max(risk_distance * 1.8, 4.0)
    else:
        lower_pool = [value for value in pool if value < tp1]
        tp2_raw = lower_pool[-1] if lower_pool else None
        if tp2_raw is None:
            tp2_raw = tp1 - max(risk_distance * 1.8, 4.0)
    tp2 = clamp_target_by_setup(entry, tp2_raw, side, setup_type)
    return tp1, tp2


def build_zone_text(levels: list[float], entry: float, side: str, atr_value: float) -> str:
    unique_levels = sorted({round(float(value), 2) for value in levels if value is not None})
    if not unique_levels:
        return "No clear zone"
    width = max(min(float(atr_value or 0.0) * 0.6, 3.0), 1.2)
    anchor = min(unique_levels, key=lambda value: abs(value - entry))
    if side == "buy":
        return f"{anchor - width:.2f}-{anchor:.2f}"
    return f"{anchor:.2f}-{anchor + width:.2f}"


def recent_fresh_structure_level(
    side: str,
    m5_candles: list[dict[str, float | int]],
    current_price: float,
) -> float | None:
    if len(m5_candles) < 5:
        return None
    recent = m5_candles[-10:]
    swing_candidates: list[float] = []
    for idx in range(1, len(recent) - 1):
        prev_candle = recent[idx - 1]
        candle = recent[idx]
        next_candle = recent[idx + 1]
        low = float(candle["low"])
        high = float(candle["high"])
        if side == "buy":
            if low <= float(prev_candle["low"]) and low <= float(next_candle["low"]) and low <= current_price:
                swing_candidates.append(low)
        else:
            if high >= float(prev_candle["high"]) and high >= float(next_candle["high"]) and high >= current_price:
                swing_candidates.append(high)
    if swing_candidates:
        return round(max(swing_candidates), 2) if side == "buy" else round(min(swing_candidates), 2)
    if side == "buy":
        below = [float(c["low"]) for c in recent[-6:] if float(c["low"]) <= current_price]
        return round(max(below), 2) if below else None
    above = [float(c["high"]) for c in recent[-6:] if float(c["high"]) >= current_price]
    return round(min(above), 2) if above else None


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
    atr = float(atr_value or 0.0)
    bounds = parse_zone_bounds(zone_text)

    if bounds is None:
        # Breakout / breakdown zones are expressed as "above X" or "below X" text.
        # Previously this auto-passed with no check, which allowed entries 20+ pts
        # beyond the broken level. Now we parse and cap the distance.
        text = str(zone_text or "").strip().lower()
        breakout_cap = max(atr * 1.0, 8.0)  # max acceptable distance past the level
        if text.startswith("above "):
            try:
                level = float(text.replace("above", "", 1).strip())
                return level <= price <= level + breakout_cap
            except ValueError:
                pass
        if text.startswith("below "):
            try:
                level = float(text.replace("below", "", 1).strip())
                return level - breakout_cap <= price <= level
            except ValueError:
                pass
        # Retest / unrecognised zones: fall back to permissive pass
        return True

    low, high = bounds
    # Pullback/range setups (entering at a structural level) use a tighter tolerance —
    # entry should be close to the level, not drifting away from it.
    # Breakout/failed-break setups use a slightly wider tolerance to account for
    # impulse spread, but still capped to prevent chasing.
    if location_label in {"breakout_zone", "breakdown_zone"}:
        tolerance = max(min(atr * 0.4, 2.8), 0.8)
    else:
        tolerance = max(min(atr * 0.18, 1.2), 0.45)

    if low - tolerance <= price <= high + tolerance:
        return True
    return False


def entry_matches_setup_zone(
    *,
    entry: float,
    side: str,
    setup_type: str,
    zone_text: str,
    atr_value: float,
) -> bool:
    atr = float(atr_value or 0.0)
    # Breakout/breakdown setups need wider tolerance (impulse spread) but must be capped
    # to prevent chasing a move that has already run too far from the level.
    # Pullback/range/failed-break setups use tight tolerance — entry must be near the level.
    is_breakout = setup_type in {"breakout_buy", "breakdown_sell"}
    if is_breakout:
        tolerance = max(min(atr * 0.4, 2.8), 0.8)
        breakout_cap = max(atr * 1.0, 8.0)
    else:
        tolerance = max(min(atr * 0.18, 1.2), 0.45)
        breakout_cap = None  # not used for non-breakout setups

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
        # Must be above the level (minus tolerance for fills) AND not too stretched past it.
        cap = breakout_cap if breakout_cap is not None else max(atr * 0.5, 4.0)
        return level - tolerance <= entry <= level + cap
    if text.startswith("below "):
        try:
            level = float(text.replace("below", "", 1).strip())
        except ValueError:
            return True
        cap = breakout_cap if breakout_cap is not None else max(atr * 0.5, 4.0)
        return level - cap <= entry <= level + tolerance
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
    # M5 is the hard gate: it must show a directional close (green body + higher than prev close
    # for buys; red body + lower than prev close for sells).
    # M1 is a soft tiebreaker — checked only when there is sufficient M1 data. Individual
    # pattern detectors already embed their own M1 checks where relevant, so requiring M1
    # to pass here too was causing ~34% of valid setups to be blocked by a single noisy
    # 1-minute candle. M1 now only hard-blocks when it is *actively opposing* (not merely neutral).
    if len(m5_candles) < 2:
        return False
    m5_last = m5_candles[-1]
    m5_prev = m5_candles[-2]
    if side == "buy":
        m5_confirm = float(m5_last["close"]) >= float(m5_last["open"]) and float(m5_last["close"]) >= float(m5_prev["close"])
        if not m5_confirm:
            return False
        # M1 override: if M1 has enough data and is clearly opposing (red body + lower close),
        # hold back. Otherwise pass — M1 noise alone should not veto a confirmed M5 setup.
        if len(m1_candles) >= 3:
            m1_last = m1_candles[-1]
            m1_prev = m1_candles[-2]
            m1_opposing = float(m1_last["close"]) < float(m1_last["open"]) and float(m1_last["close"]) < float(m1_prev["close"])
            if m1_opposing:
                return False
        return True
    m5_confirm = float(m5_last["close"]) <= float(m5_last["open"]) and float(m5_last["close"]) <= float(m5_prev["close"])
    if not m5_confirm:
        return False
    if len(m1_candles) >= 3:
        m1_last = m1_candles[-1]
        m1_prev = m1_candles[-2]
        m1_opposing = float(m1_last["close"]) > float(m1_last["open"]) and float(m1_last["close"]) > float(m1_prev["close"])
        if m1_opposing:
            return False
    return True


def recent_candles(frame: dict[str, object], count: int) -> list[dict[str, float | int]]:
    candles = frame.get("candles") if isinstance(frame.get("candles"), list) else []
    return candles[-min(count, len(candles)):] if candles else []


def safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def market_is_closed(board: dict[str, object]) -> bool:
    market = board.get("market") if isinstance(board.get("market"), dict) else {}
    tick_time = int(market.get("tick_time") or 0)
    if tick_time <= 0:
        return True
    tick_dt = datetime.fromtimestamp(tick_time, timezone.utc)
    now_utc = datetime.now(timezone.utc)
    stale_seconds = (now_utc - tick_dt).total_seconds()
    return stale_seconds > 10 * 60


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
        # 4.5 pt tolerance (~0.6x min ATR on XAUUSD). A pullback wick can land a few pts
        # above support and still be a textbook setup; 3.0 was too tight.
        near_support = support is not None and min(float(last["low"]), float(prev["low"])) <= support + 4.5
        turn = float(last["close"]) >= float(last["open"]) and float(last["close"]) >= float(prev["close"]) and float(m1_last["close"]) >= float(m1_prev["close"])
        return {"passed": near_support and turn, "detail": "Support held and M5/M1 turned up" if near_support and turn else "No clean bullish continuation"}
    near_resistance = resistance is not None and max(float(last["high"]), float(prev["high"])) >= resistance - 4.5
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


def detect_strong_breakout_impulse(
    side: str,
    level: float | None,
    m5_candles: list[dict[str, float | int]],
    m1_candles: list[dict[str, float | int]],
) -> dict[str, object]:
    if level is None or len(m5_candles) < 5 or len(m1_candles) < 2:
        return {"passed": False, "detail": "Need clearer breakout impulse"}
    compression = m5_candles[-5:-2]
    prev = m5_candles[-2]
    last = m5_candles[-1]
    m1_last = m1_candles[-1]
    avg_range = sum(abs(float(c["high"]) - float(c["low"])) for c in compression) / len(compression)
    prev_range = abs(float(prev["high"]) - float(prev["low"]))
    last_range = abs(float(last["high"]) - float(last["low"]))
    if avg_range <= 0:
        return {"passed": False, "detail": "Need clearer breakout impulse"}
    if side == "buy":
        broke = float(prev["close"]) > level and float(last["close"]) > level
        # 1.6x (was 1.9x): real breakouts with solid follow-through don't need to be
        # nearly 2x the compression range — 1.6x filters noise while catching clean moves.
        expansion = max(prev_range, last_range) >= avg_range * 1.6
        strong_close = (
            float(prev["close"]) >= float(prev["high"]) - max(prev_range * 0.35, 1.0)
            and float(last["close"]) >= float(last["high"]) - max(last_range * 0.35, 1.0)
        )
        follow_through = float(last["close"]) >= float(prev["close"]) - 1.2
        # not_too_stretched raised to 10 pts / 1.5x ATR (was 6 pts / 1.1x): on XAUUSD at
        # ~$4800 a real breakout easily travels 8-10 pts before you can react.
        not_too_stretched = (float(last["close"]) - level) <= max(avg_range * 1.5, 10.0)
        shallow_retrace = float(last["low"]) >= level - max(avg_range * 0.25, 1.0)
        m1_confirm = float(m1_last["close"]) >= float(m1_last["open"]) and float(m1_last["close"]) >= level
        passed = broke and expansion and strong_close and follow_through and not_too_stretched and shallow_retrace and m1_confirm
        return {"passed": passed, "detail": f"Strong impulse breakout above {level:.2f}" if passed else f"No strong bullish impulse above {level:.2f}"}
    broke = float(prev["close"]) < level and float(last["close"]) < level
    expansion = max(prev_range, last_range) >= avg_range * 1.6
    strong_close = (
        float(prev["close"]) <= float(prev["low"]) + max(prev_range * 0.35, 1.0)
        and float(last["close"]) <= float(last["low"]) + max(last_range * 0.35, 1.0)
    )
    follow_through = float(last["close"]) <= float(prev["close"]) + 1.2
    not_too_stretched = (level - float(last["close"])) <= max(avg_range * 1.5, 10.0)
    shallow_retrace = float(last["high"]) <= level + max(avg_range * 0.25, 1.0)
    m1_confirm = float(m1_last["close"]) <= float(m1_last["open"]) and float(m1_last["close"]) <= level
    passed = broke and expansion and strong_close and follow_through and not_too_stretched and shallow_retrace and m1_confirm
    return {"passed": passed, "detail": f"Strong impulse breakdown below {level:.2f}" if passed else f"No strong bearish impulse below {level:.2f}"}


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


# ---------------------------------------------------------------------------
# SMC — Order Block (OB) and Fair Value Gap (FVG) detection
# ---------------------------------------------------------------------------

def detect_order_blocks(
    candles: list[dict[str, float | int]],
    side: str,
    *,
    lookback: int = 80,
    atr_value: float = 6.0,
) -> list[dict[str, object]]:
    """Return unmitigated order blocks, newest first.

    Bullish OB — last bearish candle (close < open) immediately before a
    strong upward impulse (≥ 1.5x ATR).  It marks the origin of institutional
    buy orders.  The OB is mitigated once price closes below its body midpoint.

    Bearish OB — last bullish candle before a strong downward impulse.
    Mitigated when price closes above its body midpoint.
    """
    if len(candles) < 5:
        return []
    window = candles[-min(lookback, len(candles)):]
    impulse_min = max(atr_value * 1.5, 4.0)
    obs: list[dict[str, object]] = []

    for i in range(1, len(window) - 2):
        candle = window[i]
        c_open = float(candle["open"])
        c_close = float(candle["close"])
        c_high = float(candle["high"])
        c_low = float(candle["low"])
        # Look at the next 1-3 bars for the impulse that created this OB.
        impulse_window = window[i + 1: i + 4]
        if not impulse_window:
            continue

        if side == "buy":
            if c_close >= c_open:          # must be a bearish candle
                continue
            impulse = any(
                float(c["close"]) - float(c["open"]) >= impulse_min
                or float(c["high"]) > c_high + impulse_min * 0.5
                for c in impulse_window
            )
            if not impulse:
                continue
            body_mid = (c_open + c_close) / 2
            subsequent = window[i + 1:]
            mitigated = any(float(c["close"]) < body_mid for c in subsequent)
            obs.append({
                "high": round(max(c_open, c_close), 2),   # OB body top
                "low": round(min(c_open, c_close), 2),    # OB body bottom
                "wick_high": round(c_high, 2),
                "wick_low": round(c_low, 2),
                "midpoint": round(body_mid, 2),
                "time": int(candle.get("time") or 0),
                "mitigated": mitigated,
            })
        else:
            if c_close <= c_open:          # must be a bullish candle
                continue
            impulse = any(
                float(c["open"]) - float(c["close"]) >= impulse_min
                or float(c["low"]) < c_low - impulse_min * 0.5
                for c in impulse_window
            )
            if not impulse:
                continue
            body_mid = (c_open + c_close) / 2
            subsequent = window[i + 1:]
            mitigated = any(float(c["close"]) > body_mid for c in subsequent)
            obs.append({
                "high": round(max(c_open, c_close), 2),
                "low": round(min(c_open, c_close), 2),
                "wick_high": round(c_high, 2),
                "wick_low": round(c_low, 2),
                "midpoint": round(body_mid, 2),
                "time": int(candle.get("time") or 0),
                "mitigated": mitigated,
            })

    # Newest first, unmitigated only
    return [ob for ob in reversed(obs) if not ob["mitigated"]]


def detect_fvg(
    candles: list[dict[str, float | int]],
    side: str,
    *,
    lookback: int = 80,
    min_gap: float = 0.5,
) -> list[dict[str, object]]:
    """Return unfilled fair value gaps (imbalances), newest first.

    Bullish FVG — candle[i-1].high < candle[i+1].low: upward gap between the
    prior bar's high and the next bar's low, caused by a strong impulse candle.
    Price tends to retrace into this zone before continuing.

    Bearish FVG — candle[i-1].low > candle[i+1].high: the mirror image.

    A FVG is considered filled once price trades through its midpoint.
    """
    if len(candles) < 3:
        return []
    window = candles[-min(lookback, len(candles)):]
    fvgs: list[dict[str, object]] = []

    for i in range(1, len(window) - 1):
        prev = window[i - 1]
        candle = window[i]   # the impulse candle that created the gap
        nxt = window[i + 1]

        if side == "buy":
            gap_low = float(prev["high"])
            gap_high = float(nxt["low"])
            if gap_high - gap_low < min_gap:
                continue
            gap_mid = (gap_low + gap_high) / 2
            subsequent = window[i + 2:]
            # Filled when any bar closes into the lower half of the gap (close, not wick)
            filled = any(float(c["close"]) <= gap_mid for c in subsequent)
            fvgs.append({
                "high": round(gap_high, 2),
                "low": round(gap_low, 2),
                "midpoint": round(gap_mid, 2),
                "size": round(gap_high - gap_low, 2),
                "time": int(candle.get("time") or 0),
                "filled": filled,
            })
        else:
            gap_high = float(prev["low"])
            gap_low = float(nxt["high"])
            if gap_high - gap_low < min_gap:
                continue
            gap_mid = (gap_low + gap_high) / 2
            subsequent = window[i + 2:]
            filled = any(float(c["close"]) >= gap_mid for c in subsequent)
            fvgs.append({
                "high": round(gap_high, 2),
                "low": round(gap_low, 2),
                "midpoint": round(gap_mid, 2),
                "size": round(gap_high - gap_low, 2),
                "time": int(candle.get("time") or 0),
                "filled": filled,
            })

    return [fvg for fvg in reversed(fvgs) if not fvg["filled"]]


def detect_ob_entry(
    side: str,
    ob: dict[str, object] | None,
    m5_candles: list[dict[str, float | int]],
    m1_candles: list[dict[str, float | int]],
) -> dict[str, object]:
    """Price has returned into an unmitigated OB and is showing a reaction candle."""
    if ob is None or len(m5_candles) < 2:
        return {"passed": False, "detail": "No valid order block"}
    last = m5_candles[-1]
    c_low = float(last["low"])
    c_high = float(last["high"])
    c_close = float(last["close"])
    c_open = float(last["open"])
    ob_low = float(ob["low"])
    ob_high = float(ob["high"])
    if side == "buy":
        # Wick or body touched the OB zone and close is bullish
        touched = c_low <= ob_high and c_close >= ob_low
        reacting = c_close > c_open
        # M1 confirmation — must not be actively opposing
        if len(m1_candles) >= 2:
            m1 = m1_candles[-1]
            m1_opposing = float(m1["close"]) < float(m1["open"]) and float(m1["close"]) < float(m1_candles[-2]["close"])
            if m1_opposing:
                reacting = False
        passed = touched and reacting
        detail = (
            f"Bullish OB {ob_low:.2f}-{ob_high:.2f}: price reacting up"
            if passed else
            f"No reaction in OB zone {ob_low:.2f}-{ob_high:.2f}"
        )
    else:
        touched = c_high >= ob_low and c_close <= ob_high
        reacting = c_close < c_open
        if len(m1_candles) >= 2:
            m1 = m1_candles[-1]
            m1_opposing = float(m1["close"]) > float(m1["open"]) and float(m1["close"]) > float(m1_candles[-2]["close"])
            if m1_opposing:
                reacting = False
        passed = touched and reacting
        detail = (
            f"Bearish OB {ob_low:.2f}-{ob_high:.2f}: price reacting down"
            if passed else
            f"No reaction in OB zone {ob_low:.2f}-{ob_high:.2f}"
        )
    return {"passed": passed, "detail": detail}


def detect_fvg_entry(
    side: str,
    fvg: dict[str, object] | None,
    m5_candles: list[dict[str, float | int]],
    m1_candles: list[dict[str, float | int]] | None = None,
) -> dict[str, object]:
    """Price has retraced into an unfilled FVG and shows a directional reaction."""
    if fvg is None or len(m5_candles) < 2:
        return {"passed": False, "detail": "No valid FVG"}
    last = m5_candles[-1]
    c_close = float(last["close"])
    c_open = float(last["open"])
    c_low = float(last["low"])
    c_high = float(last["high"])
    fvg_low = float(fvg["low"])
    fvg_high = float(fvg["high"])
    if side == "buy":
        # Price pulled back into the bullish FVG from above and is bouncing
        in_fvg = (fvg_low <= c_close <= fvg_high) or (fvg_low <= c_low <= fvg_high)
        reacting = c_close > c_open
        # M1 confirmation — must not be actively opposing (same gate as detect_ob_entry)
        if reacting and m1_candles and len(m1_candles) >= 2:
            m1 = m1_candles[-1]
            if float(m1["close"]) < float(m1["open"]) and float(m1["close"]) < float(m1_candles[-2]["close"]):
                reacting = False
        passed = in_fvg and reacting
        detail = (
            f"Bullish FVG retest {fvg_low:.2f}-{fvg_high:.2f}: bouncing"
            if passed else
            f"Not in FVG {fvg_low:.2f}-{fvg_high:.2f}"
        )
    else:
        in_fvg = (fvg_low <= c_close <= fvg_high) or (fvg_low <= c_high <= fvg_high)
        reacting = c_close < c_open
        # M1 confirmation — must not be actively opposing
        if reacting and m1_candles and len(m1_candles) >= 2:
            m1 = m1_candles[-1]
            if float(m1["close"]) > float(m1["open"]) and float(m1["close"]) > float(m1_candles[-2]["close"]):
                reacting = False
        passed = in_fvg and reacting
        detail = (
            f"Bearish FVG fill {fvg_low:.2f}-{fvg_high:.2f}: dropping"
            if passed else
            f"Not in FVG {fvg_low:.2f}-{fvg_high:.2f}"
        )
    return {"passed": passed, "detail": detail}


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
    wait_for: list[str] | None = None,
    smc_parameters: dict[str, object] | None = None,
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
        "setup": ("BUY SMC EXECUTION" if setup_type == "smc_buy" else
                  "SELL SMC EXECUTION" if setup_type == "smc_sell" else
                  "WAIT FOR CLEANER STRUCTURE"),
        "zone": zone_text,
        "wait_for": [] if active else list(wait_for[:4]) if isinstance(wait_for, list) and wait_for else ["Cleaner M15 structure", "Better M15 dealing location", "Cleaner M5/M1 trigger"],
        "entry_note": "Entry is valid now." if active else "No clean trade right now.",
        "tp_plan": ([f"TP1: {tp1:.2f}"] if tp1 is not None else []) + ([f"TP2: {tp2:.2f}"] if tp2 is not None else []),
        "plan": execution_plan,
        "trigger": trigger_text,
        "room": f"Target near {tp1:.2f}" if tp1 is not None else "",
        "context_summary": f"H1/M15 context: phase {market_phase}; bias {bias}; location {location}",
        "trigger_summary": trigger_text,
        "execution_summary": execution_plan,
        "analysis": reason if active else f"Decision: NO TRADE. {reason}",
        "model": model,
        "smc_parameters": smc_parameters if isinstance(smc_parameters, dict) else {},
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
    if market_is_closed(board):
        return build_trade_payload(
            board=board,
            side="buy",
            market_phase="closed",
            bias="inactive",
            setup_type="none",
            location="closed",
            zone_text="Market closed",
            reason="Market is closed, so no live trade zone or trigger can form right now.",
            why=["Latest market tick is stale", "Market appears inactive / closed"],
            conflicts=["Market is closed"],
            trigger_text="Market closed",
            execution_plan="Wait for the market to reopen and print fresh ticks before evaluating setups.",
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            model=CURRENT_STRATEGY_MODEL,
            pattern_candidates=[],
            entry_checks={"market_open": False},
            smc_parameters={},
        )
    bias = infer_board_bias(board)
    market_phase = infer_board_phase(board, bias)
    location = infer_setup_location(board)
    ltf_tone = infer_ltf_tone(board)

    h1 = get_frame(board, "H1")
    m15 = get_frame(board, "M15")
    m5 = get_frame(board, "M5")
    h1_levels = h1.get("levels") if isinstance(h1.get("levels"), dict) else {}
    m15_levels = m15.get("levels") if isinstance(m15.get("levels"), dict) else {}
    m5_levels = m5.get("levels") if isinstance(m5.get("levels"), dict) else {}
    h1_structure  = h1.get("structureMap")  if isinstance(h1.get("structureMap"),  dict) else {}
    m15_structure = m15.get("structureMap") if isinstance(m15.get("structureMap"), dict) else {}
    m5_atr = ((m5.get("volatility") if isinstance(m5.get("volatility"), dict) else {}) or {}).get("atr")
    atr_value = float(m5_atr) if m5_atr is not None else (calculate_atr(m5.get("candles") if isinstance(m5.get("candles"), list) else []) or 6.0)
    buffer = max(atr_value * 0.25, 0.8)

    # --- SMC: Order Block and FVG detection on M5 + M15 candles ---
    # Use a longer lookback window (80 bars) to find structural OBs and FVGs.
    # M15 first (watch zone / structural setup), then M5 (entry confirmation precision).
    m5_all_candles = m5.get("candles") if isinstance(m5.get("candles"), list) else []
    m15_all_candles = m15.get("candles") if isinstance(m15.get("candles"), list) else []
    m5_bull_obs  = detect_order_blocks(m5_all_candles,  "buy",  atr_value=atr_value)
    m5_bear_obs  = detect_order_blocks(m5_all_candles,  "sell", atr_value=atr_value)
    m15_bull_obs = detect_order_blocks(m15_all_candles, "buy",  atr_value=atr_value)
    m15_bear_obs = detect_order_blocks(m15_all_candles, "sell", atr_value=atr_value)
    # Nearest unmitigated bullish OB below price — M15 watch zone takes priority over M5
    nearest_bull_ob = next(
        (ob for ob in m15_bull_obs + m5_bull_obs if float(ob["high"]) < current_price),
        None,
    )
    # Nearest unmitigated bearish OB above price — M15 watch zone takes priority over M5
    nearest_bear_ob = next(
        (ob for ob in m15_bear_obs + m5_bear_obs if float(ob["low"]) > current_price),
        None,
    )
    m5_bull_fvgs  = detect_fvg(m5_all_candles,  "buy")
    m5_bear_fvgs  = detect_fvg(m5_all_candles,  "sell")
    m15_bull_fvgs = detect_fvg(m15_all_candles, "buy")
    m15_bear_fvgs = detect_fvg(m15_all_candles, "sell")
    # Nearest unfilled bullish FVG below price — M15 watch zone takes priority over M5
    nearest_bull_fvg = next(
        (fvg for fvg in m15_bull_fvgs + m5_bull_fvgs if float(fvg["high"]) < current_price),
        None,
    )
    # Nearest unfilled bearish FVG above price — M15 watch zone takes priority over M5
    nearest_bear_fvg = next(
        (fvg for fvg in m15_bear_fvgs + m5_bear_fvgs if float(fvg["low"]) > current_price),
        None,
    )
    # ----------------------------------------------------------------

    # H4 is included in the target pool so TP can reach structural H4 levels.
    # H4 is intentionally excluded from near_*_candidates so the entry zone stays M5/M15-precise.
    # OB and FVG levels are also injected here:
    #   — nearest_bull_ob.high / nearest_bull_fvg.midpoint → near-entry support candidates
    #   — nearest_bear_ob.low / nearest_bear_fvg.midpoint  → resistance/TP candidates
    support_candidates = [m5_levels.get("support"), m15_levels.get("support"), h1_levels.get("support"), m15_structure.get("structureLow"), h1_structure.get("structureLow")]
    resistance_candidates = [m5_levels.get("resistance"), m15_levels.get("resistance"), h1_levels.get("resistance"), m15_structure.get("structureHigh"), h1_structure.get("structureHigh")]
    # Add OB midpoints and FVG midpoints to TP pool
    if nearest_bear_ob:
        resistance_candidates.append(nearest_bear_ob["midpoint"])
    if nearest_bear_fvg:
        resistance_candidates.append(nearest_bear_fvg["midpoint"])
    if nearest_bull_ob:
        support_candidates.append(nearest_bull_ob["midpoint"])
    if nearest_bull_fvg:
        support_candidates.append(nearest_bull_fvg["midpoint"])
    fresh_m5_support = recent_fresh_structure_level("buy", m5_all_candles, current_price)
    fresh_m5_resistance = recent_fresh_structure_level("sell", m5_all_candles, current_price)
    # OB high (body top) is a precise near-entry support level for buys
    ob_near_support = float(nearest_bull_ob["high"]) if nearest_bull_ob else None
    ob_near_resistance = float(nearest_bear_ob["low"]) if nearest_bear_ob else None
    near_support_candidates = [ob_near_support, fresh_m5_support, m5_levels.get("support"), m15_levels.get("support"), h1_levels.get("support"), m15_structure.get("buyZoneLow"), m15_structure.get("buyZoneHigh")]
    near_resistance_candidates = [ob_near_resistance, fresh_m5_resistance, m5_levels.get("resistance"), m15_levels.get("resistance"), h1_levels.get("resistance"), m15_structure.get("sellZoneLow"), m15_structure.get("sellZoneHigh")]
    support_zone = [float(value) for value in support_candidates if value is not None]
    resistance_zone = [float(value) for value in resistance_candidates if value is not None]
    near_support_zone = [float(value) for value in near_support_candidates if value is not None]
    near_resistance_zone = [float(value) for value in near_resistance_candidates if value is not None]
    # Watch zone = OTE zone (where the entry gate actually fires), not the broader fib382–equilibrium band.
    # OTE is directional: direction=2 → discount OTE is the buy zone; direction=1 → premium OTE is the sell zone.
    m15_struct_dir = int(m15_structure.get("structureDirection") or 0)
    ote_low_val  = m15_structure.get("oteLow")
    ote_high_val = m15_structure.get("oteHigh")
    if m15_struct_dir == 2 and ote_low_val is not None and ote_high_val is not None:
        pullback_buy_zone = f"{float(ote_low_val):.2f}-{float(ote_high_val):.2f}"
    else:
        pullback_buy_zone = build_zone_text(near_support_zone, current_price, "buy", atr_value) if near_support_zone else (build_zone_text(support_zone, current_price, "buy", atr_value) if support_zone else "No clear support zone")
    if m15_struct_dir == 1 and ote_low_val is not None and ote_high_val is not None:
        rally_sell_zone = f"{float(ote_low_val):.2f}-{float(ote_high_val):.2f}"
    else:
        rally_sell_zone = build_zone_text(near_resistance_zone, current_price, "sell", atr_value) if near_resistance_zone else (build_zone_text(resistance_zone, current_price, "sell", atr_value) if resistance_zone else "No clear resistance zone")

    pd_position = str(m15_structure.get("rangePosition", "middle") or "middle").strip().lower()
    smc_parameters = {
        "h1Bias": bias,
        "m15Phase": market_phase,
        "m15StructureHigh": m15_structure.get("structureHigh"),
        "m15StructureLow": m15_structure.get("structureLow"),
        "m15Equilibrium": m15_structure.get("equilibrium"),
        "m15PdPosition": pd_position,
        "m15DiscountZone": f"{float(m15_structure['discountLow']):.2f}-{float(m15_structure['discountHigh']):.2f}" if m15_structure.get("discountLow") is not None and m15_structure.get("discountHigh") is not None else "--",
        "m15PremiumZone": f"{float(m15_structure['premiumLow']):.2f}-{float(m15_structure['premiumHigh']):.2f}" if m15_structure.get("premiumLow") is not None and m15_structure.get("premiumHigh") is not None else "--",
        "buyExecutionZone": pullback_buy_zone,
        "sellExecutionZone": rally_sell_zone,
        "activePrice": round(current_price, 2),
        "ltfTone": ltf_tone,
    }
    # Dedicated OB and FVG zone texts (exact body bounds, no ATR-width approximation)
    ob_buy_zone   = f"{nearest_bull_ob['low']:.2f}-{nearest_bull_ob['high']:.2f}"  if nearest_bull_ob  else pullback_buy_zone
    ob_sell_zone  = f"{nearest_bear_ob['low']:.2f}-{nearest_bear_ob['high']:.2f}"  if nearest_bear_ob  else rally_sell_zone
    fvg_buy_zone  = f"{nearest_bull_fvg['low']:.2f}-{nearest_bull_fvg['high']:.2f}" if nearest_bull_fvg else pullback_buy_zone
    fvg_sell_zone = f"{nearest_bear_fvg['low']:.2f}-{nearest_bear_fvg['high']:.2f}" if nearest_bear_fvg else rally_sell_zone

    why: list[str] = []
    conflicts: list[str] = []
    location_note = ""

    if bias == "bullish":
        why.append("H1 context is bullish and M15 confirms the structure")
    elif bias == "bearish":
        why.append("H1 context is bearish and M15 confirms the structure")
    else:
        conflicts.append("H1 context and M15 structure are not aligned")

    if location == "resistance":
        location_note = "Price is pressing into upper structure / resistance"
    elif location == "support":
        location_note = "Price is near support / pullback value"
    else:
        location_note = "Price is in the middle of structure"

    if ltf_tone == "bullish":
        why.append("M5/M1 is stabilizing after the pullback")
    elif ltf_tone == "bearish":
        why.append("M5/M1 is leaning lower short term")
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
        location_label="support" if str(m15_structure.get("rangePosition", "") or "").lower() == "discount" else "middle",
    )
    sell_zone_ok = price_within_entry_tolerance(
        price=current_price,
        side="sell",
        zone_text=rally_sell_zone,
        atr_value=atr_value,
        location_label="resistance" if str(m15_structure.get("rangePosition", "") or "").lower() == "premium" else "middle",
    )
    buy_confirmation_ok = has_entry_confirmation(side="buy", m5_candles=m5_candles, m1_candles=m1_candles)
    sell_confirmation_ok = has_entry_confirmation(side="sell", m5_candles=m5_candles, m1_candles=m1_candles)

    signal_reviews: list[dict[str, object]] = []

    def evaluate_smc_signal(
        side: str,
        signal_name: str,
        detector: dict[str, object],
        reason_text: str,
        zone_text: str,
        location_label: str,
    ) -> dict[str, object] | None:
        passed = bool(detector.get("passed"))
        trigger_detail = str(detector.get("detail", "") or "Pattern confirmed")
        zone_ok = passed and price_within_entry_tolerance(
            price=current_price,
            side=side,
            zone_text=zone_text,
            atr_value=atr_value,
            location_label=location_label,
        )
        confirmation_ok = passed and has_entry_confirmation(side=side, m5_candles=m5_candles, m1_candles=m1_candles)
        review = {
            "setup_type": "smc_buy" if side == "buy" else "smc_sell",
            "side": side,
            "signal": signal_name,
            "passed": passed,
            "zone_ok": zone_ok,
            "confirmation_ok": confirmation_ok,
            "zone": zone_text,
            "trigger": trigger_detail,
            "rr": 0.0,
        }
        # OTE gate — OB/FVG retests must sit inside the M5 OTE zone (0.618–0.786 retracement).
        # Sweeps, breakouts, and BOS retests skip this check — they have their own structural context.
        ote_signals = {"bullish_ob_retest", "bullish_fvg_retest", "bearish_ob_retest", "bearish_fvg_retest"}
        if passed and signal_name in ote_signals:
            ote_low  = m15_structure.get("oteLow")
            ote_high = m15_structure.get("oteHigh")
            if ote_low is not None and ote_high is not None:
                in_ote = float(ote_low) <= current_price <= float(ote_high)
                review["ote_ok"] = in_ote
                if not in_ote:
                    signal_reviews.append(review)
                    return None
            else:
                review["ote_ok"] = True
        else:
            review["ote_ok"] = True

        signal_reviews.append(review)
        if not passed or not zone_ok or not confirmation_ok:
            return None

        entry = round(current_price, 2)
        matched_ob  = None
        matched_fvg = None
        # OTE edges — SL sits just outside the OTE zone (below oteLow for buys, above oteHigh for sells).
        # If price breaks back through the 0.786 retracement the OTE setup is void.
        m5_sl_anchor_buy  = m15_structure.get("oteLow")
        m5_sl_anchor_sell = m15_structure.get("oteHigh")
        if side == "buy":
            if zone_text == ob_buy_zone and nearest_bull_ob:
                matched_ob = nearest_bull_ob
            if zone_text == fvg_buy_zone and nearest_bull_fvg:
                matched_fvg = nearest_bull_fvg
            sl = smc_structural_stop(
                side="buy",
                setup_type="smc_buy",
                entry=entry,
                m5_candles=m5_candles,
                buffer=buffer,
                atr_value=atr_value,
                support_level=nearest_support,
                resistance_level=nearest_resistance,
                ob=matched_ob,
                fvg=matched_fvg,
                structure_level=float(m5_sl_anchor_buy) if m5_sl_anchor_buy is not None else None,
            )
            sl = clamp_stop_distance(entry, sl, "buy", "smc_buy")
            # TP: nearest M15 fib level above entry — trades only to the next zone, not the full structure top.
            fib_ladder_buy = [
                m15_structure.get("fib382"),        # 38.2% of range
                m15_structure.get("equilibrium"),   # 50%
                m15_structure.get("fib618"),        # 61.8%
                m15_structure.get("sellExtremeHigh"), # 70.5%
                m15_structure.get("fib786"),        # 78.6%
                m15_structure.get("structureHigh"), # 100%
            ]
            tp_pool = [float(v) for v in fib_ladder_buy if v is not None]
            tp1, tp2 = choose_smc_targets(
                entry=entry,
                side="buy",
                setup_type="smc_buy",
                target_candidates=tp_pool,
                risk_distance=abs(entry - sl),
            )
        else:
            if zone_text == ob_sell_zone and nearest_bear_ob:
                matched_ob = nearest_bear_ob
            if zone_text == fvg_sell_zone and nearest_bear_fvg:
                matched_fvg = nearest_bear_fvg
            sl = smc_structural_stop(
                side="sell",
                setup_type="smc_sell",
                entry=entry,
                m5_candles=m5_candles,
                buffer=buffer,
                atr_value=atr_value,
                support_level=nearest_support,
                resistance_level=nearest_resistance,
                ob=matched_ob,
                fvg=matched_fvg,
                structure_level=float(m5_sl_anchor_sell) if m5_sl_anchor_sell is not None else None,
            )
            sl = clamp_stop_distance(entry, sl, "sell", "smc_sell")
            # TP: nearest M15 fib level below entry — trades only to the next zone, not the full structure bottom.
            fib_ladder_sell = [
                m15_structure.get("fib786"),        # 78.6%
                m15_structure.get("sellExtremeHigh"), # 70.5%
                m15_structure.get("fib618"),        # 61.8%
                m15_structure.get("equilibrium"),   # 50%
                m15_structure.get("fib382"),        # 38.2%
                m15_structure.get("structureLow"),  # 0%
            ]
            tp_pool = [float(v) for v in fib_ladder_sell if v is not None]
            tp1, tp2 = choose_smc_targets(
                entry=entry,
                side="sell",
                setup_type="smc_sell",
                target_candidates=tp_pool,
                risk_distance=abs(entry - sl),
            )

        if not entry_matches_setup_zone(entry=entry, side=side, setup_type=("smc_buy" if side == "buy" else "smc_sell"), zone_text=zone_text, atr_value=atr_value):
            review["entry_ok"] = False
            return None

        review["entry_ok"] = True
        rr = round(abs(tp1 - entry) / abs(entry - sl), 2) if tp1 is not None and entry != sl else 0.0
        review["rr"] = rr
        return {
            "side": side,
            "setup_type": "smc_buy" if side == "buy" else "smc_sell",
            "signal": signal_name,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rr": rr,
            "zone_text": zone_text,
            "reason": reason_text,
            "trigger": trigger_detail,
            "location_label": location_label,
        }

    chosen_trade = None
    active_signal = None

    if bias == "bullish":
        bullish_signal_stack = [
            # OB/FVG retests first — cleanest SMC entry (institutional origin / imbalance)
            ("bullish_ob_retest", detect_ob_entry("buy", nearest_bull_ob, m5_candles, m1_candles) if nearest_bull_ob else {"passed": False, "detail": "No valid bullish OB"}, f"Price is reacting from bullish OB at {ob_buy_zone}." if nearest_bull_ob else "No bullish OB in range.", ob_buy_zone, "support"),
            ("bullish_fvg_retest", detect_fvg_entry("buy", nearest_bull_fvg, m5_candles, m1_candles) if nearest_bull_fvg else {"passed": False, "detail": "No valid bullish FVG"}, f"Bullish FVG retest at {fvg_buy_zone} is reacting." if nearest_bull_fvg else "No bullish FVG in range.", fvg_buy_zone, "support"),
            # Liquidity sweep and failed-break setups — strong context but less precise entry
            ("sell_side_sweep_reclaim", detect_liquidity_sweep_reversal("buy", nearest_support, m5_candles, m1_candles), "Sell-side liquidity was swept and price reclaimed support.", pullback_buy_zone, "support"),
            ("failed_breakdown_reclaim", detect_failed_break("buy", nearest_support, m5_candles, m1_candles), "Support was swept and reclaimed with bullish timing.", pullback_buy_zone, "support"),
            ("bullish_bos_retest", detect_retest_hold("buy", nearest_resistance, m5_candles, m1_candles), f"Resistance broke and is holding as support near {nearest_resistance:.2f}." if nearest_resistance is not None else "No bullish BOS retest.", f"Retest around {nearest_resistance:.2f}" if nearest_resistance is not None else "Retest zone", "breakout_zone"),
            ("bullish_displacement", detect_strong_breakout_impulse("buy", nearest_resistance, m5_candles, m1_candles), f"Bullish displacement is continuing above {nearest_resistance:.2f}." if nearest_resistance is not None else "No bullish displacement.", f"Above {nearest_resistance:.2f}" if nearest_resistance is not None else "Above breakout", "breakout_zone"),
        ]
        for signal_name, detector, reason_text, zone_text, location_label in bullish_signal_stack:
            if active_signal is None and detector.get("passed"):
                active_signal = {
                    "signal": signal_name,
                    "reason": reason_text,
                    "trigger": str(detector.get("detail", "") or ""),
                    "zone_text": zone_text,
                }
            chosen_trade = evaluate_smc_signal("buy", signal_name, detector, reason_text, zone_text, location_label)
            if chosen_trade is not None:
                break

    if bias == "bearish" and chosen_trade is None:
        bearish_signal_stack = [
            # OB/FVG retests first — cleanest SMC entry (institutional origin / imbalance)
            ("bearish_ob_retest", detect_ob_entry("sell", nearest_bear_ob, m5_candles, m1_candles) if nearest_bear_ob else {"passed": False, "detail": "No valid bearish OB"}, f"Price is reacting from bearish OB at {ob_sell_zone}." if nearest_bear_ob else "No bearish OB in range.", ob_sell_zone, "resistance"),
            ("bearish_fvg_retest", detect_fvg_entry("sell", nearest_bear_fvg, m5_candles, m1_candles) if nearest_bear_fvg else {"passed": False, "detail": "No valid bearish FVG"}, f"Bearish FVG retest at {fvg_sell_zone} is reacting." if nearest_bear_fvg else "No bearish FVG in range.", fvg_sell_zone, "resistance"),
            # Liquidity sweep and failed-break setups — strong context but less precise entry
            ("buy_side_sweep_reject", detect_liquidity_sweep_reversal("sell", nearest_resistance, m5_candles, m1_candles), "Buy-side liquidity was swept and rejected from resistance.", rally_sell_zone, "resistance"),
            ("failed_breakout_reject", detect_failed_break("sell", nearest_resistance, m5_candles, m1_candles), "Resistance was swept and rejected with bearish timing.", rally_sell_zone, "resistance"),
            ("bearish_bos_retest", detect_retest_hold("sell", nearest_support, m5_candles, m1_candles), f"Support broke and is failing from below near {nearest_support:.2f}." if nearest_support is not None else "No bearish BOS retest.", f"Retest around {nearest_support:.2f}" if nearest_support is not None else "Retest zone", "breakdown_zone"),
            ("bearish_displacement", detect_strong_breakout_impulse("sell", nearest_support, m5_candles, m1_candles), f"Bearish displacement is continuing below {nearest_support:.2f}." if nearest_support is not None else "No bearish displacement.", f"Below {nearest_support:.2f}" if nearest_support is not None else "Below breakdown", "breakdown_zone"),
        ]
        for signal_name, detector, reason_text, zone_text, location_label in bearish_signal_stack:
            if active_signal is None and detector.get("passed"):
                active_signal = {
                    "signal": signal_name,
                    "reason": reason_text,
                    "trigger": str(detector.get("detail", "") or ""),
                    "zone_text": zone_text,
                }
            chosen_trade = evaluate_smc_signal("sell", signal_name, detector, reason_text, zone_text, location_label)
            if chosen_trade is not None:
                break

    compact_candidates = [
        {
            "setup_type": str(item.get("setup_type", "") or ""),
            "side": str(item.get("side", "") or ""),
            "score": 100.0 if item.get("passed") and item.get("zone_ok") and item.get("confirmation_ok") else (70.0 if item.get("passed") else 0.0),
            "rr": round(float(item.get("rr") or 0.0), 2),
            "zone": str(item.get("zone", "") or ""),
            "trigger": str(item.get("trigger", "") or ""),
        }
        for item in signal_reviews[:6]
    ]

    def build_wait_requirements(*, bias: str, active_signal: dict[str, object] | None, zone_ok: bool, confirmation_ok: bool, location: str) -> list[str]:
        items: list[str] = []
        if active_signal is None:
            items.append("Valid M15 SMC structure")
        else:
            signal_name = str(active_signal.get("signal", "") or "").replace("_", " ").strip()
            items.append(f"M5 trigger for {signal_name}" if signal_name else "Cleaner M5 trigger")
        if not zone_ok:
            if location == "support":
                items.append("Price back into bullish PD / discount zone")
            elif location == "resistance":
                items.append("Price back into bearish PD / premium zone")
            else:
                items.append("Better M15 dealing location")
        if not confirmation_ok:
            items.append("Cleaner M5/M1 confirmation candle")
        if bias == "mixed":
            items.append("Clearer H1 and M15 alignment")
        return items[:4] if items else ["Cleaner M15 structure", "Better M15 dealing location", "Cleaner M5/M1 trigger"]

    if chosen_trade is not None:
        return build_trade_payload(
            board=board,
            side=str(chosen_trade["side"]),
            market_phase=market_phase,
            bias=bias,
            setup_type=str(chosen_trade["setup_type"]),
            location=str(chosen_trade.get("location_label", location)),
            zone_text=str(chosen_trade["zone_text"]),
            reason=str(chosen_trade["reason"]),
            why=why + [f"Execution area is {str(chosen_trade.get('location_label', location)).replace('_', ' ')}", str(chosen_trade["trigger"])],
            conflicts=conflicts,
            trigger_text=str(chosen_trade["trigger"]),
            execution_plan=f"{str(chosen_trade['side']).upper()} from {str(chosen_trade['zone_text'])} with invalidation beyond structure and targets into opposing liquidity.",
            entry=safe_float(chosen_trade["entry"]),
            sl=safe_float(chosen_trade["sl"]),
            tp1=safe_float(chosen_trade["tp1"]),
            tp2=safe_float(chosen_trade["tp2"]),
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": buy_zone_ok if str(chosen_trade["side"]) == "buy" else sell_zone_ok,
                "confirmation_ok": buy_confirmation_ok if str(chosen_trade["side"]) == "buy" else sell_confirmation_ok,
                "zone_text": str(chosen_trade["zone_text"]),
                "price": round(current_price, 2),
                "signal": str(chosen_trade.get("signal", "") or ""),
            },
            wait_for=[],
            smc_parameters=smc_parameters,
        )

    if bias == "bullish":
        if not buy_zone_ok:
            conflicts.append("Price has moved away from the buy zone")
        if not buy_confirmation_ok:
            conflicts.append("M5/M1 confirmation candle is missing")
        wait_why = list(why)
        if active_signal is not None and not buy_confirmation_ok:
            wait_why.append(f"Active structure: {str(active_signal.get('trigger') or active_signal.get('signal') or '')}")
        elif ltf_tone == "bullish" and not buy_confirmation_ok:
            wait_why = [
                "H1 context is bullish and M15 is supportive",
                "M5/M1 is stabilizing but not confirmed yet",
                "Price is near support / pullback value",
            ]
        return build_trade_payload(
            board=board,
            side="none",
            market_phase=market_phase,
            bias=bias,
            setup_type="none",
            location=location,
            zone_text=str(active_signal.get("zone_text")) if active_signal is not None else pullback_buy_zone,
            reason="Bullish context is valid, but the M5 trigger is not complete yet." if active_signal is not None else "Bullish context is clear, but no valid SMC structure is active yet.",
            why=wait_why,
            conflicts=conflicts,
            trigger_text=str(active_signal.get("trigger")) if active_signal is not None else "Waiting for M5/M1 bullish confirmation.",
            execution_plan="Wait for M5/M1 confirmation inside the active bullish SMC zone." if active_signal is not None else "Wait for a clean bullish structure on M15 and a valid M5 trigger before buying.",
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": buy_zone_ok,
                "confirmation_ok": buy_confirmation_ok,
                "zone_text": str(active_signal.get("zone_text")) if active_signal is not None else pullback_buy_zone,
                "price": round(current_price, 2),
                "signal": str(active_signal.get("signal", "") or "") if active_signal is not None else "",
            },
            wait_for=build_wait_requirements(
                bias=bias,
                active_signal=active_signal,
                zone_ok=buy_zone_ok,
                confirmation_ok=buy_confirmation_ok,
                location=location,
            ),
            smc_parameters=smc_parameters,
        )

    if bias == "bearish":
        if not sell_zone_ok:
            conflicts.append("Price has moved away from the sell zone")
        if not sell_confirmation_ok:
            conflicts.append("M5/M1 confirmation candle is missing")
        wait_why = list(why)
        if active_signal is not None and not sell_confirmation_ok:
            wait_why.append(f"Active structure: {str(active_signal.get('trigger') or active_signal.get('signal') or '')}")
        elif ltf_tone == "bearish" and not sell_confirmation_ok:
            wait_why = [
                "H1 context is bearish and M15 is supportive",
                "M5/M1 is softening but not confirmed yet",
                "Price is near resistance / rally value",
            ]
        return build_trade_payload(
            board=board,
            side="none",
            market_phase=market_phase,
            bias=bias,
            setup_type="none",
            location=location,
            zone_text=str(active_signal.get("zone_text")) if active_signal is not None else rally_sell_zone,
            reason="Bearish context is valid, but the M5 trigger is not complete yet." if active_signal is not None else "Bearish context is clear, but no valid SMC structure is active yet.",
            why=wait_why,
            conflicts=conflicts,
            trigger_text=str(active_signal.get("trigger")) if active_signal is not None else "Waiting for M5/M1 bearish confirmation.",
            execution_plan="Wait for M5/M1 confirmation inside the active bearish SMC zone." if active_signal is not None else "Wait for a clean bearish structure on M15 and a valid M5 trigger before selling.",
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            pattern_candidates=compact_candidates,
            entry_checks={
                "zone_ok": sell_zone_ok,
                "confirmation_ok": sell_confirmation_ok,
                "zone_text": str(active_signal.get("zone_text")) if active_signal is not None else rally_sell_zone,
                "price": round(current_price, 2),
                "signal": str(active_signal.get("signal", "") or "") if active_signal is not None else "",
            },
            wait_for=build_wait_requirements(
                bias=bias,
                active_signal=active_signal,
                zone_ok=sell_zone_ok,
                confirmation_ok=sell_confirmation_ok,
                location=location,
            ),
            smc_parameters=smc_parameters,
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
        execution_plan="Wait for clearer H1 context, stronger M15 structure, and cleaner M5/M1 timing.",
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
            "signal": "",
        },
        wait_for=build_wait_requirements(
            bias=bias,
            active_signal=active_signal,
            zone_ok=False,
            confirmation_ok=False,
            location=location,
        ),
        smc_parameters=smc_parameters,
    )

def model_safe_token(model: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in str(model or "model"))
    trimmed = normalized.strip("-")
    return trimmed or "model"


def http_json(url: str, method: str = "GET", payload: dict[str, object] | None = None, timeout: int = OLLAMA_TIMEOUT_SECONDS) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 QuantumBot/1.0",
        },
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
        news_calendar_status = build_manual_news_calendar_status(load_manual_news_calendar())
        if bool(news_calendar_status.get("blocked")):
            active_event = news_calendar_status.get("active_event") if isinstance(news_calendar_status.get("active_event"), dict) else {}
            detail = f"Manual news block active for {str(active_event.get('title', 'scheduled event') or 'scheduled event')}."
            log_autotrade("autotrade_dispatch", "news_block", detail, news_event=active_event)
            return HTTPStatus.OK.value, {
                "status": "news_block",
                "detail": detail,
                "news_calendar": news_calendar_status,
            }
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


def maybe_sync_google_sheet_after_close() -> None:
    try:
        from google_sheet_sync import (
            WEBHOOK_URL,
            GOOGLE_SERVICE_ACCOUNT_JSON,
            GOOGLE_SHEET_ID,
            aggregate_rows,
            push_to_google_sheet,
            push_to_webhook,
        )
    except Exception:
        return

    direct_enabled = bool(GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID)
    webhook_enabled = bool(WEBHOOK_URL)
    if not direct_enabled and not webhook_enabled:
        return

    try:
        history = fetch_closed_deals_history(period="all")
    except Exception as error:
        append_ai_logic_audit(
            build_ai_logic_event(
                "google_sheet_sync",
                "error",
                str(AUTONOMOUS_AI_STATE.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL),
                detail=f"Could not load closed history for sheet sync: {error}",
                model=CURRENT_STRATEGY_MODEL,
            )
        )
        return

    deals = history.get("deals", [])
    latest_closed_deal = None
    for deal in deals:
        latest_closed_deal = deal
        break

    if not latest_closed_deal:
        return

    latest_ticket = int(latest_closed_deal.get("ticket", 0) or 0)
    if latest_ticket <= 0:
        return

    persisted_ticket = load_last_sheet_sync_ticket()
    with AUTOTRADE_LOCK:
        remembered_ticket = int(AUTOTRADE_STATE.get("last_sheet_sync_ticket", 0) or 0)
        if max(remembered_ticket, persisted_ticket) == latest_ticket:
            return

    try:
        daily_rows, summary_rows = aggregate_rows(deals)
        close_label = str(latest_closed_deal.get("close_time_label", "") or "").strip()
        broker_date = ""
        if close_label:
            broker_date = datetime.strptime(close_label, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        if direct_enabled:
            push_to_google_sheet(daily_rows, summary_rows, "all")
        else:
            push_to_webhook(daily_rows, summary_rows, "all")
        with AUTOTRADE_LOCK:
            AUTOTRADE_STATE["last_sheet_sync_ticket"] = latest_ticket
        save_last_sheet_sync_ticket(latest_ticket)
        append_ai_logic_audit(
            build_ai_logic_event(
                "google_sheet_sync",
                "success",
                str(latest_closed_deal.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL),
                detail=f"Synced Google Sheets after closed ticket {latest_ticket} for {broker_date or 'latest date'}.",
                model=CURRENT_STRATEGY_MODEL,
                ticket=latest_ticket,
            )
        )
    except Exception as error:
        append_ai_logic_audit(
            build_ai_logic_event(
                "google_sheet_sync",
                "error",
                str(latest_closed_deal.get("symbol", AUTONOMOUS_AI_SYMBOL) or AUTONOMOUS_AI_SYMBOL),
                detail=f"Google Sheets sync failed for ticket {latest_ticket}: {error}",
                model=CURRENT_STRATEGY_MODEL,
                ticket=latest_ticket,
            )
        )


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
                magic_value = int(getattr(order, "magic", 0) or 0)
                existing = order_meta_by_key.get(order_key) or {}
                if sl_value not in (None, 0, 0.0):
                    existing["sl"] = float(sl_value)
                if tp_value not in (None, 0, 0.0):
                    existing["tp"] = float(tp_value)
                if magic_value:
                    existing["magic"] = magic_value
                order_meta_by_key[order_key] = existing

            closed_entries = {DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY, DEAL_ENTRY_INOUT}
            grouped: dict[int, dict[str, object]] = {}
            dashboard_now = get_dashboard_now()
            today_start = dashboard_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

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
                magic = int(getattr(deal, "magic", 0) or 0)
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
                        "magic": magic,
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
                if magic:
                    group["magic"] = magic
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
                if int(group["magic"] or 0) == 0 and order_meta.get("magic") not in (None, 0, 0.0):
                    group["magic"] = int(order_meta["magic"])

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
                    "magic": int(group["magic"] or 0),
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

            rows = [row for row in rows if int(row.get("magic", 0) or 0) == AUTOTRADE_MAGIC]

            all_time_rows = [
                item for item in rows
                if to_dashboard_time(int(item["close_time"])) >= HISTORY_ALL_TIME_BASELINE
            ]

            total_net = sum(float(item["net"]) for item in all_time_rows)
            total_profit = sum(float(item["net"]) for item in all_time_rows if float(item["net"]) >= 0)
            total_loss = sum(float(item["net"]) for item in all_time_rows if float(item["net"]) < 0)
            win_count = sum(1 for item in all_time_rows if float(item["net"]) >= 0)
            loss_count = sum(1 for item in all_time_rows if float(item["net"]) < 0)
            today_rows = [item for item in all_time_rows if bool(item.get("is_today"))]
            today_net = sum(float(item["net"]) for item in today_rows)
            today_profit = sum(float(item["net"]) for item in today_rows if float(item["net"]) >= 0)
            today_loss = sum(float(item["net"]) for item in today_rows if float(item["net"]) < 0)
            today_wins = sum(1 for item in today_rows if float(item["net"]) >= 0)
            today_losses = sum(1 for item in today_rows if float(item["net"]) < 0)

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
                "timezone": "MT5 Broker Time",
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


def google_sheet_sync_worker() -> None:
    while True:
        try:
            maybe_sync_google_sheet_after_close()
        except Exception:
            pass
        time.sleep(GOOGLE_SHEET_SYNC_INTERVAL_SECONDS)


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
                "news_calendar": build_manual_news_calendar_status(load_manual_news_calendar()),
            }
        self.respond_json(HTTPStatus.OK, payload)

    def handle_autotrade_config(self) -> None:
        try:
            payload = self.read_json_body()
            enabled = bool(payload.get("enabled", AUTOTRADE_STATE.get("enabled", False)))
            lot = max(0.01, float(payload.get("lot", AUTOTRADE_STATE.get("lot", 0.01)) or 0.01))
            news_calendar_payload = payload.get("news_calendar")
        except (ValueError, TypeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"detail": "Auto trade config requires valid enabled and lot values."})
            return

        with AUTOTRADE_LOCK:
            AUTOTRADE_STATE["enabled"] = enabled
            AUTOTRADE_STATE["lot"] = lot
            if news_calendar_payload is not None:
                news_calendar_state = save_manual_news_calendar(news_calendar_payload)
            else:
                news_calendar_state = load_manual_news_calendar()
            response = {
                "enabled": AUTOTRADE_STATE["enabled"],
                "lot": AUTOTRADE_STATE["lot"],
                "one_trade_only": True,
                "news_calendar": build_manual_news_calendar_status(news_calendar_state),
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
    sheet_sync_thread = threading.Thread(target=google_sheet_sync_worker, name="google-sheet-sync-worker", daemon=True)
    sheet_sync_thread.start()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Serving Quantum workspace at http://{HOST}:{PORT}")
    print(f"Autonomous AI loop active for {AUTONOMOUS_AI_SYMBOL} every {AUTONOMOUS_AI_INTERVAL_SECONDS // 60} minutes.")
    print(f"Google Sheets sync worker active every {GOOGLE_SHEET_SYNC_INTERVAL_SECONDS} seconds.")
    print("Keep this terminal window open while using the site.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
