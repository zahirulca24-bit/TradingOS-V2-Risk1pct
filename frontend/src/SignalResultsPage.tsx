import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Radio,
  Search,
  TrendingUp,
  TrendingDown,
  ExternalLink,
  AlertTriangle,
  RefreshCw,
  Clock,
  Database,
  X,
  ShieldCheck
} from "lucide-react";
import {
  NO_VALUE,
  candleText,
  priceText,
  timeText,
  useCycleFeed,
  type PlanRow,
  type PlanStatus,
  type Opportunity
} from "./CycleData";
import "./SignalResultsPage.css";

const STATUS_PRIORITY: Record<PlanStatus, number> = {
  PLAN_READY: 0,
  WAIT: 1,
  REJECTED: 2,
  EXCLUDED: 3,
};

const REASON_MAPPINGS: Record<string, string> = {
  SWEEP_PATTERN_AND_RR_CONFIRMED: "Qualified sweep, confirmation pattern & R:R ≥ 2.0 confirmed (Plan Ready)",
  NO_SWEEP_ON_LAST_CLOSED_CANDLE: "No liquidity sweep on the last closed 15m candle",
  SWEEP_WITHOUT_REQUIRED_PATTERN: "Liquidity sweep occurred but required Pin Bar or Engulfing pattern did not form",
  RISK_REWARD_TOO_LOW: "Setup detected but calculated Risk/Reward is below required 2.0:1 threshold",
  NO_OPPOSING_SWING_TARGET: "Setup swept but no opposing swing high/low exists for valid Take Profit",
  INVALID_RISK_OR_REWARD: "Risk or reward parameters calculate to zero or negative geometry",
  NO_CONFIRMED_SUPPORT: "Waiting for 15m structure to confirm swing support levels",
  NO_CONFIRMED_RESISTANCE: "Waiting for 15m structure to confirm swing resistance levels",
  NOT_ENOUGH_15M_OR_1H_CANDLES: "Insufficient closed candle history for structural validation",
  EXCLUDED: "Neutral trend excluded from directional ranking",
};

