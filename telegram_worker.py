"""Private Telegram controls for paired Quantum family accounts."""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from server import AUTH_LOCK, auth_connection, copy_settings_for


def api(token: str, method: str, payload: dict):
    request = Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def reply(token: str, chat_id: str, text: str):
    api(token, "sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": {"keyboard": [["📊 Status", "🛰 Master Bot"], ["📚 History"], ["▶ Resume", "⏸ Pause"], ["🔕 Mute Alerts", "🔔 Unmute Alerts"], ["⚙ Change Settings", "✅ Confirm Changes"], ["↩ Discard Pending", "❓ Help"]], "resize_keyboard": True, "is_persistent": True}})


def linked_user(chat_id: str):
    with AUTH_LOCK, auth_connection() as connection:
        return connection.execute("SELECT users.id, users.email FROM telegram_links JOIN users ON users.id = telegram_links.user_id WHERE telegram_links.chat_id = ?", (chat_id,)).fetchone()


def live_master_copy_status() -> dict[str, object]:
    """Read state from the actual `python server.py` runtime.

    The Telegram worker is imported by server.py. Importing `server` here would
    create a second module with fresh OFF/default state, so resolve __main__.
    """
    runtime = sys.modules.get("__main__")
    getter = getattr(runtime, "master_copy_status", None)
    if callable(getter):
        return getter()
    return {"autotrade_enabled": False, "trade_active": False, "server_loop_status": "unavailable", "server_loop_detail": "Master runtime unavailable", "last_run_at": 0}


HELP = """⚡ QUANTUM FAMILY CONTROL

Use the buttons below for status and copying controls.

📚 History
/history today
/history yesterday
/history week
/history month
/history YYYY-MM-DD YYYY-MM-DD

/mute — silence real-time trade alerts
/unmute — restore real-time trade alerts
/cancel_settings — discard staged lot/entry changes
/master — check whether the master bot and its auto-trading are live

To update exposure, send:
/change LOT ENTRIES
Example: /change 0.05 1

Then tap ✅ Confirm Changes.

🔒 Direction, entry logic, TP, and SL always follow the master bot."""


def history_range(parts: list[str]):
    myt = timezone(timedelta(hours=8))
    now = datetime.now(myt)
    boundary = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now < boundary:
        boundary -= timedelta(days=1)
    scope = parts[1].lower() if len(parts) > 1 else ""
    if scope == "today": return boundary, boundary + timedelta(days=1), "TODAY"
    if scope == "yesterday": return boundary - timedelta(days=1), boundary, "YESTERDAY"
    if scope == "week":
        start = boundary - timedelta(days=boundary.weekday())
        return start, boundary + timedelta(days=1), "THIS WEEK"
    if scope == "month":
        start = boundary.replace(day=1)
        return start, boundary + timedelta(days=1), "THIS MONTH"
    if len(parts) == 3:
        start = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=myt, hour=5)
        end = datetime.strptime(parts[2], "%Y-%m-%d").replace(tzinfo=myt, hour=5) + timedelta(days=1)
        if end <= start: raise ValueError
        return start, end, f"{parts[1]} → {parts[2]}"
    raise ValueError


def history_message(user_id: str, parts: list[str]) -> str:
    try:
        start, end, label = history_range(parts)
    except ValueError:
        return "📚 TRADE HISTORY\n\nChoose: /history today, yesterday, week, or month\nCustom: /history 2026-09-01 2026-09-05\n\nEach period runs from 5:00 AM MYT."
    with AUTH_LOCK, auth_connection() as connection:
        summary = connection.execute("SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END), 0) AS wins, COALESCE(SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END), 0) AS losses, COALESCE(SUM(net_profit), 0) AS pnl, COALESCE(AVG(CASE WHEN net_profit > 0 THEN net_profit END), 0) AS avg_win, COALESCE(AVG(CASE WHEN net_profit < 0 THEN net_profit END), 0) AS avg_loss, COALESCE(MAX(net_profit), 0) AS best, COALESCE(MIN(net_profit), 0) AS worst FROM copy_trade_ledger WHERE user_id = ? AND closed_at >= ? AND closed_at < ?", (user_id, start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat())).fetchone()
        rows = connection.execute("SELECT symbol, side, volume, net_profit, outcome, closed_at FROM copy_trade_ledger WHERE user_id = ? AND closed_at >= ? AND closed_at < ? ORDER BY closed_at DESC LIMIT 6", (user_id, start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat())).fetchall()
    total = int(summary["total"])
    rate = (float(summary["wins"]) / total * 100) if total else 0
    lines = [f"📚 {label} — 5:00 AM MYT", "", f"Closed trades: {total}", f"Wins / Losses: {summary['wins']} / {summary['losses']}", f"Win rate: {rate:.1f}%", f"Net P/L: {float(summary['pnl']):+.2f}", f"Average win / loss: {float(summary['avg_win']):+.2f} / {float(summary['avg_loss']):+.2f}", f"Best / worst: {float(summary['best']):+.2f} / {float(summary['worst']):+.2f}"]
    if rows:
        lines += ["", "Latest closed trades:"]
        lines += [f"{'✅' if row['outcome'] == 'win' else '❌' if row['outcome'] == 'loss' else '➖'} {row['symbol']} {str(row['side']).upper()} • {row['volume']} lot • {float(row['net_profit']):+.2f}" for row in rows]
    else: lines += ["", "No closed copied trades in this period."]
    return "\n".join(lines)


