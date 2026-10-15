-- TradingOS V2 — Neon PostgreSQL DDL Migration
-- Strictly DRY_RUN_ONLY, research evidence only. Zero order execution.

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
