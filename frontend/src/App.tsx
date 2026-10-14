import SignalResultsPage from "./SignalResultsPage";
import Phase3Dashboard from "./Phase3Dashboard";
import TradeJournalPage from "./TradeJournalPage";
import { useEffect as useBackendEffect, useState as useBackendState } from "react";

import ScannerPage from "./OpportunityScannerPage";
import TradingTerminalPage from "./TradingTerminalPage";
import { BrowserRouter, NavLink, Routes, Route, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Search, Radio, ChartCandlestick, Blocks,
  Bot, Wallet, BookOpen, FileBarChart, Play, Settings,
  Bell, WifiOff, ArrowUpRight, ArrowDownRight,
  Activity, ShieldCheck, Clock3, Database, ChevronRight
} from "lucide-react";
import "./App.css";

const navigation = [
  { path: "/", name: "Dashboard", icon: LayoutDashboard },
  { path: "/scanner", name: "Market Scanner", icon: Search },
  { path: "/signals", name: "Signal Center", icon: Radio },
  { path: "/terminal", name: "Trading Terminal", icon: ChartCandlestick },
  { path: "/strategy", name: "Strategy Builder", icon: Blocks },
  { path: "/bots", name: "Bot Manager", icon: Bot },
  { path: "/demo", name: "Demo Trading", icon: Wallet },
  { path: "/journal", name: "Trade Journal", icon: BookOpen },
  { path: "/reports", name: "Daily Reports", icon: FileBarChart },
  { path: "/replay", name: "Replay Lab", icon: Play },
  { path: "/settings", name: "Settings", icon: Settings },
];

const markets = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

function EmptyState({
  title,
  description,
  icon: Icon = Database
}: {
  title: string;
  description: string;
  icon?: typeof Database;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon size={25} /></div>
      <h3>{title}</h3>
      <p>{description}</p>
      <span className="status-label">NOT CONNECTED</span>
    </div>
  );
}


function StatCard({
  label, icon: Icon, note, value
}: {
  label: string;
  icon: typeof Activity;
  note: string;
  value?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span>{label}</span>
        <Icon size={18} />
      </div>
      <div className="stat-value">{value ?? "?"}</div>
      <div className="stat-note">{note}</div>
    </div>
  );
}


// TRADINGOS_REAL_DASHBOARD_V2

type DemoSummary = {
  connected: boolean;
  environment: string;
  total_equity: number | null;
  available_balance: number | null;
  open_positions: number | null;
  pending_orders: number | null;
  positions_error: boolean;
  orders_error: boolean;
  trading_enabled: boolean;
  updated_at: string;
};

type ScannerSummary = {
  generated_at: string;
  total_scanned: number;
  candidate_count: number;
};

type RankingSummary = {
  generated_at: string;
  directional_count: number;
  top_count: number;
};

function displayEquity(amount: number | null | undefined) {
  if (typeof amount !== "number" || !Number.isFinite(amount)) {
    return "?";
  }
  return amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }) + " USD";
}

function snapshotAge(iso: string | undefined) {
  if (!iso) return "Unknown";
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "Unknown";

  const minutes = Math.max(
    0,
    Math.floor((Date.now() - time) / 60000)
  );

  return `${minutes} min ago`;
}

function isSnapshotFresh(iso: string | undefined) {
  if (!iso) return false;
  const time = Date.parse(iso);

  return Number.isFinite(time)
    && time <= Date.now() + 60000
    && Date.now() - time <= 30 * 60000;
}

