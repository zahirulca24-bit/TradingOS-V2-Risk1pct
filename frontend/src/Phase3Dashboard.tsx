import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Wallet,
  Activity,
  Bot,
  ChartCandlestick,
  ShieldCheck,
  Database,
  Search,
  Radio,
  ChevronRight,
  WifiOff,
  Scale,
  Lock,
  AlertTriangle,
  CheckCircle2,
  Layers,
  ArrowUpRight,
  ArrowDownRight
} from "lucide-react";
import {
  API_ROOT,
  NO_VALUE,
  candleText,
  countdownToClose,
  timeText,
  useCycleFeed,
  priceText
} from "./CycleData";

type DemoSummary = {
  connected: boolean;
  environment: string;
  total_equity: number | null;
  available_balance: number | null;
  risk_percent: number;
  risk_budget_usd: number | null;
  max_open_and_pending: number;
  open_positions: number | null;
  open_positions_list?: Array<{ symbol: string; size: number | null; side: string; entry_price: number | null }>;
  pending_orders: number | null;
  pending_entry_orders: number | null;
  occupied_slots: number | null;
  remaining_slots: number | null;
  positions_error: boolean;
  orders_error: boolean;
  orders_first_page_only: boolean;
  trading_enabled: boolean;
  real_trading_enabled: boolean;
  execution_status: string;
  risk_health: "VALID" | "DEGRADED" | "UNAVAILABLE";
  updated_at: string;
};

type DryRunCandidate = {
  symbol: string;
  direction: "LONG" | "SHORT";
  status: "DRY_RUN_VALID" | "WAIT" | "REJECTED" | "BLOCKED_RISK" | "BLOCKED_CAPACITY" | "BLOCKED_DATA" | "BLOCKED_STALE";
  reasons: string[];
  reason_messages: string[];
  risk_percent: number;
  risk_budget_usd: number | null;
  entry_reference: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
  proposed_quantity: string | null;
  qty_step: string | null;
  estimated_position_notional_usd: number | null;
  estimated_stop_loss_amount_usd: number | null;
  estimated_fees_and_slippage_usd: number | null;
  total_conservative_projected_risk_usd: number | null;
  risk_utilization_pct: number | null;
  existing_slots: number | null;
  remaining_slots: number | null;
  margin_required_usd: number | null;
  order_execution: string;
};

type RiskPreviewResponse = {
  environment: string;
  mode: string;
  trading_enabled: boolean;
  order_execution: string;
  cycle_id: string;
  candle_close_utc: string;
  cycle_fresh: boolean;
  account_fresh: boolean;
  risk_percent: number;
  equity_basis_usd: number | null;
  risk_budget_usd: number | null;
  occupied_slots: number | null;
  remaining_slots: number | null;
  ready_count: number;
  candidates: DryRunCandidate[];
  empty_message?: string;
  execution_note: string;
};

function Stat({
  label,
  value,
  note,
  icon: Icon
}: {
  label: string;
  value: string;
  note: string;
  icon: typeof Activity;
}) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span>{label}</span>
        <Icon size={18} />
      </div>
      <div className="stat-value">{value}</div>
      <div className="stat-note">{note}</div>
    </div>
  );
}

const detail: React.CSSProperties = { color: "#999", fontSize: 12 };

