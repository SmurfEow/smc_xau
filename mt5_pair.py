"""Verify a user's JustMarkets MT5 terminal without collecting a broker password.

Run this on the Windows machine/VPS where the family member has already logged
into their own MT5 terminal. The pairing code is short-lived and single-use.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import MetaTrader5 as mt5
from server import AUTH_LOCK, auth_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Pair an already logged-in MT5 terminal with Quantum.")
    parser.add_argument("--code", required=True, help="Pairing code shown in the Quantum client account.")
    parser.add_argument("--terminal", required=True, help="Full path to this account's terminal64.exe.")
    args = parser.parse_args()
    if not mt5.initialize(path=args.terminal):
        raise SystemExit(f"Could not connect to this MT5 terminal: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None:
            raise SystemExit("MT5 has no logged-in trading account. Log in to JustMarkets MT5 first.")
        login = str(getattr(account, "login", ""))
        server = str(getattr(account, "server", ""))
        detected_mode = "demo" if "demo" in server.lower() else "real"
    finally:
        mt5.shutdown()
    with AUTH_LOCK, auth_connection() as connection:
        pairing = connection.execute("SELECT user_id, account_mode, claimed_login FROM broker_pairings WHERE pair_code = ? AND expires_at > ? AND verified_at IS NULL", (args.code.upper(), datetime.now(timezone.utc).isoformat())).fetchone()
        if not pairing:
            raise SystemExit("Pairing code is invalid, expired, or already used.")
        if login != str(pairing["claimed_login"]):
            raise SystemExit("MT5 account number does not match the number entered in Quantum. Pairing rejected.")
        if detected_mode != str(pairing["account_mode"]):
            raise SystemExit(f"MT5 server '{server}' is {detected_mode.upper()}, but Quantum was set to {pairing['account_mode'].upper()}. Pairing rejected.")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute("UPDATE broker_pairings SET verified_at = ?, mt5_server = ?, pair_code = '' WHERE user_id = ?", (now, server, pairing["user_id"]))
        connection.execute("INSERT INTO copy_accounts (user_id, terminal_path, label, enabled, created_at, account_mode, mt5_login) VALUES (?, ?, ?, 0, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET terminal_path = excluded.terminal_path, label = excluded.label, enabled = 0, account_mode = excluded.account_mode, mt5_login = excluded.mt5_login", (pairing["user_id"], args.terminal, f"JustMarkets {login}", now, detected_mode, login))
    print(f"Verified JustMarkets {detected_mode.upper()} account {login} on {server}. Copying remains disabled until explicitly enabled.")


if __name__ == "__main__":
    main()
