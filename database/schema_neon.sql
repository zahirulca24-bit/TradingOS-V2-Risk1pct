-- TradingOS V2 — Neon PostgreSQL DDL Migration
-- Strictly DRY_RUN_ONLY baseline with durable execution, risk, and control tables.

-- 1. Journal Cycles Table (Committed 15-Minute Market Scanner Cycles)
CREATE TABLE IF NOT EXISTS journal_cycles (
    cycle_id VARCHAR(32) PRIMARY KEY,
    candle_close_utc TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL,
    total_scanned INTEGER NOT NULL,
    directional_count INTEGER NOT NULL,
    neutral_excluded INTEGER NOT NULL,
    ready_count INTEGER NOT NULL,
    wait_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    schema_version VARCHAR(16) NOT NULL DEFAULT '1.0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Journal Signals Table (Evaluated Setup Signals)
CREATE TABLE IF NOT EXISTS journal_signals (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE,
    candle_close_utc TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    trend VARCHAR(16) NOT NULL,
    direction VARCHAR(16),
    status VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    pattern VARCHAR(64),
    sweep_level DOUBLE PRECISION,
    sweep_time BIGINT,
    entry_reference DOUBLE PRECISION,
    sl DOUBLE PRECISION,
    tp DOUBLE PRECISION,
    rr DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_journal_signals_cycle_symbol UNIQUE (cycle_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_signals_cycle ON journal_signals(cycle_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON journal_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_status ON journal_signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_direction ON journal_signals(direction);
CREATE INDEX IF NOT EXISTS idx_signals_candle_close ON journal_signals(candle_close_utc);

-- 3. Journal Dry Runs Table (1% Risk Engine Simulation Previews)
CREATE TABLE IF NOT EXISTS journal_dry_runs (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    status VARCHAR(64) NOT NULL,
    reasons JSONB,
    reason_messages JSONB,
    equity_basis_usd DOUBLE PRECISION,
    risk_budget_usd DOUBLE PRECISION,
    proposed_quantity VARCHAR(32),
    qty_step VARCHAR(32),
    entry_reference DOUBLE PRECISION,
    sl DOUBLE PRECISION,
    tp DOUBLE PRECISION,
    rr DOUBLE PRECISION,
    estimated_fees_slippage_usd DOUBLE PRECISION,
    total_projected_risk_usd DOUBLE PRECISION,
    occupied_slots INTEGER,
    remaining_slots INTEGER,
    order_execution VARCHAR(64) NOT NULL DEFAULT 'ORDER_EXECUTION_DISABLED',
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_journal_dry_runs_cycle_symbol UNIQUE (cycle_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_dry_runs_cycle ON journal_dry_runs(cycle_id);
CREATE INDEX IF NOT EXISTS idx_dry_runs_symbol ON journal_dry_runs(symbol);
CREATE INDEX IF NOT EXISTS idx_dry_runs_status ON journal_dry_runs(status);
CREATE INDEX IF NOT EXISTS idx_dry_runs_evaluated ON journal_dry_runs(evaluated_at);

-- 4. Durable Pipeline Cycles Table (Replaces ephemeral container local filesystem)
CREATE TABLE IF NOT EXISTS pipeline_cycles (
    cycle_id VARCHAR(32) PRIMARY KEY,
    candle_close_utc TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL,
    manifest JSONB NOT NULL,
    scanner_report JSONB NOT NULL,
    trade_plans_report JSONB NOT NULL,
    opportunities_report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_cycles_candle_close ON pipeline_cycles(candle_close_utc);

-- 5. Pipeline Meta Table (Active cycle pointer, last attempt diagnostics)
CREATE TABLE IF NOT EXISTS pipeline_meta (
    key VARCHAR(64) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Demo Executions Table (Durable Bybit Demo Order Execution Records)
CREATE TABLE IF NOT EXISTS demo_executions (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_price DOUBLE PRECISION,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    risk_budget_usd DOUBLE PRECISION NOT NULL,
    bybit_order_id VARCHAR(64),
    order_link_id VARCHAR(64) UNIQUE,
    status VARCHAR(32) NOT NULL,
    error_message TEXT,
    exit_notified INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_demo_executions_cycle ON demo_executions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_demo_executions_symbol ON demo_executions(symbol);
CREATE INDEX IF NOT EXISTS idx_demo_executions_link ON demo_executions(order_link_id);

-- 7. Journal Signal Tracking (Theoretical Signal Outcomes & Post-Mortem)
CREATE TABLE IF NOT EXISTS journal_signal_tracking (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    entry_reference DOUBLE PRECISION NOT NULL,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    pattern VARCHAR(64),
    candle_time BIGINT NOT NULL,
    candle_close_utc TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    exit_price DOUBLE PRECISION,
    exit_time TEXT,
    exit_candle_index INTEGER,
    post_mortem TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_signal_tracking UNIQUE (cycle_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_signal_tracking_status ON journal_signal_tracking(status);

-- 8. Order Intents (Persistent Pre-Execution Idempotency & Retry Tracking)
CREATE TABLE IF NOT EXISTS order_intents (
    id BIGSERIAL PRIMARY KEY,
    order_link_id VARCHAR(64) UNIQUE NOT NULL,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_reference DOUBLE PRECISION NOT NULL,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    risk_budget_usd DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    error_message TEXT,
    exchange_order_id VARCHAR(64),
    avg_fill_price DOUBLE PRECISION,
    cum_exec_qty VARCHAR(32),
    cum_exec_fee DOUBLE PRECISION,
    reconciled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_intents_cycle ON order_intents(cycle_id);
CREATE INDEX IF NOT EXISTS idx_order_intents_symbol ON order_intents(symbol);
CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status);

-- 9. Slot Reservations (Atomic Concurrency-Safe 3-Slot Capacity Enforcement)
CREATE TABLE IF NOT EXISTS slot_reservations (
    slot_id INTEGER PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    order_link_id VARCHAR(64) UNIQUE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RESERVED',
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    release_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_slot_reservations_status ON slot_reservations(status);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_link ON slot_reservations(order_link_id);

-- 10. System Controls (Persistent Emergency Execution Kill Switch & Flags)
CREATE TABLE IF NOT EXISTS system_controls (
    key VARCHAR(64) PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    triggered_by VARCHAR(64) NOT NULL DEFAULT 'SYSTEM',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_control_audit (
    id BIGSERIAL PRIMARY KEY,
    key VARCHAR(64) NOT NULL,
    old_state INTEGER,
    new_state INTEGER NOT NULL,
    reason TEXT,
    triggered_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sys_control_audit_key ON system_control_audit(key);

-- 11. Daily Risk Records (Daily 3% Drawdown Circuit Breaker & Performance Metrics)
CREATE TABLE IF NOT EXISTS daily_risk_records (
    trading_date VARCHAR(16) PRIMARY KEY,
    starting_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    current_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    peak_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    max_drawdown_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    drawdown_limit_pct DOUBLE PRECISION NOT NULL DEFAULT 3.0,
    circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_triggered_at TIMESTAMPTZ,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins_count INTEGER NOT NULL DEFAULT 0,
    losses_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 12. Closed Trades (Durable Exchange-Verified Closed Position PnL and Exit Events)
CREATE TABLE IF NOT EXISTS closed_trades (
    id BIGSERIAL PRIMARY KEY,
    close_id VARCHAR(128) UNIQUE NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    order_link_id VARCHAR(64),
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    closed_pnl DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    is_partial INTEGER NOT NULL DEFAULT 0,
    fill_count INTEGER DEFAULT 1,
    leverage VARCHAR(16),
    exchange_created_at TIMESTAMPTZ,
    exchange_updated_at TIMESTAMPTZ,
    fed_to_breaker INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON closed_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_closed_trades_order ON closed_trades(order_id);
CREATE INDEX IF NOT EXISTS idx_closed_trades_breaker ON closed_trades(fed_to_breaker);
CREATE INDEX IF NOT EXISTS idx_closed_trades_notified ON closed_trades(notified);

-- 13. Notification Outbox (Durable Process-Restart-Safe Alert Outbox)
CREATE TABLE IF NOT EXISTS notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_key VARCHAR(128) UNIQUE NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_event_type ON notification_outbox(event_type);

-- Backward-compatible column migrations for existing deployments
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS exchange_order_id VARCHAR(64);
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS avg_fill_price DOUBLE PRECISION;
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS cum_exec_qty VARCHAR(32);
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS cum_exec_fee DOUBLE PRECISION;
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;
