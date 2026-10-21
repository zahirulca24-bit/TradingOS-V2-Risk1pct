import { apiUrl, API_ROOT } from "./api";
import { useState, useEffect } from "react";
import {
  Wallet, ShieldCheck, Activity, WifiOff,
  CheckCircle2, AlertCircle, Lock, Layers, RefreshCw
} from "lucide-react";
import "./App.css";

type OpenPositionItem = {
  symbol: string;
  position_idx: string;
  size: number;
  side: string;
  entry_price: number | null;
  mark_price?: number | null;
  unrealised_pnl?: number | null;
  sl?: number | null;
  tp?: number | null;
  created_at?: string | null;
};

type DemoAccountResponse = {
  connected: boolean;
  environment: string;
  account_type: string;
  total_equity: number | null;
  available_balance: number | null;
  equity_unit: string;
  risk_percent: number;
  risk_budget_usd: number | null;
  max_open_and_pending: number;
  open_positions: number | null;
  open_positions_list: OpenPositionItem[];
  pending_orders: number | null;
  pending_entry_orders: number | null;
  occupied_slots: number | null;
  remaining_slots: number | null;
  positions_error: boolean;
  orders_error: boolean;
  trading_enabled: boolean;
  real_trading_enabled: boolean;
  execution_status: string;
  risk_health: string;
  updated_at: string;
};

type RiskHealthResponse = {
  status: "VALID" | "DEGRADED" | "UNAVAILABLE" | string;
  environment: string;
  account_connected: boolean;
  cycle_fresh: boolean;
  total_equity: number | null;
  risk_budget_usd: number | null;
  occupied_slots: number | null;
  remaining_slots: number | null;
  mode: string;
  trading_enabled: boolean;
  order_execution: string;
  reasons: string[];
};

function formatCurrency(val: number | null | undefined): string {
  if (val === null || val === undefined || !Number.isFinite(val)) {
    return "?";
  }
  return "$" + val.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }) + " USD";
}