def master_health_message() -> str:
    """Return an honest, family-friendly view of the master process state."""
    status = live_master_copy_status()
    last_run_at = float(status.get("last_run_at") or 0)
    age_seconds = max(0, int(time.time() - last_run_at)) if last_run_at else None
    # The server loop normally checks every 10 seconds.  Thirty seconds gives
    # it room for a slow broker/API call without pretending a stale process is live.
    loop_live = age_seconds is not None and age_seconds <= 30
    if loop_live:
        heartbeat = f"ONLINE • last market check {age_seconds}s ago"
    elif age_seconds is None:
        heartbeat = "WAITING • no market-check heartbeat yet"
    else:
        heartbeat = f"STALE • last market check {age_seconds}s ago"
    auto = "ON — new qualified setups may be traded" if status.get("autotrade_enabled") else "OFF — the master automation switch is off; no new master trades will be opened"
    trade = "A master position is currently active" if status.get("trade_active") else "No master position is currently active"
    detail = str(status.get("server_loop_detail") or status.get("server_loop_status") or "—")
    return f"🛰 MASTER BOT HEALTH\n\nService: {heartbeat}\nAuto-trading: {auto}\nTrade state: {trade}\nLatest check: {detail}\n\nYour own copying setting remains separate. If the master auto-trading is OFF or the service is stale, no new trades can be mirrored."