function formatReason(raw: string | undefined): string {
  if (!raw) return NO_VALUE;
  const upper = raw.trim().toUpperCase();
  if (REASON_MAPPINGS[upper]) return REASON_MAPPINGS[upper];
  return raw
    .split("_")
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function formatPattern(pattern: string | null | undefined): string {
  if (!pattern) return NO_VALUE;
  return pattern.replaceAll("_", " ");
}

type StatusFilter = "ALL" | "PLAN_READY" | "WAIT" | "REJECTED";
type DirectionFilter = "ALL" | "BULLISH" | "BEARISH";
type SortOption = "PRIORITY" | "AZ" | "ZA";

interface RankedOpportunity extends Opportunity {
  priority: number;
}

interface SignalCardProps {
  plan: PlanRow;
  opportunity?: RankedOpportunity;
  cycleFresh: boolean;
}

function SignalCard({ plan, opportunity, cycleFresh }: SignalCardProps) {
  const isReady = plan.status === "PLAN_READY";
  const isWait = plan.status === "WAIT";

  // Legitimate calculation check:
  // Trade execution levels (Entry, SL, TP, RR) must ONLY appear on qualified PLAN_READY cards.
  // On WAIT or REJECTED setups, no fabricated or unapproved levels appear.
  const displayEntry = isReady && plan.entry_reference != null ? priceText(plan.entry_reference) : NO_VALUE;
  const displaySl = isReady && plan.sl != null ? priceText(plan.sl) : NO_VALUE;
  const displayTp = isReady && plan.tp != null ? priceText(plan.tp) : NO_VALUE;
  const displayRr = isReady && plan.rr != null ? `${plan.rr.toFixed(2)}` : NO_VALUE;

  // Sweep level is valid if a sweep was confirmed in source data
  const displaySweep = plan.sweep_level != null ? priceText(plan.sweep_level) : NO_VALUE;

  // Direction: plan direction preferred, then opportunity direction, then fallback em dash
  const direction = plan.direction || opportunity?.direction || (plan.trend === "BULLISH" ? "LONG" : plan.trend === "BEARISH" ? "SHORT" : null);

  const statusClass = isReady
    ? "plan-ready"
    : isWait
    ? "wait"
    : "rejected";

  const borderClass = isReady
    ? "status-ready-border"
    : isWait
    ? "status-wait-border"
    : "status-rejected-border";

  const isBullish = plan.trend === "BULLISH" || direction === "LONG";

  return (
    <article className={`signal-card ${borderClass}`} aria-label={`Signal card for ${plan.symbol}`}>
      <div>
        {/* Top bar: Symbol & Status */}
        <div className="signal-card-top">
          <div className="signal-symbol-block">
            <span className="signal-symbol-name">{plan.symbol}</span>
            {opportunity?.priority != null && (
              <span className="signal-priority-tag">#{opportunity.priority}</span>
            )}
          </div>
          <span className={`signal-status-pill ${statusClass}`}>
            {plan.status.replaceAll("_", " ")}
          </span>
        </div>

        {/* Sub-header Badges: Trend, Direction, Pattern, Sweep */}
        <div className="signal-tags-row">
          <span className={`signal-tag ${isBullish ? "bullish" : "bearish"}`}>
            {isBullish ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
            1H {plan.trend}
          </span>

          {direction && (
            <span className={`signal-tag ${direction === "LONG" ? "bullish" : "bearish"}`}>
              {direction}
            </span>
          )}

          {plan.pattern ? (
            <span className="signal-tag pattern">
              {formatPattern(plan.pattern)}
            </span>
          ) : (
            <span className="signal-tag neutral">
              No pattern
            </span>
          )}

          {plan.sweep_level != null && (
            <span className="signal-tag sweep">
              Swept {priceText(plan.sweep_level)}
            </span>
          )}
        </div>

        {/* Rationale Box */}
        <div className="signal-rationale-box">
          <div className="signal-rationale-title">Decision Rationale</div>
          <p className="signal-rationale-text">{formatReason(plan.reason)}</p>
        </div>

        {/* Metrics Grid */}
        <div className="signal-metrics-grid">
          <div className="signal-metric-cell">
            <span className="signal-metric-label">Sweep Level</span>
            <span className="signal-metric-val">{displaySweep}</span>
          </div>

          <div className="signal-metric-cell">
            <span className="signal-metric-label">Entry Ref</span>
            <span className="signal-metric-val">{displayEntry}</span>
          </div>

          <div className="signal-metric-cell">
            <span className="signal-metric-label">Stop Loss</span>
            <span className="signal-metric-val">{displaySl}</span>
          </div>

          <div className="signal-metric-cell">
            <span className="signal-metric-label">Take Profit</span>
            <span className="signal-metric-val">{displayTp}</span>
          </div>

          <div className="signal-metric-cell">
            <span className="signal-metric-label">Risk / Reward</span>
            <span className={`signal-metric-val ${isReady && plan.rr ? "rr-highlight" : ""}`}>
              {displayRr}
            </span>
          </div>

          <div className="signal-metric-cell">
            <span className="signal-metric-label">Candle Ref</span>
            <span className="signal-metric-val">
              {plan.candle_time ? candleText(plan.candle_time) : NO_VALUE}
            </span>
          </div>
        </div>

        {/* Status Callout if Plan Ready */}
        {isReady && (
          <div
            style={{
              fontSize: 11,
              color: cycleFresh ? "#4ade80" : "#fbbf24",
              background: cycleFresh ? "rgba(34,197,94,0.08)" : "rgba(245,158,11,0.08)",
              border: `1px solid ${cycleFresh ? "rgba(34,197,94,0.25)" : "rgba(245,158,11,0.25)"}`,
              borderRadius: 5,
              padding: "6px 9px",
              marginBottom: 12,
              lineHeight: 1.4
            }}
          >
            {cycleFresh
              ? "✓ Plan Ready — Reference entry based on closed 15m candle. Independent live execution checks required."
              : "⚠ Research Only — Stale committed cycle. Never enter from an expired snapshot."}
          </div>
        )}
      </div>

      {/* Footer: Candle Timestamp & Open Chart Action */}
      <div className="signal-card-footer">
        <span className="signal-candle-time">
          <Clock size={12} />
          {plan.candle_time ? `${candleText(plan.candle_time)}` : "Closed candle"}
        </span>

        <Link
          to={`/terminal?symbol=${encodeURIComponent(plan.symbol)}&tf=15`}
          className="signal-chart-action"
          title={`Open ${plan.symbol} 15m chart in Trading Terminal`}
          aria-label={`Open chart for ${plan.symbol}`}
        >
          Open Chart
          <ExternalLink size={12} />
        </Link>
      </div>
    </article>
  );
}

export default function SignalResultsPage() {
  const { cycle, pipeline, error, freshness, refresh } = useCycleFeed();

  // Search & Filter State (preserved across cycle polling)
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("ALL");
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("ALL");
  const [sortOption, setSortOption] = useState<SortOption>("PRIORITY");

  // Build lookup map for Opportunity ranking data from the SAME cycle
  const oppMap = useMemo(() => {
    const map = new Map<string, RankedOpportunity>();
    if (!cycle) return map;
    const combined = [...cycle.opportunities.top_10, ...cycle.opportunities.remaining];
    combined.forEach((opp, idx) => {
      map.set(opp.symbol, { ...opp, priority: idx + 1 });
    });
    return map;
  }, [cycle]);

  // Aggregate stats from source data
  const rawPlans = useMemo(() => cycle?.trade_plans.results ?? [], [cycle]);
  const totalCount = rawPlans.length;

  const stats = useMemo(() => {
    let ready = 0;
    let wait = 0;
    let rejected = 0;
    let bullish = 0;
    let bearish = 0;

    for (const p of rawPlans) {
      if (p.status === "PLAN_READY") ready++;
      else if (p.status === "WAIT") wait++;
      else if (p.status === "REJECTED") rejected++;

      if (p.trend === "BULLISH" || p.direction === "LONG") bullish++;
      else if (p.trend === "BEARISH" || p.direction === "SHORT") bearish++;
    }

    return { ready, wait, rejected, bullish, bearish };
  }, [rawPlans]);

  // Reactive filtering and sorting
  const filteredPlans = useMemo(() => {
    const query = search.trim().toUpperCase();

    return rawPlans
      .filter(plan => {
        // 1. Search Query
        if (query && !plan.symbol.toUpperCase().includes(query)) {
          return false;
        }

        // 2. Status Filter
        if (statusFilter !== "ALL" && plan.status !== statusFilter) {
          return false;
        }

        // 3. Direction Filter
        if (directionFilter === "BULLISH") {
          const isBull = plan.trend === "BULLISH" || plan.direction === "LONG";
          if (!isBull) return false;
        } else if (directionFilter === "BEARISH") {
          const isBear = plan.trend === "BEARISH" || plan.direction === "SHORT";
          if (!isBear) return false;
        }

        return true;
      })
      .sort((a, b) => {
        if (sortOption === "AZ") {
          return a.symbol.localeCompare(b.symbol);
        }
        if (sortOption === "ZA") {
          return b.symbol.localeCompare(a.symbol);
        }

        // Default "PRIORITY" sorting:
        // 1. Plan Readiness (Ready first, then Wait, then Rejected)
        const rankA = STATUS_PRIORITY[a.status] ?? 99;
        const rankB = STATUS_PRIORITY[b.status] ?? 99;
        if (rankA !== rankB) return rankA - rankB;

        // 2. Opportunity Priority (from cycle opportunity ranker)
        const oppA = oppMap.get(a.symbol);
        const oppB = oppMap.get(b.symbol);
        const prioA = oppA?.priority ?? 999;
        const prioB = oppB?.priority ?? 999;
        if (prioA !== prioB) return prioA - prioB;

        // 3. Alphabetical tie-breaker
        return a.symbol.localeCompare(b.symbol);
      });
  }, [rawPlans, search, statusFilter, directionFilter, sortOption, oppMap]);

  const hasActiveFilters = search.trim() !== "" || statusFilter !== "ALL" || directionFilter !== "ALL" || sortOption !== "PRIORITY";

  function handleResetFilters() {
    setSearch("");
    setStatusFilter("ALL");
    setDirectionFilter("ALL");
    setSortOption("PRIORITY");
  }

  const isFresh = freshness === "FRESH";
  const lastAttemptFailed = pipeline?.last_attempt?.status === "failed";

  return (
    <div className="signals-workspace">
      {/* Page Header */}
      <header className="signals-header">
        <div>
          <div className="signals-eyebrow">MARKET WORKSPACE · COMMITTED EVIDENCE</div>
          <h1>Signal Center</h1>
          <p className="signals-header-desc">
            Evidence-based 50-coin research signals from the unified Phase 2 cycle. Read-only
            trading rationale; stale plans must never be executed.
          </p>
        </div>

        <div className="signals-header-badges">
          <div className="signals-meta-pill">
            <Database size={13} />
            <span>Cycle: {cycle?.manifest.cycle_id ?? NO_VALUE}</span>
            <span>·</span>
            <span>Closed: {timeText(cycle?.manifest.candle_close_utc)}</span>
            <span
              className={`signals-freshness-tag ${
                isFresh ? "fresh" : freshness === "STALE" ? "stale" : "unavailable"
              }`}
            >
              {freshness}
            </span>
          </div>
        </div>
      </header>

      {/* Warning Banners */}
      {lastAttemptFailed && (
        <div className="signals-alert warning" role="alert">
          <AlertTriangle size={16} />
          <div>
            <strong>Latest scan attempt failed:</strong> {pipeline?.last_attempt?.reason || "Rate-limit or network timeout"}. Showing last valid committed historical cycle.
          </div>
        </div>
      )}

      {error && (
        <div className="signals-alert error" role="alert">
          <AlertTriangle size={16} />
          <div>
            <strong>Cycle Feed Notice:</strong> {error}. No legacy or fabricated snapshots are displayed.
          </div>
        </div>
      )}

      {!isFresh && cycle && (
        <div className="signals-alert warning" role="alert">
          <Clock size={16} />
          <div>
            <strong>Research Stale:</strong> The committed cycle candle close was more than 15 minutes ago. All trade levels are historical research references only and not executable.
          </div>
        </div>
      )}

      {/* Summary Stat Cards */}
      <section className="signals-stats-row" aria-label="Signal statistics overview">
        <div className="signals-stat-box ready">
          <span className="signals-stat-label">Plan Ready</span>
          <span className="signals-stat-val">{stats.ready}</span>
        </div>
        <div className="signals-stat-box wait">
          <span className="signals-stat-label">Waiting</span>
          <span className="signals-stat-val">{stats.wait}</span>
        </div>
        <div className="signals-stat-box rejected">
          <span className="signals-stat-label">Rejected</span>
          <span className="signals-stat-val">{stats.rejected}</span>
        </div>
        <div className="signals-stat-box">
          <span className="signals-stat-label">Bullish / Bearish</span>
          <span className="signals-stat-val">
            <span style={{ color: "#34d399" }}>{stats.bullish}</span>
            <span style={{ color: "#71717a", fontSize: 14 }}> / </span>
            <span style={{ color: "#f87171" }}>{stats.bearish}</span>
          </span>
        </div>
        <div className="signals-stat-box">
          <span className="signals-stat-label">Total Directional</span>
          <span className="signals-stat-val">{totalCount}</span>
        </div>
      </section>

      {/* Controls & Filter Bar */}
      <section className="signals-filter-bar" aria-label="Signal filtering controls">
        <div className="signals-filter-controls">
          {/* Symbol Search */}
          <div className="signals-search-wrap">
            <Search size={14} className="signals-search-icon" />
            <input
              type="text"
              className="signals-search-input"
              placeholder="Search symbol (e.g. BTC, ETH)..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              aria-label="Search by symbol"
            />
            {search && (
              <button
                type="button"
                className="signals-search-clear"
                onClick={() => setSearch("")}
                aria-label="Clear search"
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Status Filter */}
          <div className="signals-select-group">
            <label htmlFor="status-select" className="signals-select-label">Status:</label>
            <select
              id="status-select"
              className="signals-select"
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
              aria-label="Filter by trade plan status"
            >
              <option value="ALL">All Statuses ({totalCount})</option>
              <option value="PLAN_READY">Plan Ready ({stats.ready})</option>
              <option value="WAIT">Waiting ({stats.wait})</option>
              <option value="REJECTED">Rejected ({stats.rejected})</option>
            </select>
          </div>

          {/* Direction Filter */}
          <div className="signals-select-group">
            <label htmlFor="direction-select" className="signals-select-label">Direction:</label>
            <select
              id="direction-select"
              className="signals-select"
              value={directionFilter}
              onChange={e => setDirectionFilter(e.target.value as DirectionFilter)}
              aria-label="Filter by direction"
            >
              <option value="ALL">All Directions</option>
              <option value="BULLISH">Bullish / Long ({stats.bullish})</option>
              <option value="BEARISH">Bearish / Short ({stats.bearish})</option>
            </select>
          </div>

          {/* Sort By */}
          <div className="signals-select-group">
            <label htmlFor="sort-select" className="signals-select-label">Sort:</label>
            <select
              id="sort-select"
              className="signals-select"
              value={sortOption}
              onChange={e => setSortOption(e.target.value as SortOption)}
              aria-label="Sort signals"
            >
              <option value="PRIORITY">Priority / Ready First</option>
              <option value="AZ">Symbol A → Z</option>
              <option value="ZA">Symbol Z → A</option>
            </select>
          </div>
        </div>

        {/* Counter & Action Buttons */}
        <div className="signals-filter-actions">
          <span className="signals-matching-count">
            Showing <strong>{filteredPlans.length}</strong> of {totalCount}
          </span>

          {hasActiveFilters && (
            <button
              type="button"
              className="signals-reset-btn"
              onClick={handleResetFilters}
              title="Reset all filters"
            >
              Reset Filters
            </button>
          )}

          <button
            type="button"
            className="signals-refresh-btn"
            onClick={refresh}
            title="Refresh cycle data"
            aria-label="Refresh committed cycle"
          >
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>
      </section>

      {/* Main Signal Cards Grid */}
      <main className="signals-grid" aria-label="Signal research cards">
        {filteredPlans.map(plan => (
          <SignalCard
            key={plan.symbol}
            plan={plan}
            opportunity={oppMap.get(plan.symbol)}
            cycleFresh={isFresh}
          />
        ))}

        {/* Empty State when filtered or when cycle unavailable */}
        {filteredPlans.length === 0 && (
          <div className="signals-empty-card">
            <Radio size={36} className="signals-empty-icon" />
            {totalCount === 0 ? (
              <>
                <h3>No Committed Cycle Signals</h3>
                <p>
                  {error
                    ? error
                    : "Waiting for the scanner scheduler to publish the first unified 15-minute cycle."}
                </p>
                <button
                  type="button"
                  className="signals-refresh-btn"
                  onClick={refresh}
                  style={{ margin: "0 auto" }}
                >
                  <RefreshCw size={13} /> Check Again
                </button>
              </>
            ) : (
              <>
                <h3>No Signals Match Your Filters</h3>
                <p>
                  No directional setups matched your current search and filter combination.
                </p>
                <button
                  type="button"
                  className="signals-reset-btn"
                  onClick={handleResetFilters}
                  style={{ margin: "0 auto" }}
                >
                  Clear Filters
                </button>
              </>
            )}
          </div>
        )}
      </main>

      {/* Safety Policy & Disclaimers Footer */}
      <footer className="signals-disclaimer">
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, color: "#a1a1aa" }}>
          <ShieldCheck size={14} />
          <strong>TradingOS V2 Signal Engine Guardrails</strong>
        </div>
        <p style={{ margin: "0 0 6px" }}>
          All trade plans are derived strictly from closed 15m and 1H historical candles. A WAIT plan indicates
          no qualified liquidity sweep occurred on the last closed candle. A REJECTED plan records structural
          invalidation reasons (e.g. pattern absence or R:R below 2.0). Trade execution levels appear strictly on
          qualified PLAN READY setups.
        </p>
        <p style={{ margin: 0, color: "#71717a" }}>
          DRY_RUN_ONLY mode is enforced. Real and demo order placement is disabled. Stale snapshots must never be treated as actionable signals.
        </p>
      </footer>
    </div>
  );
}
