
import os
from pathlib import Path

from dotenv import load_dotenv
from pybit.unified_trading import HTTP


ENV_FILE = Path(__file__).resolve().parent / ".env"


def check_response(response):
    if response.get("retCode") != 0:
        raise RuntimeError(
            "Bybit API returned a non-success response"
        )
    return response.get("result") or {}


def main():
    print("=" * 50)
    print("TRADINGOS V2 - BYBIT DEMO CONNECTION TEST")
    print("=" * 50)

    load_dotenv(ENV_FILE, override=True)

    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()
    demo = os.getenv("BYBIT_DEMO", "").strip().lower()

    if not key or not secret:
        print("FAILED: API credentials missing")
        return

    if demo not in ("true", "1", "yes", "on"):
        print("BLOCKED: BYBIT_DEMO must be true")
        print("No API request made.")
        return

    print("Environment: BYBIT DEMO")
    print("Mode: READ ONLY")
    print("Endpoint: api-demo.bybit.com")
    print("Credentials: Loaded (hidden)")
    print()

    session = HTTP(
        testnet=False,
        demo=True,
        api_key=key,
        api_secret=secret,
        recv_window=10000,
    )

    try:
        wallet = check_response(
            session.get_wallet_balance(accountType="UNIFIED")
        )

        accounts = wallet.get("list") or []

        if not accounts:
            print("WALLET: No account records returned")
            return

        account = accounts[0]

        print("WALLET CONNECTION: SUCCESS")
        print("Account Type:", account.get("accountType", "UNIFIED"))
        print("Total Equity:", account.get("totalEquity", "?"))
        print(
            "Total Available Balance:",
            account.get("totalAvailableBalance", "?"),
        )

    except Exception:
        print("WALLET CONNECTION: FAILED")
        print(
            "Check demo API permissions, account type, "
            "API-key environment, IP restrictions and system time."
        )
        print("Raw authentication errors suppressed.")
        return

    print()

    try:
        positions_result = check_response(
            session.get_positions(
                category="linear",
                settleCoin="USDT",
                limit=200,
            )
        )

        positions = [
            p for p in positions_result.get("list", [])
            if float(p.get("size") or 0) > 0
        ]

        print("POSITIONS CONNECTION: SUCCESS")
        print("Open USDT Linear Positions:", len(positions))

    except Exception:
        print("POSITIONS CONNECTION: FAILED")
        print("Raw API errors suppressed.")

    try:
        orders_result = check_response(
            session.get_open_orders(
                category="linear",
                settleCoin="USDT",
                limit=50,
            )
        )

        orders = orders_result.get("list") or []

        print("ORDERS CONNECTION: SUCCESS")
        print("Pending Orders (first page):", len(orders))

    except Exception:
        print("ORDERS CONNECTION: FAILED")
        print("Raw API errors suppressed.")

    print()
    print("NO ORDERS PLACED OR MODIFIED")
    print("API KEYS AND SECRET NOT PRINTED")


if __name__ == "__main__":
    main()
