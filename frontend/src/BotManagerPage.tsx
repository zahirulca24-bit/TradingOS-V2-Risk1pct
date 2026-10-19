import { apiUrl, API_ROOT } from "./api";
import { useState, useEffect } from "react";
import {
  Bot, ShieldCheck, Activity, Clock, WifiOff,
  AlertTriangle, CheckCircle2, RotateCcw, Database, Lock
} from "lucide-react";
import "./App.css";

type OperationalReport = {
  timestamp: string;
  overall_status: "HEALTHY" | "DEGRADED" | "UNHEALTHY" | string;
  supervisor: {
    status: string;
    pid: number | null;
    uptime_seconds: number;
    restarts: {
      backend: number;
      frontend: number;
      scanner: number;
    };
  };
  backend: {
    status: string;
    url: string;
    pid: number | null;
  };
  frontend: {
    status: string;
    url: string;
    pid: number | null;
  };
  scanner: {
    scheduler_status: "RUNNING" | "STOPPED" | "CONFLICT" | string;
    logical_count: number;
    pids?: number[];
    last_committed_cycle?: {
      cycle_id: string;
      candle_close_utc: string;
      committed_at: string;
      status: string;
    } | null;
    last_attempt?: {
      status: string;
      at: string;
      reason: string | null;
    } | null;
    freshness: "FRESH" | "STALE" | "UNKNOWN" | string;
    staleness_seconds: number | null;
    error_category?: string | null;
    last_failure_reason?: string | null;
  };
};

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function formatStaleness(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "Unknown";
  const mins = Math.floor(seconds / 60);
  if (mins <= 0) return "< 1 min ago";
  return `${mins} min ago`;
}

