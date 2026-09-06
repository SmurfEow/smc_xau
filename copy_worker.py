"""One deliberately separate MT5 execution worker for one private copy account.

Start only after the account owner has logged into its dedicated MT5 terminal.
No broker credential is accepted by this program or stored in Quantum.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
from server import AUTH_LOCK, TELEGRAM_BOT_TOKEN, auth_connection


def notify(user_id: str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    with AUTH_LOCK, auth_connection() as connection:
        row = connection.execute("SELECT chat_id FROM telegram_links WHERE user_id = ?", (user_id,)).fetchone()
        pref = connection.execute("SELECT trade_alerts FROM notification_preferences WHERE user_id = ?", (user_id,)).fetchone()
    if pref and not pref["trade_alerts"]:
        return
    if row and row["chat_id"]:
        from telegram_worker import reply
        reply(TELEGRAM_BOT_TOKEN, str(row["chat_id"]), text)


def next_command(email: str):
    with AUTH_LOCK, auth_connection() as connection:
        row = connection.execute("SELECT copy_commands.id, copy_commands.command, copy_commands.user_id, copy_commands.payload, copy_settings.active_lot, copy_settings.active_max_entries, copy_accounts.terminal_path, copy_accounts.mt5_login, copy_accounts.account_mode FROM copy_commands JOIN users ON users.id = copy_commands.user_id JOIN copy_settings ON copy_settings.user_id = users.id JOIN copy_accounts ON copy_accounts.user_id = users.id WHERE users.email = ? AND copy_accounts.enabled = 1 AND copy_commands.status = 'queued' ORDER BY copy_commands.created_at LIMIT 1", (email,)).fetchone()
        if row:
            connection.execute("UPDATE copy_commands SET status = 'processing' WHERE id = ?", (row["id"],))
        return row


def finish(command_id: str, status: str, result: dict):
    with AUTH_LOCK, auth_connection() as connection:
        connection.execute("UPDATE copy_commands SET status = ?, result = ?, completed_at = ? WHERE id = ?", (status, json.dumps(result), datetime.now(timezone.utc).isoformat(), command_id))


def verify_terminal_account(row) -> str | None:
    account = mt5.account_info()
    if account is None:
        return "No MT5 account is logged into this terminal."
    if str(getattr(account, "login", "")) != str(row["mt5_login"]):
        return "Safety block: this MT5 terminal is logged into a different account than the paired Quantum account. Re-pair before copying."
    server = str(getattr(account, "server", ""))
    mode = "demo" if "demo" in server.lower() else "real"
    if mode != str(row["account_mode"]):
        return "Safety block: this MT5 terminal's Demo/Real mode no longer matches the paired account. Re-pair before copying."
    return None


def copy_open(row) -> dict:
    payload = json.loads(row["payload"])
    if not mt5.initialize(path=str(row["terminal_path"])):
        return {"detail": f"MT5 initialization failed: {mt5.last_error()}"}
    try:
        safety_error = verify_terminal_account(row)
        if safety_error:
            return {"detail": safety_error}
        symbol = str(payload["symbol"])
        info = mt5.symbol_info(symbol)
        if info is None:
            return {"detail": f"Symbol {symbol} is unavailable in this terminal."}
        if not info.visible:
            mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"detail": "No live quote available."}
        lot = max(float(getattr(info, "volume_min", 0.01)), min(float(row["active_lot"]), float(getattr(info, "volume_max", 100))))
        step = float(getattr(info, "volume_step", 0.01))
        lot = round(lot / step) * step
        side = str(payload["side"])
        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot, "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL, "price": tick.ask if side == "buy" else tick.bid, "sl": payload.get("sl") or 0.0, "tp": payload.get("tp") or 0.0, "deviation": 20, "magic": 20260325, "comment": "Quantum Family Copy"}
        with AUTH_LOCK, auth_connection() as connection:
            existing = connection.execute("SELECT COUNT(*) AS total FROM copy_positions WHERE user_id = ? AND closed_at IS NULL", (row["user_id"],)).fetchone()["total"]
        if int(existing) >= int(row["active_max_entries"]):
            return {"detail": "Maximum active copied entries reached."}
        result = mt5.order_send(request)
        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"detail": getattr(result, "comment", "MT5 rejected copy order") if result else "MT5 order returned nothing."}
        ticket = int(getattr(result, "order", 0) or getattr(result, "deal", 0) or 0)
        live_position = mt5.positions_get(ticket=ticket)
        entry_price = float(live_position[0].price_open) if live_position else None
        with AUTH_LOCK, auth_connection() as connection:
            connection.execute("INSERT INTO copy_positions (user_id, master_ticket, client_ticket, side, volume, opened_at) VALUES (?, ?, ?, ?, ?, ?)", (row["user_id"], int(payload["master_ticket"]), ticket, side, lot, datetime.now(timezone.utc).isoformat()))
            connection.execute("INSERT OR REPLACE INTO copy_trade_ledger (client_ticket, user_id, master_ticket, symbol, side, volume, entry_price, stop_loss, take_profit, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (ticket, row["user_id"], int(payload["master_ticket"]), symbol, side, lot, entry_price, payload.get("sl"), payload.get("tp"), datetime.now(timezone.utc).isoformat()))
        notify(str(row["user_id"]), f"🟢 TRADE EXECUTED\n\n{symbol} • {side.upper()}\nYour lot: {lot}\nEntry: {entry_price if entry_price is not None else 'pending'}\nStop Loss: {payload.get('sl')}\nTake Profit: {payload.get('tp')}\n\nMaster trade copied successfully.")
        return {"status": "copied", "ticket": ticket, "lot": lot}
    finally:
        mt5.shutdown()


def copy_close(row) -> dict:
    payload = json.loads(row["payload"])
    with AUTH_LOCK, auth_connection() as connection:
        position = connection.execute("SELECT client_ticket, side, volume FROM copy_positions WHERE user_id = ? AND master_ticket = ? AND closed_at IS NULL ORDER BY opened_at DESC LIMIT 1", (row["user_id"], int(payload["master_ticket"]))).fetchone()
    if not position:
        return {"status": "closed", "detail": "No active copied position."}
    if not mt5.initialize(path=str(row["terminal_path"])):
        return {"detail": f"MT5 initialization failed: {mt5.last_error()}"}
    try:
        safety_error = verify_terminal_account(row)
        if safety_error:
            return {"detail": safety_error}
        live = mt5.positions_get(ticket=int(position["client_ticket"]))
        if not live:
            result = {"status": "closed", "detail": "Copied position was already closed."}
        else:
            trade = live[0]
            tick = mt5.symbol_info_tick(trade.symbol)
            side = "buy" if trade.type == mt5.POSITION_TYPE_BUY else "sell"
            request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": trade.symbol, "volume": trade.volume, "type": mt5.ORDER_TYPE_SELL if side == "buy" else mt5.ORDER_TYPE_BUY, "position": trade.ticket, "price": tick.bid if side == "buy" else tick.ask, "deviation": 20, "magic": 20260325, "comment": "Quantum Family Copy exit"}
            sent = mt5.order_send(request)
            result = {"status": "closed"} if sent and sent.retcode == mt5.TRADE_RETCODE_DONE else {"detail": getattr(sent, "comment", "MT5 rejected copy close") if sent else "MT5 close returned nothing."}
        if result.get("status") == "closed":
            deals = mt5.history_deals_get(position=int(position["client_ticket"])) or []
            net_profit = sum(float(getattr(deal, "profit", 0) or 0) + float(getattr(deal, "commission", 0) or 0) + float(getattr(deal, "swap", 0) or 0) for deal in deals)
            outcome = "win" if net_profit > 0.00001 else "loss" if net_profit < -0.00001 else "breakeven"
            with AUTH_LOCK, auth_connection() as connection:
                connection.execute("UPDATE copy_positions SET closed_at = ? WHERE user_id = ? AND master_ticket = ? AND client_ticket = ?", (datetime.now(timezone.utc).isoformat(), row["user_id"], int(payload["master_ticket"]), int(position["client_ticket"])))
                connection.execute("UPDATE copy_trade_ledger SET closed_at = ?, net_profit = ?, outcome = ? WHERE client_ticket = ?", (datetime.now(timezone.utc).isoformat(), net_profit, outcome, int(position["client_ticket"])))
            result.update({"net_profit": round(net_profit, 2), "outcome": outcome})
            icon = "✅ WIN" if outcome == "win" else "❌ LOSS" if outcome == "loss" else "➖ BREAKEVEN"
            notify(str(row["user_id"]), f"🏁 TRADE CLOSED — {icon}\n\nNet P/L: {net_profit:+.2f}\nSource: follower MT5 account history")
        return result
    finally:
        mt5.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True, help="The linked Quantum client email.")
    args = parser.parse_args()
    try:
        while True:
            row = next_command(args.email)
            if not row:
                time.sleep(2)
                continue
            result = copy_close(row) if row["command"] == "close" else copy_open(row)
            finish(str(row["id"]), "completed" if result.get("status") in {"copied", "closed"} else "failed", result)
    except KeyboardInterrupt:
        print("Copy worker stopped.")


if __name__ == "__main__":
    main()
