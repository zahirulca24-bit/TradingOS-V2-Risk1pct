# TradingOS V2 — Full-source integrated baseline

এই ZIP-এ **মূল Source + Phase 1–3 একসঙ্গে** আছে। এটি আলাদা Phase installer নয়।

## আছে
- FastAPI backend, React/Vite dark UI, Bybit public 15m/1H closed candles
- Crypto-only directional universe (allowlist), 15m scheduler, single committed scanner cycle
- Liquidity sweep + pin bar/engulfing + hypothetical Entry/SL/TP/RR planning
- Opportunity Top 10, saved chart evidence, Dashboard with authenticated read-only Demo Wallet
- Offline Python tests, browser UI, startup scripts

## পুরোপুরি শেষ নয় (সতর্কতা)
- Neon/PostgreSQL trade journal integration, Telegram delivery, daily realized P&L হিসাব
- Manual chart drawing tools, precise support/resistance rectangles, full pattern-confirmation UX
- Safeguarded demo order placement + exchange-side idempotency/risk reconciliation
- Windows background service/24×7 recovery and deployment

**No order placement code is enabled.** এটি development/read-only application, live-ready trading bot নয়।

প্রথমবার `npm install` করতে Internet লাগবে। Existing `backend/.env` ZIP-এ নেই।

Demo risk dry-run update: equity risk fixed at 1%; 3 open+pending maximum; 43 Python tests. The read-only GET endpoints `/demo/risk-policy` and `/demo/risk-preview` do NOT execute orders. Demo automatic execution is NOT implemented or enabled. Full frontend build and real Bybit connectivity must be checked on the user's own computer.