export default function BotManagerPage() {
  const [report, setReport] = useState<OperationalReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function fetchOperationalHealth() {
      try {
        const response = await fetch(apiUrl("/health/operational"), {
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = (await response.json()) as OperationalReport;
        if (active) {
          setReport(data);
          setError(null);
          setLoading(false);
        }
      } catch (err: unknown) {
        if (active) {
          setError(err instanceof Error ? err.message : "Backend connection failed");
          setReport(null);
          setLoading(false);
        }
      }
    }

    void fetchOperationalHealth();
    const interval = setInterval(() => {
      void fetchOperationalHealth();
    }, 10000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const isConnected = !error && report !== null;
  const schedulerStatus = report?.scanner?.scheduler_status ?? "UNKNOWN";
  const schedulerRunning = schedulerStatus === "RUNNING";
  const freshness = report?.scanner?.freshness ?? "UNKNOWN";
  const isFresh = freshness === "FRESH";
  const lastCycle = report?.scanner?.last_committed_cycle;
  const lastAttempt = report?.scanner?.last_attempt;
  const restarts = report?.supervisor?.restarts;

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">AUTOMATION &amp; MONITORING</div>
          <h1>Automation &amp; Scanner Monitor</h1>
          <p>
            Supervise background scanner scheduling, 15-minute cycles, data freshness, and supervisor lifecycle.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <span className="outline-pill">
            {loading ? (
              "CONNECTING..."
            ) : isConnected && schedulerRunning ? (
              <><CheckCircle2 size={13} style={{ color: "#4ade80" }} /> SCHEDULER ACTIVE (15M)</>
            ) : isConnected ? (
              <><AlertTriangle size={13} style={{ color: "#fbbf24" }} /> SCHEDULER {schedulerStatus}</>
            ) : (
              <><WifiOff size={13} style={{ color: "#f87171" }} /> BACKEND OFFLINE</>
            )}
          </span>
          <span className="outline-pill" style={{ borderColor: "#059669", color: "#34d399", background: "rgba(16, 185, 129, 0.1)" }}>
            <Activity size={13} /> BYBIT DEMO EXECUTION ENABLED
          </span>
        </div>
      </div>

      {/* Safety Notice Banner */}
      <div className="info-note" style={{ marginBottom: "24px", background: "rgba(16, 185, 129, 0.08)", borderColor: "rgba(16, 185, 129, 0.3)" }}>
        <ShieldCheck size={18} style={{ color: "#34d399", flexShrink: 0 }} />
        <div>
          <strong style={{ color: "#34d399" }}>Autonomous Order Execution Active: </strong>
          TradingOS V2 automated Bybit Demo execution is active.
          Qualified <strong>PLAN_READY</strong> signals (RR &ge; 1.5) are automatically executed as Market orders with attached SL/TP within a strict 1% risk budget.
        </div>
      </div>

      {/* Disconnected State Alert */}
      {!loading && !isConnected && (
        <div className="panel" style={{ padding: "24px", marginBottom: "24px", borderColor: "#ef444444", background: "#100808" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", color: "#f87171", fontWeight: 600, marginBottom: "8px" }}>
            <WifiOff size={20} />
            Backend Unavailable ({API_ROOT})
          </div>
          <p style={{ color: "#9299a3", fontSize: "13px", margin: 0, lineHeight: "1.6" }}>
            Unable to connect to the TradingOS backend API. Ensure the supervisor is active via
            <code> Launch-TradingOS.ps1</code>. The operational health metrics and scanner scheduler status
            will automatically refresh once the backend resumes.
          </p>
        </div>
      )}

      {/* Stat Cards */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-top">
            <span>Scheduler Status</span>
            <Bot size={18} />
          </div>
          <div className="stat-value" style={{
            color: schedulerRunning ? "#4ade80" : isConnected ? "#fbbf24" : "#666"
          }}>
            {loading ? "..." : isConnected ? schedulerStatus : "OFFLINE"}
          </div>
          <div className="stat-note">
            {isConnected
              ? `${report?.scanner?.logical_count ?? 0} logical instance (Mutual Exclusion)`
              : "Backend unreachable"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Scan Freshness</span>
            <Activity size={18} />
          </div>
          <div className="stat-value" style={{
            color: isFresh ? "#4ade80" : freshness === "STALE" ? "#f87171" : "#666"
          }}>
            {loading ? "..." : isConnected ? freshness : "UNKNOWN"}
          </div>
          <div className="stat-note">
            {isConnected
              ? `Age: ${formatStaleness(report?.scanner?.staleness_seconds)}`
              : "Freshness unverified"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Latest Cycle</span>
            <Database size={18} />
          </div>
          <div className="stat-value" style={{ fontSize: "16px", letterSpacing: "0.5px" }}>
            {loading ? "..." : (lastCycle?.cycle_id ?? "NONE")}
          </div>
          <div className="stat-note">
            {lastCycle?.candle_close_utc ? `Close: ${lastCycle.candle_close_utc.replace("+00:00", "Z")}` : "No cycle committed"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Supervisor Health</span>
            <Clock size={18} />
          </div>
          <div className="stat-value" style={{
            color: report?.overall_status === "HEALTHY" ? "#4ade80" : isConnected ? "#fbbf24" : "#666"
          }}>
            {loading ? "..." : (report?.overall_status ?? "OFFLINE")}
          </div>
          <div className="stat-note">
            {isConnected ? `Uptime: ${formatUptime(report?.supervisor?.uptime_seconds ?? 0)}` : "Supervisor offline"}
          </div>
        </div>
      </div>

      {/* Main Operational Diagnostic Panels */}
      <div className="dashboard-grid" style={{ marginTop: "24px" }}>
        {/* Left: Scheduler & Cadence Architecture */}
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Scheduler Architecture &amp; Cadence</h2>
              <p>Automated 15-minute background cycle synchronization</p>
            </div>
            <span className="subtle-tag">SUPERVISOR</span>
          </div>

          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><Bot size={15} /> Logical Instance Lock</span>
              <b>
                {isConnected && report?.scanner?.logical_count === 1 ? (
                  <span style={{ color: "#4ade80" }}>Exclusive Single Instance (pipeline.lock)</span>
                ) : isConnected && (report?.scanner?.logical_count ?? 0) > 1 ? (
                  <span style={{ color: "#f87171" }}>CONFLICT: Multiple Instances</span>
                ) : (
                  <span>Unverified</span>
                )}
              </b>
            </div>

            <div className="system-row">
              <span><Clock size={15} /> Cadence Boundary</span>
              <b>15-Minute UTC intervals (:00, :15, :30, :45)</b>
            </div>

            <div className="system-row">
              <span><Activity size={15} /> Scan Target Universe</span>
              <b>50 Pairs (1H trend + 15m liquidity sweep + pattern)</b>
            </div>

            <div className="system-row">
              <span><RotateCcw size={15} /> Bounded Retries &amp; Backoff</span>
              <b>
                {restarts ? (
                  `Restarts: Backend (${restarts.backend}), Frontend (${restarts.frontend}), Scanner (${restarts.scanner})`
                ) : (
                  "N/A"
                )}
              </b>
            </div>

            <div className="system-row">
              <span><ShieldCheck size={15} /> Rate-Limit Protection</span>
              <b>Preserves scheduler on Bybit 429; never terminates healthy scheduler</b>
            </div>

            <div className="system-row">
              <span><Lock size={15} /> Execution Status</span>
              <b style={{ color: "#fbbf24" }}>ORDER_EXECUTION_DISABLED (Dry Run Only)</b>
            </div>
          </div>
        </section>

        {/* Right: Operational Warnings & Last Attempt */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Attempt Diagnostics &amp; Warnings</h2>
              <p>Real-time error classification &amp; rate limits</p>
            </div>
            <span className="subtle-tag">HEALTH</span>
          </div>

          <div style={{ padding: "20px", display: "grid", gap: "16px", fontSize: "13px" }}>
            <div>
              <div style={{ color: "#9299a3", fontSize: "11px", marginBottom: "4px" }}>LAST SCAN ATTEMPT</div>
              <div style={{ fontWeight: 600 }}>
                {lastAttempt?.status ? (
                  <span style={{
                    color: lastAttempt.status === "success" ? "#4ade80" : "#f87171"
                  }}>
                    {lastAttempt.status.toUpperCase()}
                  </span>
                ) : (
                  "No recorded attempt"
                )}
              </div>
              <div style={{ color: "#777", fontSize: "12px", marginTop: "2px" }}>
                {lastAttempt?.at ? `Attempted at: ${lastAttempt.at}` : "Awaiting first run"}
              </div>
            </div>

            <div>
              <div style={{ color: "#9299a3", fontSize: "11px", marginBottom: "4px" }}>ERROR CATEGORY</div>
              <div>
                {report?.scanner?.error_category ? (
                  <span className="status-label" style={{ color: "#f87171", borderColor: "#f8717144" }}>
                    {report.scanner.error_category}
                  </span>
                ) : (
                  <span style={{ color: "#4ade80", fontWeight: 500 }}>
                    <CheckCircle2 size={13} style={{ verticalAlign: "middle", marginRight: "4px" }} />
                    None (Operational Nominal)
                  </span>
                )}
              </div>
            </div>

            {report?.scanner?.last_failure_reason && (
              <div style={{
                background: "#180a0a",
                border: "1px solid #ef444444",
                borderRadius: "6px",
                padding: "12px",
                fontSize: "12px",
                color: "#f87171"
              }}>
                <strong>Last Failure: </strong>
                {report.scanner.last_failure_reason}
              </div>
            )}

            <div style={{
              background: "#080808",
              border: "1px solid #202020",
              borderRadius: "6px",
              padding: "14px",
              fontSize: "12px",
              color: "#888"
            }}>
              <Lock size={14} style={{ verticalAlign: "middle", marginRight: "6px", color: "#666" }} />
              <strong>Bot Controls Notice: </strong>
              Controls to create bots, start independent loops, or toggle automated trading are omitted.
              The single supervised scheduler handles all research cycles safely.
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
