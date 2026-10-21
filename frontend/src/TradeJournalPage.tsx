import { API_ROOT } from "./api";
import React, { useState, useEffect } from "react";
import {
  BookOpen, ShieldCheck, FileBarChart, Download,
  Filter, ChevronLeft, ChevronRight, Calendar, Info, Zap
} from "lucide-react";

import "./TradeJournalPage.css";

interface JournalHealth {
  status: string;
  database_type: string;
  total_cycles_logged: number;
  total_signals_logged: number;
  total_dry_runs_logged: number;
  latest_cycle_id: string | null;
  latest_candle_close_utc: string | null;
  mode: string;
}

interface SignalItem {
  id: number;
  cycle_id: string;
  candle_close_utc: string;
  symbol: string;
  trend: string;
  direction: string | null;
  status: string;
  reason: string;
  pattern: string | null;
  sweep_level: number | null;
  sweep_time: number | null;
  entry_reference: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
  created_at: string;
}

interface DryRunItem {
  id: number;
  cycle_id: string;
  symbol: string;
  direction: string;
  status: string;
  reasons: string[];
  reason_messages: string[];
  equity_basis_usd: number | null;
  risk_budget_usd: number | null;
  proposed_quantity: string | null;
  qty_step: string | null;
  entry_reference: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
  estimated_fees_slippage_usd: number | null;
  total_projected_risk_usd: number | null;
  occupied_slots: number | null;
  remaining_slots: number | null;
  order_execution: string;
  evaluated_at: string;
}

interface ExecutedTradeItem {
  id: string | number;
  cycle_id: string;
  symbol: string;
  direction: string;
  qty: string;
  entry_price: number | null;
  exit_price: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
  risk_budget_usd: number | null;
  bybit_order_id: string | null;
  status: string;
  closed_pnl: number | null;
  created_at: string;
}

interface DailyReportItem {
  utc_date: string;
  cycles_committed: number;
  latest_cycle_id: string | null;
  latest_candle_close_utc: string | null;
  total_signal_observations: number;
  unique_symbols_examined: number;
  plan_ready_count: number;
  wait_count: number;
  rejected_count: number;
  bullish_observations: number;
  bearish_observations: number;
  dry_run_evaluations: number;
  dry_run_valid_count: number;
  dry_run_blocked_count: number;
  top_recurring_reasons: Array<{ reason: string; count: number }>;
  blocked_breakdown: Array<{ status: string; count: number }>;
  completeness_note: string;
  realized_pnl: string;
  executed_trades: string;
  win_rate: string;
  roi: string;
  disclosure: string;
}

const API_BASE = API_ROOT;

