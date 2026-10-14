"""Conservative cryptocurrency-only Bybit USDT linear perpetual universe.

Fail closed on unrecognized base assets. Classification is an explicit reviewed
allowlist, *not* a claim that a Bybit API field certifies asset class.
"""

import math

from universe import get_bybit_data

# Reviewed base assets. Tokens not on this list are excluded until reviewed.
CRYPTO_BASES = frozenset("""
BTC ETH SOL XRP BNB DOGE ADA AVAX LINK LTC BCH DOT ATOM UNI AAVE ETC FIL
NEAR TRX XLM XMR TON HBAR ALGO ICP VET APT SUI ARB OP INJ SEI TIA PEPE
SHIB BONK FLOKI WIF PENGU TRUMP FARTCOIN POPCAT RENDER RENDER RNDR FET
TAO RLC HYPE ENA ONDO PYTH GRASS STRK W JUP JTO JASMY SAND MANA AXS
GALA CHZ CRV LDO MKR SKY COMP SNX SUSHI YFI ZRX 1INCH BLUR DYDX
GMX PENDLE ENJ KAVA RUNE EGLD FLOW MINA ROSE KSM ZEC DASH ZIL IOTA
NEO QTUM XTZ EOS NEO ZRO EIGEN ETHFI WLD AR RSR KAS STX IMX GRT
LPT ENS MASK ANKR COTI SSV ILV CFX CELO FTM SONIC S POL APE ASTR
KNC UMA BAL BICO API3 ACH C98 OCEAN NMR ORDI SATS NOT DOGS MEW
BOME PEOPLE MEME MOODENG PNUT ACT NEIRO BRETT TURBO DEGEN GOAT
VIRTUAL AI16Z AIXBT COOKIE MOVE ZK ZETA OM CHR ONE HOT IOST YGG
ARKM ALT PORTAL DYM ACE NFP BAKE CAKE TWT LRC IDEX LINA FLM
MAV MAVIA PIXEL BIGTIME SUPER HIGH MAGIC IMX FXS RDNT STG HFT
ANKR GNO RAY ORCA DRIFT KMNO IO ZBCN ATH ZENT RON CATI HMSTR
BARK REZ SAGA LISTA BANANA SLERF MYRO BSV BTT BERA IP KAITO
1000PEPE 1000BONK 1000SHIB 1000FLOKI 1000RATS 1000LUNC 1000XEC
1000SATS 1000TURBO 1000MOG 1000NEIRO
""".split())

# Do not inadvertently allow tokenized equities, metals, oil, index products.
NON_CRYPTO_BASES = frozenset("""
XAU XAG GOLD SILVER OIL CL UKOIL USOIL BRENT WTI
NVDA TSLA AAPL MSFT AMZN GOOGL GOOG META AMD INTC
SOXL SQQQ TQQQ SPY QQQ SPX NDX DJI DIA
MU KLAC PANW SNDK SKHYNIX COIN MSTR PLTR NFLX
""".split())


def eligible_instrument(item):
    if not isinstance(item, dict):
        return False
    base = str(item.get("baseCoin") or "").upper()
    symbol = str(item.get("symbol") or "").upper()
    return (
        base in CRYPTO_BASES
        and base not in NON_CRYPTO_BASES
        and symbol == base + "USDT"
        and item.get("status") == "Trading"
        and item.get("contractType") == "LinearPerpetual"
        and item.get("quoteCoin") == "USDT"
        and item.get("settleCoin") == "USDT"
    )


def get_crypto_tickers():
    allowed = set()
    cursor = ""
    visited = set()
    while True:
        params = {"category": "linear", "status": "Trading", "limit": 500}
        if cursor:
            params["cursor"] = cursor
        result = get_bybit_data("/v5/market/instruments-info", params)
        for item in result.get("list", []):
            if eligible_instrument(item):
                allowed.add(item["symbol"])
        nxt = result.get("nextPageCursor") or ""
        if not nxt:
            break
        if nxt == cursor or nxt in visited:
            raise RuntimeError("Repeated instruments pagination cursor")
        visited.add(nxt)
        cursor = nxt

    if not allowed:
        raise RuntimeError("No recognized crypto linear perpetual instruments")
    tickers = get_bybit_data("/v5/market/tickers", {"category": "linear"})
    ranked = []
    for item in tickers.get("list", []):
        symbol = item.get("symbol")
        if symbol not in allowed:
            continue
        try:
            turnover = float(item.get("turnover24h") or 0)
        except (ValueError, TypeError):
            continue
        if math.isfinite(turnover) and turnover > 0:
            ranked.append({"symbol": symbol, "turnover24h": turnover})
    ranked.sort(key=lambda item: (-item["turnover24h"], item["symbol"]))
    if not ranked:
        raise RuntimeError("No crypto futures tickers with valid turnover")
    return ranked
