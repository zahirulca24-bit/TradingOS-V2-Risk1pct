# TradingOS V2 — একটি Full-Source ZIP (Phase 1–3 merged)

**এই প্যাকেজ আলাদা Phase-ভিত্তিক Patch নয়।** Original Source Audit এবং Phase 1–3-এর Source একত্রিত করা হয়েছে। `E:\TradingOS-V2`-এ সরাসরি Overwrite না করে নতুন `E:\TradingOS-V2-Full` Folder-এ Install করো।

## কীভাবে চালাবে (Windows PowerShell)

1. ZIP Downloads-এ রেখে Extract করো:
   ```powershell
   Expand-Archive -LiteralPath "$HOME\Downloads\TradingOS_V2_Full_Source.zip" -DestinationPath 'E:\TradingOS-V2-Full-Extracted' -Force
   ```
2. নতুন Folder-এ Install করো (Python/Node/npm থাকতে হবে, প্রথমবার Internet লাগবে):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File 'E:\TradingOS-V2-Full-Extracted\Setup-TradingOS.ps1' -ProjectRoot 'E:\TradingOS-V2-Full'
   ```
3. তোমার **নিজের PC-তে** পুরোনো `.env` Copy করো, Chat-এ পাঠাবে না:
   ```powershell
   Copy-Item 'E:\TradingOS-V2\backend\.env' 'E:\TradingOS-V2-Full\backend\.env'
   ```
4. Test চালাও:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File 'E:\TradingOS-V2-Full\Verify-TradingOS.ps1' -ProjectRoot 'E:\TradingOS-V2-Full'
   ```
5. চালাও (Scheduler-সহ):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File 'E:\TradingOS-V2-Full\Start-TradingOS.ps1' -StartScheduler
   ```

Backend API `http://127.0.0.1:8765/health`, Frontend `http://127.0.0.1:5173`। প্রথম সম্পূর্ণ Candle Cycle প্রকাশ না হওয়া পর্যন্ত `/scanner/cycle` API-তে 503 স্বাভাবিক।

## Verified here (offline)
- Python 31 automated unit/contract tests; syntax pass
- No private credentials included, no trading endpoints enabled

## Only verify on your PC
- React/Vite production build (npm dependencies not available in packaging environment)
- Bybit authenticated demo account / Telegram / Neon access
- Genuine Bybit scanner timing and HTTP connectivity

## Not yet implemented (important)
Neon persistence, Telegram alert delivery, P&L history calculation, manual chart drawing, and safe demo order execution are **not completed**. This is a functional **read-only integrated baseline**, **not a completed automated demo trading bot**. Do not send orders through it.

## Demo Auto-Trading: 1% Risk DRY RUN (new)

This package configures **1% of Bybit Demo total equity as the maximum theoretical risk budget per planned trade**. For 1,000 USD demo equity, that is 10 USD; it does not mean 1% position size. This is only a read-only preview; **automatic demo orders and real orders are disabled**.

- `GET http://127.0.0.1:8765/demo/risk-policy`: the fixed 1% risk policy, max 3 open + pending positions and execution disabled.
- `GET http://127.0.0.1:8765/demo/risk-preview`: reads demo wallet and the most recent committed scanner cycle; shows qualifying plans and explicit reasons they are blocked.
- Risk preview has no API for placing, changing or canceling orders. It is deliberately blocked without exchange quantity/minimum rules, verified available margin and fees/slippage; a partial pending-order count is also blocked.
- Offline verification has **43 Python tests**, including 12 new risk checks. On your own Windows PC run `Verify-TradingOS.ps1` and inspect the report before considering future demo execution functionality.
- **Do not turn on demo execution yet**. Enabling it later requires authenticated demo-only order-routing code, exchange filter precision, complete paginated positions+orders, duplicate prevention, persistent idempotency, daily loss circuit breaker, and explicit separate consent.

Existing users: **Install as a fresh project to an empty folder**, following Setup/Verify scripts. The ZIP excludes credentials; copy `backend/.env` on your own machine only. Existing `E:\TradingOS-V2` is not changed by this package.