export function Dashboard() {
  const [account, setAccount] =
    useBackendState<DemoSummary | null>(null);

  const [accountError, setAccountError] =
    useBackendState("Connecting...");

  const [scanner, setScanner] =
    useBackendState<ScannerSummary | null>(null);

  const [ranking, setRanking] =
    useBackendState<RankingSummary | null>(null);

  useBackendEffect(() => {
    let active = true;

    async function readJson<T>(path: string): Promise<T> {
      const response = await fetch(
        `http://127.0.0.1:8765${path}`,
        { cache: "no-store" }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      return await response.json() as T;
    }

    async function refresh() {
      const results = await Promise.allSettled([
        readJson<DemoSummary>("/demo/account"),
        readJson<ScannerSummary>("/scanner/results"),
        readJson<RankingSummary>("/scanner/opportunities")
      ]);

      if (!active) return;

      const demoResult = results[0];
      const scannerResult = results[1];
      const rankingResult = results[2];

      if (
        demoResult.status === "fulfilled" &&
        demoResult.value.connected === true &&
        demoResult.value.environment === "BYBIT_DEMO"
      ) {
        setAccount(demoResult.value);
        setAccountError("");
      } else {
        setAccount(null);
        setAccountError("Demo account API unavailable");
      }

      setScanner(
        scannerResult.status === "fulfilled"
          ? scannerResult.value : null
      );

      setRanking(
        rankingResult.status === "fulfilled"
          ? rankingResult.value : null
      );
    }

    void refresh();

    const timer = window.setInterval(() => {
      void refresh();
    }, 30000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const connected = account?.connected === true;
  const positions = account?.positions_error
    ? null : account?.open_positions;
  const orders = account?.orders_error
    ? null : account?.pending_orders;

  const positionsKnown =
    typeof positions === "number";

  const ordersKnown =
    typeof orders === "number";

  const scannerFresh = isSnapshotFresh(scanner?.generated_at);
  const rankingFresh = isSnapshotFresh(ranking?.generated_at);

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TRADING OVERVIEW</div>
          <h1>Dashboard</h1>
          <p>Market intelligence, automated bots and demo trading performance.</p>
        </div>

        <span className="outline-pill">
          {connected
            ? "BYBIT DEMO CONNECTED"
            : <><WifiOff size={14} /> DEMO DISCONNECTED</>}
        </span>
      </div>

      <div className="stats-grid">
        <StatCard
          label="Demo Equity"
          icon={Wallet}
          value={connected ? displayEquity(account?.total_equity) : "?"}
          note={connected
            ? "Bybit Demo Unified Account"
            : accountError}
        />

        <StatCard
          label="Today's P&L"
          icon={Activity}
          note="Realized P&L history not integrated"
        />

        <StatCard
          label="Active Bots"
          icon={Bot}
          note="Bot runtime status not integrated"
        />

        <StatCard
          label="Open Positions"
          icon={ChartCandlestick}
          value={positionsKnown ? String(positions) : "?"}
          note={ordersKnown
            ? `${orders} pending orders (first page)`
            : "Order count unavailable"}
        />
      </div>

      <div className="dashboard-grid">
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Portfolio Performance</h2>
              <p>Demo account overview ? historical P&L coming later</p>
            </div>
            <span className="subtle-tag">DEMO</span>
          </div>

          {connected ? (
            <div style={{
              padding: 24,
              display: "grid",
              gap: 22
            }}>
              <div>
                <div style={{
                  color: "#9299a3",
                  fontSize: 12,
                  marginBottom: 8
                }}>
                  Demo Total Equity
                </div>
                <div style={{
                  fontWeight: 700,
                  fontSize: 30
                }}>
                  {displayEquity(account?.total_equity)}
                </div>
              </div>

              <div style={{
                display: "flex",
                gap: 38,
                flexWrap: "wrap"
              }}>
                <div>
                  <div style={{ color: "#9299a3", fontSize: 12 }}>
                    Open Positions
                  </div>
                  <strong>{positionsKnown ? positions : "?"}</strong>
                </div>

                <div>
                  <div style={{ color: "#9299a3", fontSize: 12 }}>
                    Pending Orders
                  </div>
                  <strong>{ordersKnown ? orders : "?"}</strong>
                </div>

                <div>
                  <div style={{ color: "#9299a3", fontSize: 12 }}>
                    Order Execution
                  </div>
                  <strong>
                    {account?.trading_enabled
                      ? "ENABLED (check configuration)"
                      : "DISABLED"}
                  </strong>
                </div>
              </div>

              <p style={{
                color: "#9299a3",
                fontSize: 12,
                margin: 0
              }}>
                Last wallet update: {account?.updated_at}
              </p>

              <p style={{
                color: "#9299a3",
                fontSize: 12,
                margin: 0
              }}>
                Equity history and realized P&L are not yet
                available. Pending order count uses the first
                Bybit API page only.
              </p>
            </div>
          ) : (
            <EmptyState
              title="Demo account data unavailable"
              description="Waiting for authenticated read-only Bybit Demo API."
              icon={Activity}
            />
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>System Status</h2>
              <p>Actual connection and saved scanner snapshots</p>
            </div>
          </div>

          <div className="system-row">
            <span><Database size={16} /> Demo Account</span>
            <b>{connected ? "Connected" : "Unavailable"}</b>
          </div>

          <div className="system-row">
            <span><Search size={16} /> Market Scanner</span>
            <b>
              {scanner
                ? scannerFresh
                  ? `${scanner.total_scanned} scanned`
                  : "Snapshot Stale"
                : "Unavailable"}
            </b>
          </div>

          <div className="system-row">
            <span><Radio size={16} /> Opportunity Ranker</span>
            <b>
              {ranking
                ? rankingFresh
                  ? `${ranking.directional_count} directional`
                  : "Snapshot Stale"
                : "Unavailable"}
            </b>
          </div>

          <div className="system-row">
            <span><Bot size={16} /> Demo Execution</span>
            <b>{account?.trading_enabled ? "Enabled" : "Disabled"}</b>
          </div>

          <div className="system-row">
            <span><FileBarChart size={16} /> Daily Reports</span>
            <b>Not Integrated</b>
          </div>

          <div className="info-note">
            <ShieldCheck size={17} />
            This dashboard is read-only. No orders are placed.
          </div>
        </section>
      </div>

      <div className="dashboard-grid">
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Latest Scanner Candidates</h2>
              <p>Saved research candidates, not verified trade entries</p>
            </div>
            <NavLink className="text-link" to="/signals">
              Signal Center <ChevronRight size={15} />
            </NavLink>
          </div>

          <div style={{ padding: 24 }}>
            <div style={{
              fontSize: 28,
              fontWeight: 700
            }}>
              {scanner?.candidate_count ?? "?"}
            </div>

            <p style={{
              color: "#9299a3",
              fontSize: 12
            }}>
              Saved sweep candidates ? Last scanner snapshot:
              {" "}{snapshotAge(scanner?.generated_at)}
              {!scannerFresh && " ? STALE"}
            </p>

            <p style={{
              color: "#9299a3",
              fontSize: 12
            }}>
              A scanner candidate does not mean Entry Ready.
              Liquidity Sweep, Candle Pattern and Risk?Reward
              must all pass.
            </p>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Daily Loss Intelligence</h2>
              <p>Why your demo bot gained or lost money</p>
            </div>
          </div>

          <EmptyState
            title="No report available"
            description="Reports will use actual signal, order, fill and market evidence."
            icon={FileBarChart}
          />
        </section>
      </div>
    </div>
  );
}

export function Scanner() {
  return (
    <div className="page-content">
      <PageHeading eyebrow="MARKET INTELLIGENCE" title="Market Scanner" description="Detect market structure, volume conditions and potential setups across selected symbols." />
      <section className="panel">
        <div className="panel-header"><div><h2>Market Overview</h2><p>15-minute timeframe · Bybit market data integration pending</p></div><span className="subtle-tag">3 SYMBOLS</span></div>
        <div className="table-scroll">
          <table className="market-table">
            <thead><tr><th>SYMBOL</th><th>PRICE</th><th>24H CHANGE</th><th>TREND</th><th>VOLUME</th><th>SETUP</th><th>STATUS</th></tr></thead>
            <tbody>{markets.map((symbol) => (
              <tr key={symbol}>
                <td className="symbol-cell"><span className="symbol-dot" />{symbol}</td>
                <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
                <td><span className="status-label">NOT CONNECTED</span></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      <div className="info-note"><Search size={17} /> Scanner conditions will come from your Strategy Builder modules, not hardcoded BUY/SELL guesses.</div>
    </div>
  );
}

export function Signals() {
  return (
    <div className="page-content">
      <PageHeading eyebrow="TRADE OPPORTUNITIES" title="Signal Center" description="Review confirmed strategy signals with entry, stop loss, targets and decision evidence." />
      <div className="stats-grid three">
        <StatCard label="New Signals" icon={Radio} note="Waiting for signal engine" />
        <StatCard label="Long Setups" icon={ArrowUpRight} note="No confirmed signals" />
        <StatCard label="Short Setups" icon={ArrowDownRight} note="No confirmed signals" />
      </div>
      <section className="panel"><EmptyState title="No verified signals" description="A signal will appear only after your saved strategy rules are satisfied. Each signal will explain which conditions passed or failed." icon={Radio} /></section>
    </div>
  );
}

export function TradingTerminal() {
  return (
    <div className="page-content terminal-content">
      <PageHeading eyebrow="MARKET WORKSPACE" title="Trading Terminal" description="Professional candlestick chart, indicators and symbol watchlist." />
      <div className="terminal-grid">
        <section className="panel chart-panel">
          <div className="panel-header">
            <div><h2>BTCUSDT <span className="small-muted">· 15m</span></h2><p>Bybit market chart</p></div>
            <span className="subtle-tag">CHART ENGINE NEXT</span>
          </div>
          <EmptyState title="Chart integration is next" description="The next step connects actual Bybit candles using TradingView Lightweight Charts, with RSI, Volume and indicator controls." icon={ChartCandlestick} />
        </section>
        <aside className="panel watchlist-panel">
          <div className="panel-header"><div><h2>Watchlist</h2><p>Bybit Perpetual</p></div></div>
          {markets.map((symbol) => <div className="watchlist-row" key={symbol}><strong>{symbol}</strong><span>—</span></div>)}
          <p className="watchlist-footnote">Real prices will load after market-data integration.</p>
        </aside>
      </div>
    </div>
  );
}

function Strategy() {
  return (
    <div className="page-content">
      <PageHeading eyebrow="MODULAR TRADING LOGIC" title="Strategy Builder" description="Add trend, support/resistance, indicators and entry rules one module at a time." />
      <div className="feature-grid">
        <section className="panel feature-panel"><Activity size={22} /><h2>Trend Logic</h2><p>Define swing highs/lows, HH, HL, LH and LL detection.</p><span className="status-label">NOT CONFIGURED</span></section>
        <section className="panel feature-panel"><Blocks size={22} /><h2>Support & Resistance</h2><p>Define zones, retests, breakouts and invalidation rules.</p><span className="status-label">NOT CONFIGURED</span></section>
        <section className="panel feature-panel"><ChartCandlestick size={22} /><h2>Indicator Rules</h2><p>Use RSI, Volume, EMA and custom indicator conditions.</p><span className="status-label">NOT CONFIGURED</span></section>
      </div>
      <div className="info-note"><ShieldCheck size={17} /> A visual indicator does not automatically become a bot trading rule. You must explicitly add it to a strategy.</div>
    </div>
  );
}

function SimplePage({
  eyebrow, title, description, emptyTitle, emptyDescription, icon
}: {
  eyebrow: string; title: string; description: string;
  emptyTitle: string; emptyDescription: string; icon: typeof Activity;
}) {
  return (
    <div className="page-content">
      <PageHeading eyebrow={eyebrow} title={title} description={description} />
      <section className="panel"><EmptyState title={emptyTitle} description={emptyDescription} icon={icon} /></section>
    </div>
  );
}

function PageHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="page-heading">
      <div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>
    </div>
  );
}

function AppLayout() {

  // Track the real Python backend status
  const [backendOnline, setBackendOnline] = useBackendState(false);

  useBackendEffect(() => {
    let active = true;

    async function checkBackend() {
      try {
        const response = await fetch(
          "http://127.0.0.1:8765/health",
          { cache: "no-store" }
        );

        if (!response.ok) {
          throw new Error("Backend returned an error");
        }

        const data = await response.json();

        if (active) {
          setBackendOnline(data.status === "online");
        }
      } catch {
        if (active) {
          setBackendOnline(false);
        }
      }
    }

    // Check immediately and every 10 seconds
    void checkBackend();
    const timer = setInterval(() => {
      void checkBackend();
    }, 10000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const location = useLocation();
  const activePage = navigation.find((item) => item.path === location.pathname);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon"><ChartCandlestick size={23} /></div>
          <div><strong>TRADING<span>OS</span></strong><small>INTELLIGENCE PLATFORM</small></div>
        </div>

        <div className="sidebar-label">WORKSPACE</div>
        <nav className="sidebar-nav">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.path} to={item.path} end={item.path === "/"} className={({isActive}) => `nav-link ${isActive ? "active" : ""}`}>
                <Icon size={18} /><span>{item.name}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <span className="mode-tag"><span className="grey-dot" /> DEMO MODE</span>
          <p>Read-only Demo · account status on Dashboard</p>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><ChevronRight size={15} /><strong>{activePage?.name ?? "Dashboard"}</strong></div>
          <div className="topbar-actions">
            <span className={`header-status ${backendOnline ? "system-is-online" : "system-is-offline"}`} role="status"><span className="system-light" aria-hidden="true" /> {backendOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}</span>
            <span className="top-divider" />
            <Bell size={18} />
            <div className="avatar">T</div>
          </div>
        </header>
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Phase3Dashboard />} />
            <Route path="/scanner" element={<ScannerPage />} />
            <Route path="/signals" element={<SignalResultsPage />} />
            <Route path="/terminal" element={<TradingTerminalPage />} />
            <Route path="/strategy" element={<Strategy />} />
            <Route path="/bots" element={<SimplePage eyebrow="AUTOMATION" title="Bot Manager" description="Create, assign and control automated demo trading bots." emptyTitle="No bots configured" emptyDescription="Bot configuration, lifecycle controls and risk limits will be added after the strategy engine." icon={Bot} />} />
            <Route path="/demo" element={<SimplePage eyebrow="BYBIT INTEGRATION" title="Demo Trading" description="Demo balance, orders, open positions and execution status." emptyTitle="Bybit Demo not connected" emptyDescription="Demo API is read-only on Dashboard. No order execution enabled." icon={Wallet} />} />
            <Route path="/journal" element={<TradeJournalPage initialTab="signals" />} />
            <Route path="/reports" element={<TradeJournalPage initialTab="reports" />} />
            <Route path="/replay" element={<SimplePage eyebrow="HISTORICAL VERIFICATION" title="Replay Lab" description="Check whether your bot detects market structure correctly on historical data." emptyTitle="Replay engine not connected" emptyDescription="Replay will reveal candles step by step without letting strategy logic see future data." icon={Clock3} />} />

            <Route path="/settings" element={<SimplePage eyebrow="APPLICATION CONFIGURATION" title="Settings" description="Bybit Demo API, risk controls, indicators and notification preferences." emptyTitle="Settings module coming next" emptyDescription="Demo credentials will be handled by the backend, never embedded in frontend source code." icon={Settings} />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return <BrowserRouter><AppLayout /></BrowserRouter>;
}