export default function TradeJournalPage({
  initialTab = "signals"
}: {
  initialTab?: "signals" | "dry-runs" | "reports" | "executions";
}) {
  const [activeTab, setActiveTab] = useState<"signals" | "dry-runs" | "reports" | "executions">(initialTab);

  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);
  const [health, setHealth] = useState<JournalHealth | null>(null);
  const [loading, setLoading] = useState(false);

  // Filters
  const [searchSymbol, setSearchSymbol] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [directionFilter, setDirectionFilter] = useState("ALL");
  const [dateFilter, setDateFilter] = useState("");

  // Signals State
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [signalsTotal, setSignalsTotal] = useState(0);
  const [signalsOffset, setSignalsOffset] = useState(0);
  const pageLimit = 50;

  // Dry Runs State
  const [dryRuns, setDryRuns] = useState<DryRunItem[]>([]);
  const [dryRunsTotal, setDryRunsTotal] = useState(0);
  const [dryRunsOffset, setDryRunsOffset] = useState(0);

  // Daily Reports State
  const [dailyReports, setDailyReports] = useState<DailyReportItem[]>([]);
  // Executed Trades State
  const [executedTrades, setExecutedTrades] = useState<ExecutedTradeItem[]>([]);
  const [execStats, setExecStats] = useState<{ total_closed: number; wins: number; losses: number; win_rate: number; total_realized_pnl: number }>({
    total_closed: 0, wins: 0, losses: 0, win_rate: 0, total_realized_pnl: 0
  });

  async function fetchExecutedTrades() {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/journal/executed-trades`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setExecutedTrades(data.executions || []);
        setExecStats({
          total_closed: data.total_closed || 0,
          wins: data.wins || 0,
          losses: data.losses || 0,
          win_rate: data.win_rate || 0,
          total_realized_pnl: data.total_realized_pnl || 0
        });
      }
    } catch {
      setExecutedTrades([]);
    } finally {
      setLoading(false);
    }
  }


  // Refresh Journal Health
  async function fetchHealth() {
    try {
      const res = await fetch(`${API_BASE}/journal/health`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setHealth(data);
      }
    } catch {
      // Offline fallback
    }
  }

  // Fetch Signals
  async function fetchSignals() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: pageLimit.toString(),
        offset: signalsOffset.toString(),
      });
      if (searchSymbol.trim()) params.set("symbol", searchSymbol.trim());
      if (statusFilter !== "ALL") params.set("status", statusFilter);
      if (directionFilter !== "ALL") params.set("direction", directionFilter);
      if (dateFilter) params.set("date", dateFilter);

      const res = await fetch(`${API_BASE}/journal/signals?${params.toString()}`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setSignals(data.signals || []);
        setSignalsTotal(data.total || 0);
      }
    } catch {
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }

  // Fetch Dry Runs
  async function fetchDryRuns() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: pageLimit.toString(),
        offset: dryRunsOffset.toString(),
      });
      if (searchSymbol.trim()) params.set("symbol", searchSymbol.trim());
      if (statusFilter !== "ALL") params.set("status", statusFilter);
      if (dateFilter) params.set("date", dateFilter);

      const res = await fetch(`${API_BASE}/journal/dry-runs?${params.toString()}`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setDryRuns(data.dry_runs || []);
        setDryRunsTotal(data.total || 0);
      }
    } catch {
      setDryRuns([]);
    } finally {
      setLoading(false);
    }
  }

  // Fetch Daily Reports
  async function fetchDailyReports() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (dateFilter) params.set("date", dateFilter);

      const res = await fetch(`${API_BASE}/journal/daily?${params.toString()}`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setDailyReports(data.reports || []);
      }
    } catch {
      setDailyReports([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchHealth();
  }, []);

  useEffect(() => {
    if (activeTab === "signals") {
      void fetchSignals();
    } else if (activeTab === "dry-runs") {
      void fetchDryRuns();
    } else if (activeTab === "reports") {
      void fetchDailyReports();
    } else if (activeTab === "executions") {
      void fetchExecutedTrades();
    }
  }, [activeTab, signalsOffset, dryRunsOffset, statusFilter, directionFilter, dateFilter]);

  // Handle search with debounce / trigger
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSignalsOffset(0);
    setDryRunsOffset(0);
    if (activeTab === "signals") void fetchSignals();
    if (activeTab === "dry-runs") void fetchDryRuns();
  };

  return (
    <div className="journal-workspace">
      {/* Header */}
      <header className="journal-header">
        <div>
          <div className="journal-eyebrow">RESEARCH AUDIT TRAIL — DRY RUN ONLY</div>
          <h1>Trade Journal & Daily Reports</h1>
          <p className="journal-header-desc">
            Durable, restart-safe SQLite research journal aggregating 15-minute scanner observations,
            1% dry-run position-sizing previews, and deterministic UTC-day operational reports.
          </p>
        </div>

        <div className="journal-header-meta">
          <div className="journal-health-badge">
            <span className={`journal-status-dot ${health?.status === "healthy" ? "" : "offline"}`} />
            <span>{health ? `${health.database_type}` : "SQLite Connecting..."}</span>
          </div>
          {health && (
            <div className="journal-counts-badge">
              <span><strong>{health.total_cycles_logged}</strong> cycles</span>
              <span>•</span>
              <span><strong>{health.total_signals_logged}</strong> signals</span>
              <span>•</span>
              <span><strong>{health.total_dry_runs_logged}</strong> dry-runs</span>
            </div>
          )}
        </div>
      </header>

      {/* Research Disclosures Banner */}
      <div className="journal-research-banner">
        <Info size={18} className="journal-banner-icon" />
        <div className="journal-banner-content">
          <h4>Strictly Research Evidence & Audit Trail</h4>
          <p>
            TradingOS V2 operates in <strong>DRY_RUN_ONLY</strong> research mode with order execution completely disabled.
            Signal observations represent periodic 15m scanner evaluation states, not unique trading opportunities.
            Performance metrics (realized P&L, win rate, ROI) are non-applicable (N/A) research disclosures.
          </p>
        </div>
      </div>

      {/* Navigation & Export Bar */}
      <div className="journal-nav-bar">
        <div className="journal-tabs">
          <button
            className={`journal-tab-btn ${activeTab === "signals" ? "active" : ""}`}
            onClick={() => setActiveTab("signals")}
          >
            <BookOpen size={16} />
            <span>Signals Journal</span>
          </button>
          <button
            className={`journal-tab-btn ${activeTab === "dry-runs" ? "active" : ""}`}
            onClick={() => setActiveTab("dry-runs")}
          >
            <ShieldCheck size={16} />
            <span>Dry-Run Previews</span>
          </button>
          <button
            className={`journal-tab-btn ${activeTab === "reports" ? "active" : ""}`}
            onClick={() => setActiveTab("reports")}
          >
            <FileBarChart size={16} />
            <span>Daily Reports</span>
          </button>
          <button
            className={`journal-tab-btn ${activeTab === "executions" ? "active" : ""}`}
            onClick={() => setActiveTab("executions")}
            style={{ color: activeTab === "executions" ? "#22c55e" : undefined }}
          >
            <Zap size={16} />
            <span>Executed Trades & PnL</span>
          </button>

        </div>

        <div className="journal-export-group">
          <a
            href={`${API_BASE}/journal/export/signals.csv`}
            download
            className="journal-export-btn"
            title="Download injection-safe Signals CSV"
          >
            <Download size={14} />
            <span>Export Signals CSV</span>
          </a>
          <a
            href={`${API_BASE}/journal/export/dry-runs.csv`}
            download
            className="journal-export-btn"
            title="Download injection-safe Dry-Runs CSV"
          >
            <Download size={14} />
            <span>Export Dry-Runs CSV</span>
          </a>
        </div>
      </div>

      {/* Tab Content: Signals */}
      {activeTab === "signals" && (
        <section>
          {/* Filters */}
          <form className="journal-filters" onSubmit={handleSearchSubmit}>
            <div className="journal-filter-item">
              <label>Search Symbol</label>
              <input
                type="text"
                placeholder="BTC, ETH..."
                className="journal-input"
                value={searchSymbol}
                onChange={(e) => setSearchSymbol(e.target.value)}
              />
            </div>

            <div className="journal-filter-item">
              <label>Plan Status</label>
              <select
                className="journal-select"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value);
                  setSignalsOffset(0);
                }}
              >
                <option value="ALL">All Statuses</option>
                <option value="PLAN_READY">PLAN_READY</option>
                <option value="WAIT">WAIT</option>
                <option value="REJECTED">REJECTED</option>
              </select>
            </div>

            <div className="journal-filter-item">
              <label>Direction</label>
              <select
                className="journal-select"
                value={directionFilter}
                onChange={(e) => {
                  setDirectionFilter(e.target.value);
                  setSignalsOffset(0);
                }}
              >
                <option value="ALL">All Directions</option>
                <option value="LONG">LONG (Bullish)</option>
                <option value="SHORT">SHORT (Bearish)</option>
              </select>
            </div>

            <div className="journal-filter-item">
              <label>Date (UTC)</label>
              <input
                type="date"
                className="journal-input"
                value={dateFilter}
                onChange={(e) => {
                  setDateFilter(e.target.value);
                  setSignalsOffset(0);
                }}
              />
            </div>

            <button type="submit" className="journal-export-btn" style={{ marginTop: "16px" }}>
              <Filter size={13} />
              <span>Apply Filter</span>
            </button>

            {dateFilter && (
              <button
                type="button"
                className="journal-export-btn"
                style={{ marginTop: "16px" }}
                onClick={() => {
                  setDateFilter("");
                  setSignalsOffset(0);
                }}
              >
                Clear Date
              </button>
            )}

            <div className="journal-filter-count">
              Showing {signals.length} of {signalsTotal} observations
            </div>
          </form>

          {/* Signals Table */}
          <div className="journal-table-container">
            {loading ? (
              <div className="journal-empty">
                <h3>Loading Signals...</h3>
              </div>
            ) : signals.length === 0 ? (
              <div className="journal-empty">
                <h3>No Signal Observations Found</h3>
                <p>No journal records match your current filter criteria.</p>
              </div>
            ) : (
              <table className="journal-table">
                <thead>
                  <tr>
                    <th>Cycle ID / Candle Close</th>
                    <th>Symbol</th>
                    <th>Trend / Direction</th>
                    <th>Plan Status</th>
                    <th>Reason / Decision</th>
                    <th>Pattern / Sweep</th>
                    <th>Entry Ref</th>
                    <th>Stop Loss</th>
                    <th>Take Profit</th>
                    <th>R:R</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((sig) => (
                    <tr key={sig.id}>
                      <td className="mono">
                        <div><strong>{sig.cycle_id}</strong></div>
                        <small style={{ color: "#71717a" }}>{sig.candle_close_utc}</small>
                      </td>
                      <td className="mono"><strong>{sig.symbol}</strong></td>
                      <td>
                        <span className={sig.trend === "BULLISH" ? "badge-bullish" : "badge-bearish"}>
                          {sig.trend} {sig.direction ? `(${sig.direction})` : ""}
                        </span>
                      </td>
                      <td>
                        <span
                          className={
                            sig.status === "PLAN_READY"
                              ? "badge-ready"
                              : sig.status === "WAIT"
                              ? "badge-wait"
                              : "badge-rejected"
                          }
                        >
                          {sig.status}
                        </span>
                      </td>
                      <td className="reason-cell">{sig.reason}</td>
                      <td>
                        {sig.pattern ? (
                          <div>
                            <span>{sig.pattern}</span>
                            {sig.sweep_level && (
                              <div style={{ fontSize: "10px", color: "#71717a" }}>
                                Sweep: {sig.sweep_level}
                              </div>
                            )}
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="mono">{sig.entry_reference != null ? sig.entry_reference : "—"}</td>
                      <td className="mono">{sig.sl != null ? sig.sl : "—"}</td>
                      <td className="mono">{sig.tp != null ? sig.tp : "—"}</td>
                      <td className="mono">
                        {sig.rr != null ? <strong>{sig.rr.toFixed(2)}R</strong> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
          <div className="journal-pagination">
            <button
              className="journal-page-btn"
              disabled={signalsOffset === 0 || loading}
              onClick={() => setSignalsOffset(Math.max(0, signalsOffset - pageLimit))}
            >
              <ChevronLeft size={14} style={{ display: "inline", verticalAlign: "middle" }} /> Previous
            </button>
            <span className="journal-page-info">
              Page {Math.floor(signalsOffset / pageLimit) + 1} of{" "}
              {Math.max(1, Math.ceil(signalsTotal / pageLimit))}
            </span>
            <button
              className="journal-page-btn"
              disabled={signalsOffset + pageLimit >= signalsTotal || loading}
              onClick={() => setSignalsOffset(signalsOffset + pageLimit)}
            >
              Next <ChevronRight size={14} style={{ display: "inline", verticalAlign: "middle" }} />
            </button>
          </div>
        </section>
      )}

      {/* Tab Content: Dry Runs */}
      {activeTab === "dry-runs" && (
        <section>
          {/* Filters */}
          <form className="journal-filters" onSubmit={handleSearchSubmit}>
            <div className="journal-filter-item">
              <label>Search Symbol</label>
              <input
                type="text"
                placeholder="BTC, ETH..."
                className="journal-input"
                value={searchSymbol}
                onChange={(e) => setSearchSymbol(e.target.value)}
              />
            </div>

            <div className="journal-filter-item">
              <label>Verification Status</label>
              <select
                className="journal-select"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value);
                  setDryRunsOffset(0);
                }}
              >
                <option value="ALL">All Statuses</option>
                <option value="DRY_RUN_VALID">DRY_RUN_VALID</option>
                <option value="BLOCKED_CAPACITY">BLOCKED_CAPACITY</option>
                <option value="BLOCKED_RISK">BLOCKED_RISK</option>
                <option value="BLOCKED_STALE">BLOCKED_STALE</option>
                <option value="BLOCKED_PRECISION">BLOCKED_PRECISION</option>
                <option value="BLOCKED_RR">BLOCKED_RR</option>
                <option value="BLOCKED_DATA">BLOCKED_DATA</option>
              </select>
            </div>

            <div className="journal-filter-item">
              <label>Date (UTC)</label>
              <input
                type="date"
                className="journal-input"
                value={dateFilter}
                onChange={(e) => {
                  setDateFilter(e.target.value);
                  setDryRunsOffset(0);
                }}
              />
            </div>

            <button type="submit" className="journal-export-btn" style={{ marginTop: "16px" }}>
              <Filter size={13} />
              <span>Apply Filter</span>
            </button>

            {dateFilter && (
              <button
                type="button"
                className="journal-export-btn"
                style={{ marginTop: "16px" }}
                onClick={() => {
                  setDateFilter("");
                  setDryRunsOffset(0);
                }}
              >
                Clear Date
              </button>
            )}

            <div className="journal-filter-count">
              Showing {dryRuns.length} of {dryRunsTotal} dry-run evaluations
            </div>
          </form>

          {/* Dry Runs Table */}
          <div className="journal-table-container">
            {loading ? (
              <div className="journal-empty">
                <h3>Loading Dry-Run Previews...</h3>
              </div>
            ) : dryRuns.length === 0 ? (
              <div className="journal-empty">
                <h3>No Dry-Run Evaluations Found</h3>
                <p>Dry-run position sizing is evaluated when PLAN_READY assets exist.</p>
              </div>
            ) : (
              <table className="journal-table">
                <thead>
                  <tr>
                    <th>Cycle ID / Evaluated At</th>
                    <th>Symbol & Dir</th>
                    <th>Dry-Run Status</th>
                    <th>Safety Verification Details</th>
                    <th>Equity Basis & 1% Budget</th>
                    <th>Proposed Qty (Step)</th>
                    <th>Entry / SL / TP</th>
                    <th>R:R</th>
                    <th>Est Fees + Slippage</th>
                    <th>Total Risk USD</th>
                    <th>Slots (Occ / Rem)</th>
                    <th>Execution Guard</th>
                  </tr>
                </thead>
                <tbody>
                  {dryRuns.map((dr) => (
                    <tr key={dr.id}>
                      <td className="mono">
                        <div><strong>{dr.cycle_id}</strong></div>
                        <small style={{ color: "#71717a" }}>{dr.evaluated_at.replace("T", " ").substring(0, 19)}</small>
                      </td>
                      <td className="mono">
                        <strong>{dr.symbol}</strong>
                        <span style={{ marginLeft: "6px" }} className={dr.direction === "LONG" ? "badge-bullish" : "badge-bearish"}>
                          {dr.direction}
                        </span>
                      </td>
                      <td>
                        <span className={dr.status === "DRY_RUN_VALID" ? "badge-valid" : "badge-blocked"}>
                          {dr.status}
                        </span>
                      </td>
                      <td className="reason-cell">
                        {(() => {
                          let rList: string[] = [];
                          if (Array.isArray(dr.reasons)) {
                            rList = dr.reasons;
                          } else if (typeof dr.reasons === "string") {
                            try {
                              const parsed = JSON.parse(dr.reasons);
                              rList = Array.isArray(parsed) ? parsed : [dr.reasons];
                            } catch {
                              rList = [dr.reasons];
                            }
                          }
                          return rList.length > 0 ? (
                            <div>
                              {rList.map((r, i) => (
                                <span key={i} style={{ display: "block", color: "#f87171", fontSize: "10px" }}>
                                  • {r}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span style={{ color: "#4ade80" }}>All safety checks passed</span>
                          );
                        })()}
                      </td>

                      <td className="mono">
                        <div>${dr.equity_basis_usd?.toFixed(2) ?? "—"}</div>
                        <small style={{ color: "#38bdf8" }}>Budget: ${dr.risk_budget_usd?.toFixed(2) ?? "—"}</small>
                      </td>
                      <td className="mono">
                        <div>{dr.proposed_quantity ?? "0"}</div>
                        <small style={{ color: "#71717a" }}>step: {dr.qty_step ?? "—"}</small>
                      </td>
                      <td className="mono">
                        <div>E: {dr.entry_reference ?? "—"}</div>
                        <small style={{ color: "#f87171" }}>SL: {dr.sl ?? "—"}</small>{" "}
                        <small style={{ color: "#4ade80" }}>TP: {dr.tp ?? "—"}</small>
                      </td>
                      <td className="mono">
                        {dr.rr != null ? <strong>{dr.rr.toFixed(2)}R</strong> : "—"}
                      </td>
                      <td className="mono">${dr.estimated_fees_slippage_usd?.toFixed(2) ?? "—"}</td>
                      <td className="mono">
                        <strong>${dr.total_projected_risk_usd?.toFixed(2) ?? "—"}</strong>
                      </td>
                      <td className="mono">
                        {dr.occupied_slots ?? "—"} / 3 <small style={{ color: "#71717a" }}>({dr.remaining_slots ?? "—"} left)</small>
                      </td>
                      <td>
                        <span className="tag-execution-disabled">ORDER_EXECUTION_DISABLED</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
          <div className="journal-pagination">
            <button
              className="journal-page-btn"
              disabled={dryRunsOffset === 0 || loading}
              onClick={() => setDryRunsOffset(Math.max(0, dryRunsOffset - pageLimit))}
            >
              <ChevronLeft size={14} style={{ display: "inline", verticalAlign: "middle" }} /> Previous
            </button>
            <span className="journal-page-info">
              Page {Math.floor(dryRunsOffset / pageLimit) + 1} of{" "}
              {Math.max(1, Math.ceil(dryRunsTotal / pageLimit))}
            </span>
            <button
              className="journal-page-btn"
              disabled={dryRunsOffset + pageLimit >= dryRunsTotal || loading}
              onClick={() => setDryRunsOffset(dryRunsOffset + pageLimit)}
            >
              Next <ChevronRight size={14} style={{ display: "inline", verticalAlign: "middle" }} />
            </button>
          </div>
        </section>
      )}

      {/* Tab Content: Daily Reports */}
      {activeTab === "reports" && (
        <section>
          {/* Date Selector */}
          <div className="journal-filters">
            <div className="journal-filter-item">
              <label>Filter Specific UTC Date</label>
              <input
                type="date"
                className="journal-input"
                value={dateFilter}
                onChange={(e) => setDateFilter(e.target.value)}
              />
            </div>
            {dateFilter && (
              <button
                type="button"
                className="journal-export-btn"
                style={{ marginTop: "16px" }}
                onClick={() => setDateFilter("")}
              >
                Show All Days
              </button>
            )}
          </div>

          {/* Daily Reports Cards */}
          <div className="daily-reports-list">
            {loading ? (
              <div className="journal-empty">
                <h3>Loading Daily Reports...</h3>
              </div>
            ) : dailyReports.length === 0 ? (
              <div className="journal-empty">
                <h3>No Daily Reports Logged</h3>
                <p>Committed cycle observations are aggregated by UTC calendar date.</p>
              </div>
            ) : (
              dailyReports.map((report) => (
                <div key={report.utc_date} className="daily-card">
                  {/* Card Header */}
                  <div className="daily-card-header">
                    <div className="daily-card-title">
                      <Calendar size={18} style={{ color: "#38bdf8" }} />
                      <span className="daily-date">{report.utc_date} UTC</span>
                    </div>
                    <div className="daily-completeness">
                      {report.completeness_note}
                    </div>
                  </div>

                  {/* Summary Metric Counters */}
                  <div className="daily-stats-grid">
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Cycles Committed</div>
                      <div className="daily-stat-value">{report.cycles_committed}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Signal Observations</div>
                      <div className="daily-stat-value">{report.total_signal_observations}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Unique Symbols</div>
                      <div className="daily-stat-value">{report.unique_symbols_examined}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Plan Ready</div>
                      <div className="daily-stat-value ready">{report.plan_ready_count}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Wait / Filtering</div>
                      <div className="daily-stat-value wait">{report.wait_count}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Rejected</div>
                      <div className="daily-stat-value rejected">{report.rejected_count}</div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Bullish / Bearish</div>
                      <div className="daily-stat-value" style={{ fontSize: "15px" }}>
                        <span style={{ color: "#4ade80" }}>{report.bullish_observations}</span> /{" "}
                        <span style={{ color: "#f87171" }}>{report.bearish_observations}</span>
                      </div>
                    </div>
                    <div className="daily-stat-box">
                      <div className="daily-stat-label">Dry-Runs (Valid/Block)</div>
                      <div className="daily-stat-value" style={{ fontSize: "15px" }}>
                        <span style={{ color: "#4ade80" }}>{report.dry_run_valid_count}</span> /{" "}
                        <span style={{ color: "#f87171" }}>{report.dry_run_blocked_count}</span>
                      </div>
                    </div>
                  </div>

                  {/* Recurring Reasons & Dry-Run Blocked Breakdown */}
                  <div className="daily-sections-grid">
                    <div className="daily-subpanel">
                      <h3>Top Recurring WAIT & REJECTED Reasons</h3>
                      {report.top_recurring_reasons && report.top_recurring_reasons.length > 0 ? (
                        <ul className="reasons-list">
                          {report.top_recurring_reasons.map((item, idx) => (
                            <li key={idx}>
                              <span>{item.reason}</span>
                              <span className="reason-count">{item.count}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p style={{ color: "#71717a", fontSize: "12px", margin: 0 }}>
                          No WAIT or REJECTED patterns observed for this date.
                        </p>
                      )}
                    </div>

                    <div className="daily-subpanel">
                      <h3>Dry-Run Safety Block Breakdown</h3>
                      {report.blocked_breakdown && report.blocked_breakdown.length > 0 ? (
                        <ul className="blocked-list">
                          {report.blocked_breakdown.map((item, idx) => (
                            <li key={idx}>
                              <span>{item.status}</span>
                              <span className="blocked-count">{item.count}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p style={{ color: "#71717a", fontSize: "12px", margin: 0 }}>
                          No blocked dry-run evaluations recorded for this date.
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Explicit Performance Disclosures */}
                  <div className="daily-performance-panel">
                    <div className="daily-perf-grid">
                      <div className="daily-perf-item">
                        <span className="daily-perf-label">Realized P&L</span>
                        <span className="daily-perf-val na">{report.realized_pnl}</span>
                      </div>
                      <div className="daily-perf-item">
                        <span className="daily-perf-label">Executed Trades</span>
                        <span className="daily-perf-val">0</span>
                      </div>
                      <div className="daily-perf-item">
                        <span className="daily-perf-label">Win Rate</span>
                        <span className="daily-perf-val na">{report.win_rate}</span>
                      </div>
                      <div className="daily-perf-item">
                        <span className="daily-perf-label">ROI Basis</span>
                        <span className="daily-perf-val na">{report.roi}</span>
                      </div>
                    </div>
                    <p className="daily-disclosure-note">
                      <strong>Scientific Research Audit Notice:</strong> {report.disclosure}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>
      )}

      {/* Tab Content: Executed Trades & PnL */}
      {activeTab === "executions" && (
        <section className="journal-content-panel">
          {/* Summary KPIs */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px", marginBottom: "24px" }}>
            <div style={{ background: "#111", border: "1px solid #262626", borderRadius: "8px", padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#888", textTransform: "uppercase", letterSpacing: "0.5px" }}>Total Realized P&L</div>
              <div style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px", color: execStats.total_realized_pnl >= 0 ? "#4ade80" : "#ef4444" }}>
                {execStats.total_realized_pnl >= 0 ? `+$${execStats.total_realized_pnl.toFixed(2)}` : `-$${Math.abs(execStats.total_realized_pnl).toFixed(2)}`} USD
              </div>
            </div>
            <div style={{ background: "#111", border: "1px solid #262626", borderRadius: "8px", padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#888", textTransform: "uppercase", letterSpacing: "0.5px" }}>Win Rate</div>
              <div style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px", color: "#38bdf8" }}>
                {execStats.win_rate}% <span style={{ fontSize: "12px", color: "#888", fontWeight: 400 }}>({execStats.wins}W / {execStats.losses}L)</span>
              </div>
            </div>
            <div style={{ background: "#111", border: "1px solid #262626", borderRadius: "8px", padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#888", textTransform: "uppercase", letterSpacing: "0.5px" }}>Closed Trades</div>
              <div style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px", color: "#fff" }}>
                {execStats.total_closed} <span style={{ fontSize: "12px", color: "#888", fontWeight: 400 }}>Positions</span>
              </div>
            </div>
            <div style={{ background: "#111", border: "1px solid #262626", borderRadius: "8px", padding: "16px" }}>
              <div style={{ fontSize: "11px", color: "#888", textTransform: "uppercase", letterSpacing: "0.5px" }}>Risk Guardrail</div>
              <div style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px", color: "#22c55e" }}>
                1.0% <span style={{ fontSize: "12px", color: "#888", fontWeight: 400 }}>Strict per trade</span>
              </div>
            </div>
          </div>

          {loading ? (
            <div style={{ padding: "40px", textAlign: "center", color: "#888" }}>Loading Executed Trades...</div>
          ) : executedTrades.length === 0 ? (
            <div style={{ padding: "40px", textAlign: "center", color: "#888" }}>No executed trades recorded yet.</div>
          ) : (
            <div className="journal-table-wrap">
              <table className="journal-table">
                <thead>
                  <tr>
                    <th>TIME (UTC)</th>
                    <th>SYMBOL</th>
                    <th>SIDE</th>
                    <th>STATUS</th>
                    <th>QTY</th>
                    <th>ENTRY</th>
                    <th>EXIT / TP</th>
                    <th>STOP LOSS</th>
                    <th>REALIZED PNL</th>
                    <th>BYBIT ORDER ID</th>
                  </tr>
                </thead>
                <tbody>
                  {executedTrades.map((t, idx) => (
                    <tr key={`${t.id}-${idx}`}>
                      <td style={{ fontSize: "12px", color: "#888" }}>{t.created_at ? t.created_at.slice(0, 19).replace("T", " ") : "—"}</td>
                      <td><strong>{t.symbol}</strong></td>
                      <td>
                        <span style={{ color: t.direction?.toUpperCase() === "LONG" ? "#4ade80" : "#f87171", fontWeight: 600 }}>
                          {t.direction}
                        </span>
                      </td>
                      <td>
                        <span style={{
                          padding: "2px 8px",
                          borderRadius: "4px",
                          fontSize: "11px",
                          fontWeight: 700,
                          background: t.status === "CLOSED_WON" ? "rgba(34, 197, 94, 0.15)" : t.status === "CLOSED_LOST" ? "rgba(239, 68, 68, 0.15)" : "rgba(56, 189, 248, 0.15)",
                          color: t.status === "CLOSED_WON" ? "#22c55e" : t.status === "CLOSED_LOST" ? "#ef4444" : "#38bdf8"
                        }}>
                          {t.status === "CLOSED_WON" ? "WON 🟢" : t.status === "CLOSED_LOST" ? "LOST 🔴" : "OPEN ⏳"}
                        </span>
                      </td>
                      <td>{t.qty}</td>
                      <td>${t.entry_price ? t.entry_price.toLocaleString() : "—"}</td>
                      <td>
                        {t.exit_price ? (
                          <span style={{ color: "#38bdf8" }}>${t.exit_price.toLocaleString()}</span>
                        ) : t.tp ? (
                          <span style={{ color: "#22c55e" }}>${t.tp.toLocaleString()}</span>
                        ) : "—"}
                      </td>
                      <td>${t.sl ? t.sl.toLocaleString() : "—"}</td>
                      <td>
                        {t.closed_pnl !== null && t.closed_pnl !== undefined ? (
                          <strong style={{ color: t.closed_pnl >= 0 ? "#22c55e" : "#ef4444" }}>
                            {t.closed_pnl >= 0 ? `+$${t.closed_pnl.toFixed(2)}` : `-$${Math.abs(t.closed_pnl).toFixed(2)}`}
                          </strong>
                        ) : (
                          <span style={{ color: "#888" }}>Running...</span>
                        )}
                      </td>
                      <td style={{ fontSize: "11px", color: "#666", fontFamily: "monospace" }}>
                        {t.bybit_order_id ? `${String(t.bybit_order_id).slice(0, 8)}...` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

    </div>
  );
}