def handle(token: str, chat_id: str, text: str):
    button_commands = {"📊 Status": "/status", "🛰 Master Bot": "/master", "📚 History": "/history", "▶ Resume": "/resume", "⏸ Pause": "/pause", "🔕 Mute Alerts": "/mute", "🔔 Unmute Alerts": "/unmute", "⚙ Change Settings": "/change", "✅ Confirm Changes": "/confirm", "↩ Discard Pending": "/cancel_settings", "❓ Help": "/help"}
    text = button_commands.get(text.strip(), text)
    parts = text.strip().split()
    command = parts[0].lower() if parts else ""
    if command in {"/start", "/help"}:
        return reply(token, chat_id, HELP)
    if command == "/link" and len(parts) == 2:
        with AUTH_LOCK, auth_connection() as connection:
            row = connection.execute("SELECT user_id FROM telegram_links WHERE pair_code = ? AND pair_expires_at > ?", (parts[1].upper(), datetime.now(timezone.utc).isoformat())).fetchone()
            if row:
                connection.execute("UPDATE telegram_links SET chat_id = ?, pair_code = NULL, pair_expires_at = NULL WHERE user_id = ?", (chat_id, row["user_id"]))
        return reply(token, chat_id, "Quantum account paired successfully.\n\n" + HELP) if row else reply(token, chat_id, "That pairing code is invalid or expired. Generate a fresh code from Quantum and try again.")
    user = linked_user(chat_id)
    if not user:
        return reply(token, chat_id, "Pair first: sign in to Quantum and request a Telegram pairing code.")
    if command == "/master":
        return reply(token, chat_id, master_health_message())
    if command == "/status":
        settings = copy_settings_for(user["id"])
        with AUTH_LOCK, auth_connection() as connection:
            account = connection.execute("SELECT enabled, account_mode, mt5_login FROM copy_accounts WHERE user_id = ?", (user["id"],)).fetchone()
            broker = connection.execute("SELECT mt5_server, verified_at FROM broker_pairings WHERE user_id = ?", (user["id"],)).fetchone()
            preference = connection.execute("SELECT trade_alerts FROM notification_preferences WHERE user_id = ?", (user["id"],)).fetchone()
        broker_text = "Not connected"
        if account and broker and broker["verified_at"]:
            broker_text = f"Verified {str(account['account_mode']).upper()} MT5 ••••{str(account['mt5_login'])[-4:]}\nServer: {broker['mt5_server']}"
        copy_text = "PAUSED" if not account or not account["enabled"] else ("READY" if settings.get("armed") else "NOT READY")
        active = settings.get("active") or "Not confirmed"
        pending = settings.get("pending") or "None"
        alerts = "ON" if not preference or preference["trade_alerts"] else "MUTED"
        master_auto = "ON" if live_master_copy_status().get("autotrade_enabled") else "OFF (master switch)"
        return reply(token, chat_id, f"📊 QUANTUM STATUS\n\n🏦 Broker\n{broker_text}\n\n⚡ Your copying: {copy_text}\n🛰 Master auto-trading: {master_auto}\n🔔 Trade alerts: {alerts}\n⚙ Active settings: {active}\n📝 Pending settings: {pending}\n\nUse 🛰 Master Bot for the live service heartbeat.\n🔒 TP/SL and direction are locked to the master bot.")
    if command == "/history":
        return reply(token, chat_id, history_message(str(user["id"]), parts))
    if command in {"/mute", "/unmute"}:
        enabled = 0 if command == "/mute" else 1
        with AUTH_LOCK, auth_connection() as connection:
            connection.execute("INSERT INTO notification_preferences (user_id, trade_alerts) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET trade_alerts = excluded.trade_alerts", (user["id"], enabled))
        return reply(token, chat_id, "🔕 Real-time trade execution and closure alerts are muted. Your 5:00 AM MYT daily summary will still be sent." if not enabled else "🔔 Real-time trade alerts are back on. Daily summaries remain enabled.")
    if command == "/cancel_settings":
        with AUTH_LOCK, auth_connection() as connection:
            connection.execute("UPDATE copy_settings SET pending_lot = NULL, pending_max_entries = NULL, updated_at = ? WHERE user_id = ?", (datetime.now(timezone.utc).isoformat(), user["id"]))
        return reply(token, chat_id, "↩ Pending settings discarded. Your currently active lot size and entry limit remain unchanged.")
    if command in {"/pause", "/resume"}:
        with AUTH_LOCK, auth_connection() as connection:
            account = connection.execute("SELECT 1 FROM copy_accounts WHERE user_id = ?", (user["id"],)).fetchone()
            active = connection.execute("SELECT active_lot FROM copy_settings WHERE user_id = ?", (user["id"],)).fetchone()
            if account and (command == "/pause" or active and active["active_lot"] is not None):
                connection.execute("UPDATE copy_accounts SET enabled = ? WHERE user_id = ?", (0 if command == "/pause" else 1, user["id"]))
            else:
                return reply(token, chat_id, "Cannot resume yet. Verify your MT5 account and confirm lot size/entry settings first.")
        return reply(token, chat_id, "Copying paused. No future master entries will be mirrored; existing positions are unchanged." if command == "/pause" else "Copying resumed. Only future master trades will be mirrored using your confirmed settings.")
    if command == "/change" and len(parts) == 3:
        try:
            lot, entries = round(float(parts[1]), 2), int(parts[2])
            if not 0.01 <= lot <= 100 or not 1 <= entries <= 10: raise ValueError
        except ValueError:
            return reply(token, chat_id, "Use /change LOT ENTRIES, for example /change 0.02 1")
        with AUTH_LOCK, auth_connection() as connection:
            connection.execute("INSERT INTO copy_settings (user_id, pending_lot, pending_max_entries, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET pending_lot = excluded.pending_lot, pending_max_entries = excluded.pending_max_entries, updated_at = excluded.updated_at", (user["id"], lot, entries, datetime.now(timezone.utc).isoformat()))
        return reply(token, chat_id, f"Settings staged:\n• Lot size: {lot}\n• Maximum entries: {entries}\n\nSend /confirm to apply these only to the next new master trade. Current positions, TP, and SL will not change.")
    if command == "/change":
        return reply(token, chat_id, "⚙ CHANGE SETTINGS\n\nSend: /change LOT ENTRIES\nExample: /change 0.05 1\n\nYour changes will remain pending until you tap ✅ Confirm Changes.")
    if command == "/confirm":
        with AUTH_LOCK, auth_connection() as connection:
            connection.execute("UPDATE copy_settings SET active_lot = pending_lot, active_max_entries = pending_max_entries, pending_lot = NULL, pending_max_entries = NULL, updated_at = ? WHERE user_id = ? AND pending_lot IS NOT NULL", (datetime.now(timezone.utc).isoformat(), user["id"]))
        return reply(token, chat_id, "Settings confirmed. They will be used only for the next new master trade. Your current positions remain unchanged.")
    reply(token, chat_id, HELP)


def telegram_worker(token: str):
    offset = 0
    while True:
        try:
            updates = api(token, "getUpdates", {"offset": offset, "timeout": 25}).get("result", [])
            for update in updates:
                offset = int(update["update_id"]) + 1
                message = update.get("message", {})
                text = message.get("text")
                if text:
                    handle(token, str(message["chat"]["id"]), text)
        except Exception:
            time.sleep(5)