export default function Phase3Dashboard() {
  const { cycle, pipeline, error, freshness, now } = useCycleFeed();
  const [account, setAccount] = useState<DemoSummary | null>(null);
  const [accountError, setAccountError] = useState("Connecting to Demo API...");
  const [riskPreview, setRiskPreview] = useState<RiskPreviewResponse | null>(null);

  useEffect(() => {
    let active = true;
    let busy = false;
    let controller: AbortController | null = null;

    async function loadRiskData() {
      if (!active || busy) return;
      busy = true;
      controller = new AbortController();

      try {
        // 1. Fetch Demo Account (P4)
        const accRes = await fetch(`${API_ROOT}/demo/account`, {
          cache: "no-store",
          signal: controller.signal
        });
        if (!accRes.ok) throw new Error(`Demo API HTTP ${accRes.status}`);

        const accData: DemoSummary = await accRes.json();
        if (!accData.connected || accData.environment !== "BYBIT_DEMO") {
          throw new Error("Demo mode not confirmed");
        }

        if (active) {
          setAccount(accData);
          setAccountError("");
        }

        // 2. Fetch Risk Preview (P5)
        try {
          const prevRes = await fetch(`${API_ROOT}/demo/risk-preview`, {
            cache: "no-store",
            signal: controller.signal
          });
          if (prevRes.ok) {
            const prevData: RiskPreviewResponse = await prevRes.json();
            if (active) {
              setRiskPreview(prevData);
            }
          }
        } catch {
          // Non-fatal if risk-preview fails temporarily
        }
      } catch (e) {
        if (active) {
          setAccount(null);
          setAccountError(e instanceof Error ? e.message : "Demo API unavailable");
        }
      } finally {
        busy = false;
      }
    }

    const refreshOnFocus = () => {
      if (document.visibilityState === "visible") void loadRiskData();
    };

    void loadRiskData();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadRiskData();
    }, 15_000);

    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshOnFocus);

    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshOnFocus);
    };
  }, []);

  const demoConnected = account?.connected === true;
  const positions = !account?.positions_error ? account?.open_positions : null;
  const pendingEntries = !account?.orders_error ? account?.pending_entry_orders : null;
  const occupiedSlots = account?.occupied_slots ?? null;
  const remainingSlots = account?.remaining_slots ?? null;
  const riskHealth = account?.risk_health ?? "UNAVAILABLE";
  const ready = cycle?.trade_plans.ready_count ?? null;
  const attemptFailed = pipeline?.last_attempt?.status === "failed";

  return (
    <div className="page-content">
      {/* Top Header */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">TRADING OVERVIEW / READ ONLY</div>
          <h1>Dashboard</h1>
          <p>Verified Demo Risk Management, 15m scan intelligence, and dry-run position sizing.</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span
            className="outline-pill"
            style={{
              color: riskHealth === "VALID" ? "#4ade80" : riskHealth === "DEGRADED" ? "#fbbf24" : "#f87171",
              borderColor: riskHealth === "VALID" ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"
            }}
          >
            {riskHealth === "VALID" ? (
              <CheckCircle2 size={13} />
            ) : riskHealth === "DEGRADED" ? (
              <AlertTriangle size={13} />
            ) : (
              <WifiOff size={13} />
            )}
            RISK HEALTH: {riskHealth}
          </span>

          <span className="outline-pill">
            {demoConnected ? (
              "BYBIT DEMO CONNECTED"
            ) : (
              <>
                <WifiOff size={14} /> DEMO DISCONNECTED
              </>
            )}
          </span>
        </div>
      </div>

      {/* Primary 4 Stat Cards */}
      <div className="stats-grid">
        <Stat
          label="Demo Equity"
          icon={Wallet}
          value={
            demoConnected && account?.total_equity != null
              ? `$${account.total_equity.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} USD`
              : NO_VALUE
          }
          note={demoConnected ? "Bybit Demo unified verified equity" : accountError}
        />
        <Stat
          label="Risk Budget (1%)"
          icon={Scale}
          value={
            demoConnected && account?.risk_budget_usd != null
              ? `$${account.risk_budget_usd.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} USD`
              : NO_VALUE
          }
          note="Exactly 1% equity per trade limit"
        />
        <Stat
          label="Exposure Slots"
          icon={Layers}
          value={
            typeof occupiedSlots === "number"
              ? `${occupiedSlots} / 3 Used`
              : NO_VALUE
          }
          note={
            typeof occupiedSlots === "number" && typeof positions === "number" && typeof pendingEntries === "number"
              ? `${positions} active pos + ${pendingEntries} pending entry = ${occupiedSlots}/3`
              : typeof remainingSlots === "number"
              ? `${remainingSlots} slots remaining · Max 3 total`
              : "Slot capacity unavailable"
          }
        />
        <Stat
          label="Open Exposure"
          icon={ChartCandlestick}
          value={
            typeof positions === "number" && typeof pendingEntries === "number"
              ? `${positions} pos / ${pendingEntries} entry`
              : NO_VALUE
          }
          note="Exit reduce-only orders strictly excluded"
        />
      </div>

      {/* Row 1: Scan Cycle Intelligence & System Status */}
      <div className="dashboard-grid">
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Scan Cycle Intelligence</h2>
              <p>Committed 15m close · identical cycle across three reports</p>
            </div>
            <span className="subtle-tag">{freshness}</span>
          </div>
          <div style={{ padding: 22, display: "grid", gap: 15 }}>
            {cycle ? (
              <>
                <div style={{ fontSize: 22, fontWeight: 700 }}>
                  {cycle.opportunities.directional_count}{" "}
                  <span style={{ fontSize: 13, color: "#9299a3" }}>directional crypto assets</span>
                </div>
                <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                  <div>
                    <div style={detail}>Examined</div>
                    <strong>{cycle.scanner.universe_examined ?? cycle.scanner.total_scanned}</strong>
                  </div>
                  <div>
                    <div style={detail}>Neutral excluded</div>
                    <strong>{cycle.scanner.neutral_excluded}</strong>
                  </div>
                  <div>
                    <div style={detail}>Top ranked</div>
                    <strong>{cycle.opportunities.top_count}</strong>
                  </div>
                  <div>
                    <div style={detail}>Plan ready</div>
                    <strong>{ready}</strong>
                  </div>
                </div>
                <div style={detail}>
                  Cycle ID: {cycle.manifest.cycle_id} · Candle closed: {timeText(cycle.manifest.candle_close_utc)}
                </div>
                <div style={detail}>
                  Committed: {timeText(cycle.manifest.committed_at)} · Countdown to next 15m close:{" "}
                  <strong>{countdownToClose(now)}</strong>
                </div>
                {freshness !== "FRESH" && (
                  <p style={{ color: "#f59e0b", margin: 0 }}>
                    Stale committed cycle. Research only; do not treat plans as current entries.
                  </p>
                )}
              </>
            ) : (
              <p style={{ color: "#f59e0b" }}>
                No valid Phase 2 committed cycle yet. {error} Start the Phase 2 scheduler and wait for a successful 15m scan.
              </p>
            )}
            {attemptFailed && (
              <p style={{ color: "#f87171", margin: 0 }}>
                Last scan attempt FAILED. Previous committed cycle may still be available. Review scheduler logs.
              </p>
            )}
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              <Link to="/scanner" style={{ color: "#93c5fd" }}>Top 10 Scanner →</Link>
              <Link to="/signals" style={{ color: "#93c5fd" }}>Trade Plans →</Link>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>System Status</h2>
              <p>Verified state, not assumptions</p>
            </div>
          </div>
          <div className="system-row">
            <span><Database size={16} /> Bybit Demo</span>
            <b>{demoConnected ? "Connected" : "Unavailable"}</b>
          </div>
          <div className="system-row">
            <span><Search size={16} /> Scanner Cycle</span>
            <b>{freshness}</b>
          </div>
          <div className="system-row">
            <span><ShieldCheck size={16} /> Risk Policy Engine</span>
            <b style={{ color: riskHealth === "VALID" ? "#4ade80" : "#fbbf24" }}>{riskHealth}</b>
          </div>
          <div className="system-row">
            <span><Radio size={16} /> Trade Plan Engine</span>
            <b>{cycle ? `${cycle.trade_plans.ready_count} ready / ${cycle.trade_plans.rejected_count} rejected` : "Unavailable"}</b>
          </div>
          <div className="system-row">
            <span><Bot size={16} /> Order Execution</span>
            <b>{account?.trading_enabled ? "CHECK BACKEND SETTINGS" : "DISABLED / NO ORDERS"}</b>
          </div>
          <div className="system-row">
            <span><Lock size={16} /> Latest Attempt</span>
            <b>{pipeline?.last_attempt?.status ?? "Unknown"}</b>
          </div>
          <p style={{ padding: "10px 20px", ...detail }}>
            Signals are research evidence, not orders. Absolutely no live or demo execution is initiated.
          </p>
        </section>
      </div>

      {/* Row 2: Trade Plan Summary & Demo Portfolio Risk (P4) */}
      <div className="dashboard-grid">
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Trade Plan Summary</h2>
              <p>Only Phase 2 committed reports are counted</p>
            </div>
            <Link className="text-link" to="/signals">
              Signal Center <ChevronRight size={15} />
            </Link>
          </div>
          <div style={{ padding: 22, display: "flex", gap: 30, flexWrap: "wrap" }}>
            <div>
              <div style={detail}>PLAN READY</div>
              <strong style={{ fontSize: 20, color: ready && ready > 0 ? "#4ade80" : "#fff" }}>
                {ready ?? NO_VALUE}
              </strong>
            </div>
            <div>
              <div style={detail}>WAIT</div>
              <strong style={{ fontSize: 20 }}>{cycle?.trade_plans.wait_count ?? NO_VALUE}</strong>
            </div>
            <div>
              <div style={detail}>REJECTED</div>
              <strong style={{ fontSize: 20 }}>{cycle?.trade_plans.rejected_count ?? NO_VALUE}</strong>
            </div>
            <div>
              <div style={detail}>Last signal candle</div>
              <strong>{cycle ? candleText(cycle.opportunities.top_10[0]?.candle_time) : NO_VALUE}</strong>
            </div>
          </div>
        </section>

        {/* Demo Risk & Portfolio Panel (P4) */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Demo Risk Engine (P4)</h2>
              <p>Live Bybit DEMO risk parameters</p>
            </div>
            <span
              className="subtle-tag"
              style={{
                color: demoConnected ? "#4ade80" : "#f87171",
                borderColor: demoConnected ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"
              }}
            >
              {demoConnected ? "BYBIT_DEMO" : "DISCONNECTED"}
            </span>
          </div>
          <div style={{ padding: 20, display: "grid", gap: 9, fontSize: 13 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Verified Equity:</span>
              <strong>{demoConnected ? priceText(account?.total_equity) + " USD" : NO_VALUE}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Per-Trade Risk Policy:</span>
              <strong style={{ color: "#93c5fd" }}>Exactly 1.0% Equity</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Current 1% Risk Budget:</span>
              <strong style={{ color: "#34d399" }}>
                {demoConnected && account?.risk_budget_usd != null
                  ? `$${account.risk_budget_usd.toFixed(2)} USD`
                  : NO_VALUE}
              </strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Maximum Combined Slots:</span>
              <strong>3 (Positions + Entries)</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Current Open Positions:</span>
              <strong>{positions ?? NO_VALUE}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Pending Entry Orders:</span>
              <strong>{pendingEntries ?? NO_VALUE}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Remaining Available Slots:</span>
              <strong style={{ color: remainingSlots === 0 ? "#f87171" : "#4ade80" }}>
                {remainingSlots ?? NO_VALUE}
              </strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={detail}>Execution Status:</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: "#f87171" }}>
                DISABLED / NO ORDERS
              </span>
            </div>
            <div style={{ ...detail, marginTop: 4, borderTop: "1px solid #222", paddingTop: 8 }}>
              Wallet updated: {timeText(account?.updated_at)}
            </div>
            <div style={{ ...detail, color: "#666" }}>
              Exit/reduce-only orders excluded from entry slot exposure.
            </div>
          </div>
        </section>
      </div>

      {/* Row 3: Dry-Run Position Sizing Preview (P5) */}
      <section className="panel" style={{ marginTop: 19 }}>
        <div className="panel-header">
          <div>
            <h2>Dry-Run Position Sizing & Safety Preview (P5)</h2>
            <p>
              Decimal-safe 1% sizing with modeled fees and slippage · Instrument filters applied · DRY RUN ONLY
            </p>
          </div>
          <span
            className="outline-pill"
            style={{
              fontSize: 10,
              color: "#fbbf24",
              borderColor: "rgba(245,158,11,0.3)",
              background: "rgba(245,158,11,0.06)"
            }}
          >
            ORDER_EXECUTION_DISABLED
          </span>
        </div>

        <div style={{ padding: 22 }}>
          {riskPreview && riskPreview.candidates && riskPreview.candidates.length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                gap: 16
              }}
            >
              {riskPreview.candidates.map((cand) => {
                const isValid = cand.status === "DRY_RUN_VALID";
                const isBull = cand.direction === "LONG";
                return (
                  <div
                    key={cand.symbol}
                    style={{
                      background: "#14171d",
                      border: `1px solid ${isValid ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
                      borderRadius: 8,
                      padding: 16,
                      display: "flex",
                      flexDirection: "column",
                      gap: 10
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <strong style={{ fontSize: 16, color: "#fff" }}>{cand.symbol}</strong>
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: isBull ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
                            color: isBull ? "#4ade80" : "#f87171",
                            fontWeight: 700,
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4
                          }}
                        >
                          {isBull ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                          {cand.direction}
                        </span>
                      </div>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "3px 8px",
                          borderRadius: 4,
                          background: isValid ? "rgba(34,197,94,0.2)" : "rgba(245,158,11,0.2)",
                          color: isValid ? "#4ade80" : "#fbbf24"
                        }}
                      >
                        {cand.status.replaceAll("_", " ")}
                      </span>
                    </div>

                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 8,
                        fontSize: 12,
                        background: "#0c0e12",
                        padding: 10,
                        borderRadius: 6
                      }}
                    >
                      <div>
                        <span style={detail}>Entry Ref:</span>
                        <div><strong>{cand.entry_reference != null ? priceText(cand.entry_reference) : NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Stop Loss:</span>
                        <div><strong>{cand.sl != null ? priceText(cand.sl) : NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Take Profit:</span>
                        <div><strong>{cand.tp != null ? priceText(cand.tp) : NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Risk / Reward:</span>
                        <div><strong style={{ color: "#38bdf8" }}>{cand.rr ? cand.rr.toFixed(2) : NO_VALUE}</strong></div>
                      </div>
                    </div>

                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: 8,
                        fontSize: 12,
                        background: "#0c0e12",
                        padding: 10,
                        borderRadius: 6
                      }}
                    >
                      <div>
                        <span style={detail}>Proposed Qty:</span>
                        <div><strong>{cand.proposed_quantity ?? NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Qty Step:</span>
                        <div><strong>{cand.qty_step ?? NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Position Notional:</span>
                        <div><strong>{cand.estimated_position_notional_usd != null ? `$${cand.estimated_position_notional_usd.toFixed(2)}` : NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Fees & Slippage:</span>
                        <div><strong>{cand.estimated_fees_and_slippage_usd != null ? `$${cand.estimated_fees_and_slippage_usd.toFixed(3)}` : NO_VALUE}</strong></div>
                      </div>
                      <div>
                        <span style={detail}>Total Projected Risk:</span>
                        <div style={{ color: "#4ade80", fontWeight: 700 }}>
                          {cand.total_conservative_projected_risk_usd != null
                            ? `$${cand.total_conservative_projected_risk_usd.toFixed(2)}`
                            : NO_VALUE}
                        </div>
                      </div>
                      <div>
                        <span style={detail}>Risk Utilization:</span>
                        <div><strong>{cand.risk_utilization_pct != null ? `${cand.risk_utilization_pct.toFixed(2)}%` : NO_VALUE}</strong></div>
                      </div>
                    </div>

                    {cand.reason_messages && cand.reason_messages.length > 0 && (
                      <div
                        style={{
                          fontSize: 11,
                          color: "#fbbf24",
                          background: "rgba(245,158,11,0.08)",
                          padding: "6px 9px",
                          borderRadius: 4,
                          lineHeight: 1.4
                        }}
                      >
                        {cand.reason_messages.join(" · ")}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="empty-state">
              <div className="empty-icon" style={{ borderColor: "#2a2d34" }}>
                <Scale size={24} color="#71717a" />
              </div>
              <h3 style={{ margin: "14px 0 6px", fontSize: 15, color: "#eee" }}>
                No Confirmed Trade Plans
              </h3>
              <p style={{ maxWidth: 460, color: "#888", fontSize: 13, margin: "0 auto 12px" }}>
                No confirmed trade plans in the latest committed cycle. No dry-run position preview available.
              </p>
              <span
                style={{
                  fontSize: 11,
                  color: "#6b7280",
                  background: "#161920",
                  padding: "4px 10px",
                  borderRadius: 4
                }}
              >
                1% position sizing will automatically compute here as soon as a 15m cycle produces a PLAN_READY setup.
              </span>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