export default function DemoTradingPage() {
  const [account, setAccount] = useState<DemoAccountResponse | null>(null);
  const [riskHealth, setRiskHealth] = useState<RiskHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function fetchDemoData() {
      try {
        const [accRes, riskRes] = await Promise.allSettled([
          fetch(apiUrl("/demo/account"), { cache: "no-store" }),
          fetch(apiUrl("/demo/risk-health"), { cache: "no-store" }),
        ]);

        if (!active) return;

        if (accRes.status === "fulfilled" && accRes.value.ok) {
          const accData = (await accRes.value.json()) as DemoAccountResponse;
          setAccount(accData);
          setError(null);
        } else {
          setAccount(null);
          const errDetail = accRes.status === "fulfilled"
            ? `HTTP ${accRes.value.status}`
            : (accRes.reason instanceof Error ? accRes.reason.message : "Connection failed");
          setError(errDetail);
        }

        if (riskRes.status === "fulfilled" && riskRes.value.ok) {
          const rData = (await riskRes.value.json()) as RiskHealthResponse;
          setRiskHealth(rData);
        } else {
          setRiskHealth(null);
        }

        setLoading(false);
      } catch (err: unknown) {
        if (active) {
          setError(err instanceof Error ? err.message : "Backend unavailable");
          setAccount(null);
          setRiskHealth(null);
          setLoading(false);
        }
      }
    }

    void fetchDemoData();
    const timer = setInterval(() => {
      void fetchDemoData();
    }, 15000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const connected = account?.connected === true && account?.environment === "BYBIT_DEMO";
  const positions = account?.open_positions_list ?? [];
  const occupiedSlots = account?.occupied_slots ?? (riskHealth?.occupied_slots ?? null);
  const remainingSlots = account?.remaining_slots ?? (riskHealth?.remaining_slots ?? null);

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">BYBIT UNIFIED DEMO</div>
          <h1>Demo Trading</h1>
          <p>
            Read-only inspection of Bybit Unified Demo balance, open positions, pending orders, and 1% risk capacity.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <span className="outline-pill">
            {loading ? (
              "CONNECTING..."
            ) : connected ? (
              <><CheckCircle2 size={13} style={{ color: "#4ade80" }} /> BYBIT DEMO CONNECTED</>
            ) : (
              <><WifiOff size={13} style={{ color: "#f87171" }} /> DEMO DISCONNECTED</>
            )}
          </span>
          <span className="outline-pill" style={{ borderColor: "#22c55e44", color: "#4ade80" }}>
            <Activity size={13} /> BYBIT DEMO EXECUTION ENABLED
          </span>
        </div>
      </div>

      {/* Execution Safety Banner */}
      <div className="info-note" style={{ marginBottom: "24px", background: "#0c1510", borderColor: "#22c55e33" }}>
        <ShieldCheck size={18} style={{ color: "#4ade80", flexShrink: 0 }} />
        <div>
          <strong>Autonomous 1% Risk Execution: </strong>
          Automatic Bybit Demo execution is active with strict 1% equity guardrail per trade and max 3 exposure slots.
        </div>
      </div>

      {/* Disconnected / Error Banner */}
      {!loading && !connected && (
        <div className="panel" style={{ padding: "24px", marginBottom: "24px", borderColor: "#ef444444", background: "#100808" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", color: "#f87171", fontWeight: 600, marginBottom: "8px" }}>
            <WifiOff size={20} />
            Demo Account API Unavailable ({error ?? "Not connected"})
          </div>
          <p style={{ color: "#9299a3", fontSize: "13px", margin: 0, lineHeight: "1.6" }}>
            The backend demo API could not be reached or Bybit Demo credentials are unconfigured.
            Check that <code>backend/.env</code> contains valid <code>BYBIT_DEMO=true</code>, <code>BYBIT_API_KEY</code>,
            and <code>BYBIT_API_SECRET</code>, and ensure the backend is running at <code>{API_ROOT}</code>.
          </p>
        </div>
      )}

      {/* Stat Cards Grid */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-top">
            <span>Demo Total Equity</span>
            <Wallet size={18} />
          </div>
          <div className="stat-value">
            {loading ? "..." : connected ? formatCurrency(account?.total_equity) : "?"}
          </div>
          <div className="stat-note">
            {connected ? "Bybit Demo Unified Account" : "Account unavailable"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Available Balance</span>
            <Activity size={18} />
          </div>
          <div className="stat-value">
            {loading ? "..." : connected ? formatCurrency(account?.available_balance) : "?"}
          </div>
          <div className="stat-note">
            {connected ? "Unencumbered margin balance" : "Balance unavailable"}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>1% Risk Budget</span>
            <ShieldCheck size={18} />
          </div>
          <div className="stat-value" style={{ color: "#4ade80" }}>
            {loading ? "..." : connected ? formatCurrency(account?.risk_budget_usd) : "?"}
          </div>
          <div className="stat-note">
            Strictly 1.0% equity per trade leg
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-top">
            <span>Exposure Capacity</span>
            <Layers size={18} />
          </div>
          <div className="stat-value" style={{
            color: occupiedSlots !== null && occupiedSlots >= 3 ? "#f87171" : "#4ade80"
          }}>
            {loading ? "..." : occupiedSlots !== null ? `${occupiedSlots} / 3 Occupied` : "?"}
          </div>
          <div className="stat-note">
            {remainingSlots !== null ? `${remainingSlots} slots remaining (Max 3)` : "Capacity unverified"}
          </div>
        </div>
      </div>

      {/* Main Panels Grid */}
      <div className="dashboard-grid" style={{ marginTop: "24px" }}>
        {/* Active Open Positions Panel */}
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Active Positions (Read-Only)</h2>
              <p>Paginated Bybit Unified Linear perpetual positions</p>
            </div>
            <span className="subtle-tag">
              {connected ? `${positions.length} OPEN` : "UNAVAILABLE"}
            </span>
          </div>

          {connected && positions.length > 0 ? (
            <div className="table-scroll">
              <table className="market-table">
                <thead>
                  <tr>
                    <th>OPEN TIME</th>
                    <th>SYMBOL</th>
                    <th>SIDE</th>
                    <th>SIZE</th>
                    <th>ENTRY</th>
                    <th>MARK PRICE</th>
                    <th>STOP LOSS (SL)</th>
                    <th>TAKE PROFIT (TP)</th>
                    <th>UNREALISED P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos, idx) => {
                    const pnl = pos.unrealised_pnl;
                    const isWin = pnl !== null && pnl !== undefined && pnl >= 0;
                    return (
                      <tr key={`${pos.symbol}-${pos.position_idx}-${idx}`}>
                        <td style={{ fontSize: "11px", color: "#888", whiteSpace: "nowrap" }}>
                          {pos.created_at || "—"}
                        </td>
                        <td className="symbol-cell">
                          <span className="symbol-dot" />
                          <strong>{pos.symbol}</strong>
                        </td>
                        <td>
                          <span style={{
                            color: pos.side?.toUpperCase() === "BUY" ? "#4ade80" : "#f87171",
                            fontWeight: 600
                          }}>
                            {pos.side?.toUpperCase() === "BUY" ? "LONG 🟢" : pos.side?.toUpperCase() === "SELL" ? "SHORT 🔴" : pos.side}
                          </span>
                        </td>
                        <td>{pos.size}</td>
                        <td>{pos.entry_price ? `$${pos.entry_price.toLocaleString()}` : "—"}</td>
                        <td>{pos.mark_price ? `$${pos.mark_price.toLocaleString()}` : "—"}</td>
                        <td style={{ color: "#ef4444", fontWeight: 500 }}>
                          {pos.sl ? `$${pos.sl.toLocaleString()}` : "—"}
                        </td>
                        <td style={{ color: "#22c55e", fontWeight: 500 }}>
                          {pos.tp ? `$${pos.tp.toLocaleString()}` : "—"}
                        </td>
                        <td>
                          {pnl !== null && pnl !== undefined ? (
                            <span style={{
                              fontWeight: 700,
                              color: isWin ? "#22c55e" : "#ef4444",
                              background: isWin ? "rgba(34, 197, 94, 0.12)" : "rgba(239, 68, 68, 0.12)",
                              padding: "3px 8px",
                              borderRadius: "4px",
                              display: "inline-block"
                            }}>
                              {isWin ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`} USD
                            </span>
                          ) : (
                            <span style={{ color: "#888" }}>—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ padding: "32px", textAlign: "center", color: "#888", fontSize: "13px" }}>
              <Layers size={28} style={{ color: "#444", margin: "0 auto 10px" }} />
              <div style={{ color: "#ccc", fontWeight: 500, marginBottom: "4px" }}>
                {connected ? "No Open Positions" : "Positions Unavailable"}
              </div>
              <p style={{ margin: 0, color: "#666", fontSize: "12px" }}>
                {connected
                  ? "Bybit Demo account has 0 active linear positions currently occupied."
                  : "Connect to Bybit Demo to inspect active position legs."}
              </p>
            </div>
          )}
        </section>

        {/* Risk Health & Capacity Panel */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Exposure &amp; Capacity Guardrails</h2>
              <p>3-Slot constraint &amp; dry-run verification</p>
            </div>
            <span className="subtle-tag" style={{
              color: riskHealth?.status === "VALID" ? "#4ade80" : riskHealth?.status === "DEGRADED" ? "#fbbf24" : "#f87171"
            }}>
              {riskHealth?.status ?? "UNKNOWN"}
            </span>
          </div>

          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Total Pending Orders</span>
              <b>{connected ? (account?.pending_orders ?? 0) : "?"}</b>
            </div>

            <div className="system-row">
              <span><ShieldCheck size={15} /> Pending Entry Commitments</span>
              <b>{connected ? (account?.pending_entry_orders ?? 0) : "?"}</b>
            </div>

            <div className="system-row">
              <span><Layers size={15} /> Maximum Exposure Slots</span>
              <b>3 Slots (Active Legs + Pending Entries)</b>
            </div>

            <div className="system-row">
              <span><Activity size={15} /> Account Sizing Fraction</span>
              <b>1.0% Equity Risk per Trade Leg</b>
            </div>

            <div className="system-row">
              <span><Activity size={15} /> Execution Status</span>
              <b style={{ color: "#4ade80" }}>ACTIVE / BYBIT DEMO (1% RISK)</b>
            </div>

            <div className="system-row">
              <span><RefreshCw size={15} /> Last Account Sync</span>
              <b style={{ color: "#888", fontSize: "11px" }}>
                {account?.updated_at ? account.updated_at.replace("T", " ").slice(0, 19) + " UTC" : "—"}
              </b>
            </div>

            {riskHealth?.reasons && riskHealth.reasons.length > 0 && (
              <div style={{
                background: "#180a0a",
                border: "1px solid #ef444433",
                borderRadius: "6px",
                padding: "12px",
                fontSize: "12px",
                color: "#f87171"
              }}>
                <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                  <AlertCircle size={14} style={{ verticalAlign: "middle", marginRight: "5px" }} />
                  Operational Risk Warnings:
                </div>
                <ul style={{ margin: 0, paddingLeft: "18px" }}>
                  {riskHealth.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
