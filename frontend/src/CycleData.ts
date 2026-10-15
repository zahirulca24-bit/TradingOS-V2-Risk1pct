import { API_ROOT } from "./api";
// TradingOS Phase 3: one committed read-only Phase 2 cycle for every page.
import { useEffect, useState } from "react";

export { API_ROOT } from "./api";
export const NO_VALUE = "\u2014";

export type PlanStatus = "PLAN_READY" | "WAIT" | "REJECTED" | "EXCLUDED";
export type Zone = { type: string; level: number; lower: number; upper: number };
export type ScanRow = {
  symbol: string; trend: string; status: string;
  direction: string | null; level: number | null;
  close: number | null; candle_time: number | null;
};
export type PlanRow = {
  symbol: string; trend: string; status: PlanStatus; reason: string;
  direction?: string | null; pattern?: string | null;
  sweep_level?: number | null; entry_reference?: number | null;
  sl?: number | null; tp?: number | null; rr?: number | null;
  candle_time?: number | null; valid_after?: number | null;
};
export type Opportunity = {
  symbol: string; trend: string; direction: string; status: string;
  reason: string; price: number; candle_time: number;
  support: Zone | null; resistance: Zone | null;
  distance_pct: number | null; range_progress_pct: number | null;
  trade_plan_status?: string; trade_plan_reason?: string;
};
export type CycleBundle = {
  manifest: { cycle_id: string; candle_close_utc: string; committed_at: string; status: string };
  scanner: {
    cycle_id: string; status: string; generated_at: string;
    total_scanned: number; universe_examined?: number; directional_count: number;
    neutral_excluded: number; candidate_count: number;
    trading_enabled: boolean; results: ScanRow[];
  };
  trade_plans: {
    cycle_id: string; status: string; ready_count: number;
    wait_count: number; rejected_count: number;
    trading_enabled: boolean; results: PlanRow[];
  };
  opportunities: {
    cycle_id: string; status: string; generated_at: string;
    directional_count: number; top_count: number;
    trading_enabled: boolean; top_10: Opportunity[]; remaining: Opportunity[];
  };
};
export type PipelineStatus = {
  last_committed_cycle: CycleBundle["manifest"] | null;
  last_attempt: { status: string; at: string; reason?: string } | null;
  trading_enabled: boolean;
};

export function assertCycle(data: unknown): CycleBundle {
  if (!data || typeof data !== "object") throw new Error("No complete committed cycle");
  const c = data as CycleBundle;
  const id = c.manifest?.cycle_id;
  if (!id || c.manifest.status !== "success" ||
      c.scanner?.cycle_id !== id || c.trade_plans?.cycle_id !== id ||
      c.opportunities?.cycle_id !== id ||
      c.scanner.status !== "success" || c.trade_plans.status !== "success" ||
      c.opportunities.status !== "success" ||
      c.scanner.trading_enabled !== false ||
      c.trade_plans.trading_enabled !== false ||
      c.opportunities.trading_enabled !== false ||
      !Array.isArray(c.scanner.results) ||
      !Array.isArray(c.trade_plans.results) ||
      !Array.isArray(c.opportunities.top_10) ||
      !Array.isArray(c.opportunities.remaining)) {
    throw new Error("Cycle reports are not a matching, read-only commit");
  }
  const keys = (items: {symbol: string}[]) => items.map(x => x.symbol).sort().join("|");
  if (keys(c.scanner.results) !== keys(c.trade_plans.results) ||
      keys(c.scanner.results) !== keys([...c.opportunities.top_10, ...c.opportunities.remaining])) {
    throw new Error("Cycle symbol membership differs");
  }
  return c;
}

export type CycleFreshness = "FRESH" | "STALE" | "UNAVAILABLE";
export function cycleFreshness(cycle: CycleBundle | null, now = Date.now()): CycleFreshness {
  if (!cycle) return "UNAVAILABLE";
  const closed = Date.parse(cycle.manifest.candle_close_utc);
  if (!Number.isFinite(closed) || closed > now + 60_000 || now - closed > 30 * 60_000) return "STALE";
  return "FRESH";
}
export function timeText(input?: string | null) {
  if (!input) return NO_VALUE;
  const parsed = Date.parse(input);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString().replace("T", " ").slice(0, 16) + " UTC" : NO_VALUE;
}
export function candleText(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE;
  return new Date(value * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}
export function priceText(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return NO_VALUE;
  return Number(value.toPrecision(9)).toString();
}
export function countdownToClose(now: number) {
  const interval = 900_000;
  const milliseconds = interval - ((now % interval + interval) % interval);
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function useCycleFeed() {
  const [cycle, setCycle] = useState<CycleBundle | null>(null);
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let busy = false;
    let controller: AbortController | null = null;

    async function fetchCycle() {
      if (!active || busy) return;
      busy = true;
      controller = new AbortController();

      try {
        const response = await fetch(`${API_ROOT}/scanner/cycle`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Committed cycle unavailable (HTTP ${response.status})`);
        }
        const next = assertCycle(await response.json());
        if (active) {
          setCycle(next);
          setError("");
        }
      } catch (e) {
        if (active) {
          setCycle(null);
          setError(e instanceof Error ? e.message : "Cycle API unavailable");
        }
      }

      try {
        const response = await fetch(`${API_ROOT}/scanner/pipeline-status`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`Status HTTP ${response.status}`);
        const status: PipelineStatus = await response.json();
        if (active) setPipeline(status);
      } catch {
        if (active) setPipeline(null);
      } finally {
        busy = false;
      }
    }

    const refreshOnFocus = () => {
      if (document.visibilityState === "visible") void fetchCycle();
    };

    void fetchCycle();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void fetchCycle();
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
  }, [revision]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return {
    cycle, pipeline, error, now, freshness: cycleFreshness(cycle, now),
    refresh: () => setRevision(v => v + 1),
  };
}
