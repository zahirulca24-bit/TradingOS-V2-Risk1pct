import { API_ROOT } from "./api";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  type LineWidth,
  type UTCTimestamp,
  type LogicalRange
} from "lightweight-charts";
import { Activity, BarChart3, RefreshCw, Database } from "lucide-react";
import "./TradingTerminalPage.css";
import ChartDrawingLayer, { type DrawingProjection } from "./ChartDrawingLayer";
import { NO_VALUE, candleText, priceText, timeText, useCycleFeed, type Opportunity } from "./CycleData";

type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

// TRADINGOS_PHASE3_CHART_EVIDENCE
// Candle markers use only confirmed 2-left / 2-right pivots.
type ChartMarker = {
  time: UTCTimestamp;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
};
function confirmedStructure(candles: Candle[]): ChartMarker[] {
  const result: ChartMarker[] = [];
  let prevHigh: number | null = null;
  let prevLow: number | null = null;
  for (let i = 2; i < candles.length - 2; i++) {
    const candle = candles[i];
    const left = candles.slice(i - 2, i);
    const right = candles.slice(i + 1, i + 3);
    if ([...left,...right].every(c => candle.high > c.high)) {
      const text = prevHigh === null ? "SH" : candle.high > prevHigh ? "HH" : "LH";
      result.push({time:candle.time as UTCTimestamp,position:"aboveBar",color:"#f87171",shape:"circle",text});
      prevHigh=candle.high;
    }
    if ([...left,...right].every(c => candle.low < c.low)) {
      const text = prevLow === null ? "SL" : candle.low > prevLow ? "HL" : "LL";
      result.push({time:candle.time as UTCTimestamp,position:"belowBar",color:"#34d399",shape:"circle",text});
      prevLow=candle.low;
    }
  }
  return result;
}

const API = API_ROOT;

function calculateRSI(data: Candle[], period = 14) {
  const result: { time: UTCTimestamp; value: number }[] = [];
  if (data.length <= period) return result;

  let gain = 0;
  let loss = 0;

  for (let i = 1; i <= period; i++) {
    const delta = data[i].close - data[i - 1].close;
    gain += Math.max(delta, 0);
    loss += Math.max(-delta, 0);
  }

  gain /= period;
  loss /= period;

  function value() {
    if (gain === 0 && loss === 0) return 50;
    if (loss === 0) return 100;
    if (gain === 0) return 0;
    return 100 - 100 / (1 + gain / loss);
  }

  result.push({
    time: data[period].time as UTCTimestamp,
    value: value()
  });

  for (let i = period + 1; i < data.length; i++) {
    const delta = data[i].close - data[i - 1].close;
    gain = (gain * (period - 1) + Math.max(delta, 0)) / period;
    loss = (loss * (period - 1) + Math.max(-delta, 0)) / period;

    result.push({
      time: data[i].time as UTCTimestamp,
      value: value()
    });
  }

  return result;
}

// TRADINGOS_CHART_LAYOUT_V3
// Pane ratios: 65% Candlestick / 20% Volume / 15% RSI
// Use setStretchFactor (proportional, survives resize) rather than
// setHeight (absolute px, fights resize observer and collapses Volume).
// Only apply layout once after series creation — never inside renderData.
const STRETCH_CANDLE = 65;
const STRETCH_VOLUME = 20;
const STRETCH_RSI    = 15;

export default function TradingTerminalPage() {
  // TRADINGOS_MANUAL_DRAWINGS_V1
  // TRADINGOS_CHART_AUTO_SYNC_V1
  const containerRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<LogicalRange | null>(null);
  const loadedKeyRef = useRef("");
  const chartRenderRef = useRef<(() => void) | null>(null);
  const drawingProjectionRef = useRef<DrawingProjection | null>(null);
  const [drawingRevision, setDrawingRevision] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const initialSymbol = searchParams.get("symbol") ?? "BTCUSDT";
  const initialTf = searchParams.get("tf") ?? "15";

  const validSymbol = (s: string) =>
    /^[A-Z0-9]{2,25}USDT$/.test(s);

  const [symbol, setSymbol] = useState(
    validSymbol(initialSymbol) ? initialSymbol : "BTCUSDT"
  );

  const {cycle, error: cycleError, freshness} = useCycleFeed();
  const opportunities: Opportunity[] = cycle
    ? [...cycle.opportunities.top_10, ...cycle.opportunities.remaining]
    : [];
  const rankingTime = cycle?.manifest.candle_close_utc ?? "";
  const rankingError = cycleError;
  const cycleFresh = freshness === "FRESH";
  const [timeframe, setTimeframe] = useState(
    ["15", "60"].includes(initialTf) ? initialTf : "15"
  );
  const [candles, setCandles] = useState<Candle[]>([]);
  const [volumeOn, setVolumeOn] = useState(true);
  const [rsiOn, setRsiOn] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const requestedSymbol = searchParams.get("symbol");
    const requestedTf = searchParams.get("tf");

    if (requestedSymbol && validSymbol(requestedSymbol)) {
      setSymbol(requestedSymbol);
    }
    if (requestedTf && ["15", "60"].includes(requestedTf)) {
      setTimeframe(requestedTf);
    }
  }, [searchParams]);


  function chooseSymbol(nextSymbol: string) {
    if (!validSymbol(nextSymbol)) return;
    if (nextSymbol !== symbol) viewportRef.current = null;
    setSymbol(nextSymbol);
    setSearchParams({ symbol: nextSymbol, tf: timeframe });
  }

  function chooseTimeframe(nextTf: string) {
    if (nextTf !== "15" && nextTf !== "60") return;
    if (nextTf !== timeframe) viewportRef.current = null;
    setTimeframe(nextTf);
    setSearchParams({ symbol, tf: nextTf });
  }

  const selectedOpportunity = opportunities.find(
    (item) => item.symbol === symbol
  );
  const selectedPlan = cycle?.trade_plans.results.find(item => item.symbol === symbol);
  const selectedSweep = cycle?.scanner.results.find(item => item.symbol === symbol);
  const closedCycleSeconds = cycle
    ? Date.parse(cycle.manifest.candle_close_utc) / 1000 : NaN;
  const chartAligned = timeframe === "15" && candles.length > 0 &&
    candles[candles.length - 1].time + 900 === closedCycleSeconds;
  const showEvidence = cycleFresh && chartAligned;
  const planReady = showEvidence && selectedPlan?.status === "PLAN_READY";

  const [watchlistFilter, setWatchlistFilter] = useState<"all" | "waiting">("all");

  type CandidateItem = {
    symbol: string;
    cleanSymbol: string;
    price: number | null;
    direction: string;
    status: string;
    badgeLabel: string;
    badgeType: "ready" | "waiting" | "approaching" | "watch" | "neutral";
    distancePct: number | null;
    subLabel: string;
    supportLevel?: number | null;
    resistanceLevel?: number | null;
    plan?: typeof selectedPlan;
  };

  const candidates = useMemo<CandidateItem[]>(() => {
    const list: CandidateItem[] = [];
    const seen = new Set<string>();

    for (const opp of opportunities) {
      seen.add(opp.symbol);
      const plan = cycle?.trade_plans.results.find(p => p.symbol === opp.symbol);
      const isReady = plan?.status === "PLAN_READY";
      const isWaiting = opp.status === "ENTRY_WATCH";
      const isApproaching = opp.status === "APPROACHING" || opp.status === "NEAR_ZONE";

      let badgeType: CandidateItem["badgeType"] = "watch";
      let badgeLabel = "WATCH";

      if (isReady) {
        badgeType = "ready";
        badgeLabel = "READY TO HIT";
      } else if (isWaiting) {
        badgeType = "waiting";
        badgeLabel = "WAITING TO HIT";
      } else if (isApproaching) {
        badgeType = "approaching";
        badgeLabel = "APPROACHING";
      }

      const distText = typeof opp.distance_pct === "number"
        ? (opp.distance_pct === 0 ? "In key zone" : `${(opp.distance_pct * 100).toFixed(1)}% to zone`)
        : "Monitored";

      list.push({
        symbol: opp.symbol,
        cleanSymbol: opp.symbol.replace("USDT", ""),
        price: opp.price,
        direction: opp.direction,
        status: opp.status,
        badgeLabel,
        badgeType,
        distancePct: opp.distance_pct,
        subLabel: isReady ? `1:${plan?.rr?.toFixed(1) ?? "2.0"} RR` : distText,
        supportLevel: opp.support?.level,
        resistanceLevel: opp.resistance?.level,
        plan
      });
    }

    // Prioritize candidates: ready > waiting > approaching > others
    list.sort((a, b) => {
      const order = { ready: 0, waiting: 1, approaching: 2, watch: 3, neutral: 4 };
      const diff = (order[a.badgeType] ?? 99) - (order[b.badgeType] ?? 99);
      if (diff !== 0) return diff;
      return (a.distancePct ?? 999) - (b.distancePct ?? 999);
    });

    if (symbol && !seen.has(symbol)) {
      list.unshift({
        symbol,
        cleanSymbol: symbol.replace("USDT", ""),
        price: null,
        direction: "NEUTRAL",
        status: "ACTIVE",
        badgeLabel: "ACTIVE",
        badgeType: "neutral",
        distancePct: null,
        subLabel: "Current chart",
      });
    }

    return list;
  }, [opportunities, cycle, symbol]);

  const waitingCount = useMemo(() => {
    return candidates.filter(c => c.badgeType === "ready" || c.badgeType === "waiting").length;
  }, [candidates]);

  const displayedCandidates = useMemo(() => {
    if (watchlistFilter === "waiting") {
      return candidates.filter(
        c => c.badgeType === "ready" || c.badgeType === "waiting" || c.badgeType === "approaching" || c.symbol === symbol
      );
    }
    return candidates;
  }, [candidates, watchlistFilter, symbol]);

  // Poll closed historical candles without sending trading orders.
  useEffect(() => {
    const update = () => {
      if (document.visibilityState === "visible") {
        setRefresh(value => value + 1);
      }
    };

    const timer = window.setInterval(update, 60_000);
    document.addEventListener("visibilitychange", update);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError("");
      const candleKey = `${symbol}:${timeframe}`;
      if (loadedKeyRef.current !== candleKey) {
        loadedKeyRef.current = candleKey;
        setCandles([]);
      }

      try {
        const response = await fetch(
          `${API}/market/candles?symbol=${symbol}&interval=${timeframe}&limit=150`,
          { signal: controller.signal }
        );

        if (!response.ok) throw new Error(`API error: ${response.status}`);

        const result = await response.json();
        const rows = result.candles as Candle[];

        if (!Array.isArray(rows) || rows.length === 0) {
          throw new Error("Bybit returned no closed candles");
        }

        setCandles(rows);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "Data loading failed");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();

    return () => controller.abort();
  }, [symbol, timeframe, refresh]);

  // TRADINGOS_BALANCED_CHART_V3
  // Keep chart instance stable on candle refresh and scanner status updates.
  // Rebuild only for a different symbol/timeframe or indicator visibility change.
  // Pane layout is set once via setStretchFactor (proportional) and not
  // repeated on every renderData call — this prevents the Volume pane from
  // collapsing and eliminates conflicts with the user's own drag-resize.
  const latestVisualRef = useRef({
    candles, selectedOpportunity, selectedPlan, selectedSweep, showEvidence, planReady
  });
  latestVisualRef.current = {
    candles, selectedOpportunity, selectedPlan, selectedSweep, showEvidence, planReady
  };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 650,
      layout: {
        background: { type: ColorType.Solid, color: "#000000" },
        textColor: "#a0a0a0",
        fontFamily: "Segoe UI, Arial, sans-serif",
        panes: {
          separatorColor: "#282828",
          separatorHoverColor: "#555555",
          enableResize: true
        }
      },
      grid: {
        vertLines: { color: "#161616" },
        horzLines: { color: "#161616" }
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#292929" },
      timeScale: {
        borderColor: "#292929",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 7,
        barSpacing: 9
      }
    });

    const price = chart.addSeries(CandlestickSeries, {
      upColor: "#00c878", downColor: "#ff4545",
      borderUpColor: "#00c878", borderDownColor: "#ff4545",
      wickUpColor: "#00c878", wickDownColor: "#ff4545"
    }, 0);

    let nextPane = 1;
    const volume = volumeOn ? chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    }, nextPane++) : null;
    const rsi = rsiOn ? chart.addSeries(LineSeries, {
      color: "#c5a1ff", lineWidth: 2, priceLineVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 }
    }, nextPane) : null;

    // Configure volume price scale for autoscale and clean margins so bars fill pane nicely.
    if (volume) {
      volume.priceScale().applyOptions({
        autoScale: true,
        scaleMargins: {
          top: 0.1,
          bottom: 0,
        },
      });
    }

    if (rsi) {
      rsi.priceScale().applyOptions({
        autoScale: true,
        scaleMargins: {
          top: 0.08,
          bottom: 0.08,
        },
      });
    }

    // Apply proportional pane layout using stretch factors.
    // This is done once after creation; the library maintains the ratio on resize.
    // We do NOT call this inside renderData so candle polling never overrides
    // the user's own pane drag-resizes.
    function applyPaneStretch() {
      const panes = chart.panes();
      if (volumeOn && rsiOn && panes.length >= 3) {
        panes[0].setStretchFactor(STRETCH_CANDLE);
        panes[1].setStretchFactor(STRETCH_VOLUME);
        panes[2].setStretchFactor(STRETCH_RSI);
      } else if (panes.length === 2) {
        panes[0].setStretchFactor(volumeOn ? 80 : 85);
        panes[1].setStretchFactor(volumeOn ? 20 : 15);
      }
    }

    const markerApi = createSeriesMarkers(price, []);
    const priceLines: ReturnType<typeof price.createPriceLine>[] = [];
    let lastVisibleCount = 0;

    drawingProjectionRef.current = {
      toScreen: point => {
        const x = chart.timeScale().timeToCoordinate(point.time as UTCTimestamp);
        const y = price.priceToCoordinate(point.price);
        return x === null || y === null ? null : { x: Number(x), y: Number(y) };
      },
      fromScreen: (x, y) => {
        const logical = chart.timeScale().coordinateToLogical(x);
        const value = price.coordinateToPrice(y);
        const data = latestVisualRef.current.candles;
        if (logical === null || value === null || !Number.isFinite(value) || value <= 0 || !data.length) return null;
        const index = Math.max(0, Math.min(data.length - 1, Math.round(Number(logical))));
        return { time: data[index].time, price: Number(value) };
      },
      pricePaneHeight: () => chart.panes()[0]?.getHeight() ?? 0
    };

    function renderData() {
      const state = latestVisualRef.current;
      const data = state.candles;
      price.setData(data.map(c => ({
        time: c.time as UTCTimestamp,
        open: c.open, high: c.high, low: c.low, close: c.close
      })));
      if (volume) volume.setData(data.map(c => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "#008b59" : "#ae3333"
      })));
      if (rsi) rsi.setData(calculateRSI(data));

      for (const line of priceLines) {
        try { price.removePriceLine(line); } catch { /* ignore */ }
      }
      priceLines.length = 0;

      const addLevel = (
        level: number | null | undefined,
        title: string,
        color: string,
        lineStyle: LineStyle = LineStyle.Dashed,
        lineWidth: LineWidth = 1
      ) => {
        if (typeof level !== "number" || !Number.isFinite(level) || level <= 0) return;
        try {
          priceLines.push(price.createPriceLine({
            price: level,
            color,
            lineWidth,
            lineStyle,
            axisLabelVisible: true,
            title
          }));
        } catch { /* ignore */ }
      };

      const opp = state.selectedOpportunity;
      const plan = state.selectedPlan;

      // 1. Draw Support & Resistance levels analyzed by the bot
      if (opp) {
        const isLong = opp.direction === "LONG";
        const isWaiting = opp.status === "ENTRY_WATCH" || opp.status === "APPROACHING";

        // Support / Demand Zone & Level
        if (opp.support?.level) {
          const suppTitle = isLong && isWaiting
            ? `🟢 DEMAND TARGET ($${opp.support.level.toLocaleString()})`
            : `BOT SUPPORT ($${opp.support.level.toLocaleString()})`;
          addLevel(opp.support.level, suppTitle, "#10b981", LineStyle.Solid, (isLong ? 2 : 1) as LineWidth);

          if (opp.support.upper && Math.abs(opp.support.upper - opp.support.level) > 0.000001) {
            addLevel(opp.support.upper, "DEMAND ZONE HIGH", "#10b98166", LineStyle.Dashed, 1);
          }
          if (opp.support.lower && Math.abs(opp.support.lower - opp.support.level) > 0.000001) {
            addLevel(opp.support.lower, "DEMAND ZONE LOW", "#10b98166", LineStyle.Dashed, 1);
          }
        }

        // Resistance / Supply Zone & Level
        if (opp.resistance?.level) {
          const resTitle = !isLong && isWaiting
            ? `🔴 SUPPLY TARGET ($${opp.resistance.level.toLocaleString()})`
            : `BOT RESISTANCE ($${opp.resistance.level.toLocaleString()})`;
          addLevel(opp.resistance.level, resTitle, "#ef4444", LineStyle.Solid, (!isLong ? 2 : 1) as LineWidth);

          if (opp.resistance.lower && Math.abs(opp.resistance.lower - opp.resistance.level) > 0.000001) {
            addLevel(opp.resistance.lower, "SUPPLY ZONE LOW", "#ef444466", LineStyle.Dashed, 1);
          }
          if (opp.resistance.upper && Math.abs(opp.resistance.upper - opp.resistance.level) > 0.000001) {
            addLevel(opp.resistance.upper, "SUPPLY ZONE HIGH", "#ef444466", LineStyle.Dashed, 1);
          }
        }
      }

      // 2. Draw Trade Plan Levels (Entry Ref, SL, TP, Sweep Level)
      if (plan) {
        if (typeof plan.entry_reference === "number" && plan.entry_reference > 0) {
          addLevel(plan.entry_reference, `PLAN ENTRY ($${plan.entry_reference.toLocaleString()})`, "#38bdf8", LineStyle.Solid, 2);
        }
        if (typeof plan.sl === "number" && plan.sl > 0) {
          addLevel(plan.sl, `PLAN SL ($${plan.sl.toLocaleString()})`, "#ef4444", LineStyle.Solid, 2);
        }
        if (typeof plan.tp === "number" && plan.tp > 0) {
          addLevel(plan.tp, `PLAN TP ($${plan.tp.toLocaleString()})`, "#22c55e", LineStyle.Solid, 2);
        }
        if (typeof plan.sweep_level === "number" && plan.sweep_level > 0) {
          addLevel(plan.sweep_level, `SWEEP TARGET ($${plan.sweep_level.toLocaleString()})`, "#fbbf24", LineStyle.Dashed, 1);
        }
      }

      // 3. Structure & Liquidity Sweep Markers
      const markers: ChartMarker[] = confirmedStructure(data);
      const sweepTime = state.selectedSweep?.candle_time;
      if (typeof sweepTime === "number" && data.some(c => c.time === sweepTime)) {
        markers.push({
          time: sweepTime as UTCTimestamp,
          position: state.selectedSweep?.direction === "SHORT" ? "aboveBar" : "belowBar",
          color: state.selectedPlan?.status === "PLAN_READY" ? "#34d399" : "#fbbf24",
          shape: state.selectedSweep?.direction === "SHORT" ? "arrowDown" : "arrowUp",
          text: state.selectedPlan?.status === "PLAN_READY"
            ? `SWEEP + ${state.selectedPlan.pattern ?? "PATTERN"} (PLAN READY)`
            : "LIQUIDITY SWEEP OBSERVED"
        });
      }
      markers.sort((a, b) => Number(a.time) - Number(b.time));
      markerApi.setMarkers(markers);

      if (data.length && lastVisibleCount === 0) {
        if (viewportRef.current) {
          chart.timeScale().setVisibleLogicalRange(viewportRef.current);
        } else {
          chart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, data.length - 120), to: data.length + 5
          });
        }
      }
      lastVisibleCount = data.length;
      // NOTE: Do NOT call applyPaneStretch() here.
      // Pane layout is set once at creation; calling it on every poll
      // collapses the Volume pane and overrides user's drag-resizes.
      setDrawingRevision(n => n + 1);
    }

    chartRenderRef.current = renderData;
    renderData();
    // Apply stretch ratios after the first render so panes exist with data.
    applyPaneStretch();

    const rangeChanged = (range: LogicalRange | null) => {
      if (range) viewportRef.current = range;
      setDrawingRevision(n => n + 1);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(rangeChanged);

    const observer = new ResizeObserver(() => {
      chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight || 650
      });
      setDrawingRevision(n => n + 1);
    });
    observer.observe(container);

    return () => {
      chartRenderRef.current = null;
      drawingProjectionRef.current = null;
      observer.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(rangeChanged);
      chart.remove();
    };
  }, [symbol, timeframe, volumeOn, rsiOn]);

  useEffect(() => {
    chartRenderRef.current?.();
  }, [candles, selectedOpportunity, selectedPlan, selectedSweep, showEvidence, planReady]);

  const last = candles[candles.length - 1];
  const dateText = last
    ? new Date(last.time * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC"
    : "—";

  return (
    <div className="market-terminal">
      <div className="market-page-title">
        <div>
          <div className="market-eyebrow">MARKET WORKSPACE / REAL HISTORICAL DATA</div>
          <h1>Trading Terminal</h1>
          <p>Bybit USDT Perpetual · 15-minute candles</p>
        </div>
        <div className="market-history-badge">
          <Database size={15} /> HISTORICAL · NOT LIVE
        </div>
      </div>

      <div className="market-grid">
        <section className="market-chart-box">
          <div className="market-toolbar">
            <div className="market-symbol">
              <strong>{symbol}</strong>
              <span>PERPETUAL</span>
              <div className="market-timeframes">
                {[
                  { value: "15", label: "15m" },
                  { value: "60", label: "1H" }
                ].map(tf => (
                  <button
                    key={tf.value}
                    className={timeframe === tf.value ? "selected" : ""}
                    onClick={() => chooseTimeframe(tf.value)}
                  >
                    {tf.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="market-tools">
              <button
                className={volumeOn ? "active" : ""}
                onClick={() => setVolumeOn(v => !v)}
              >
                <BarChart3 size={15} /> Volume {volumeOn ? "✓" : "+"}
              </button>
              <button
                className={rsiOn ? "active" : ""}
                onClick={() => setRsiOn(v => !v)}
              >
                <Activity size={15} /> RSI {rsiOn ? "✓" : "+"}
              </button>
              <button
                onClick={() => setRefresh(n => n + 1)}
                title="Reload local historical candles"
              >
                <RefreshCw size={15} />
              </button>
            </div>
          </div>

          <div className="market-ohlc">
            {last ? (
              <>
                <span>O <strong>{last.open.toLocaleString()}</strong></span>
                <span>H <strong>{last.high.toLocaleString()}</strong></span>
                <span>L <strong>{last.low.toLocaleString()}</strong></span>
                <span>C <strong>{last.close.toLocaleString()}</strong></span>
              </>
            ) : "Waiting for data..."}
          </div>

          {/* Bot Analysis HUD */}
          {selectedOpportunity ? (
            <div className="market-bot-hud">
              <span className={`hud-tag ${selectedOpportunity.direction === "LONG" ? "long" : "short"}`}>
                {selectedOpportunity.direction === "LONG" ? "🟢 LONG BIAS" : "🔴 SHORT BIAS"}
              </span>

              <span className={`hud-tag ${
                selectedPlan?.status === "PLAN_READY" ? "ready" :
                selectedOpportunity.status === "ENTRY_WATCH" ? "waiting" :
                selectedOpportunity.status === "APPROACHING" ? "approaching" : "watch"
              }`}>
                {selectedPlan?.status === "PLAN_READY" ? "🎯 PLAN READY TO HIT" :
                 selectedOpportunity.status === "ENTRY_WATCH" ? "⏳ WAITING TO HIT (IN ZONE)" :
                 selectedOpportunity.status === "APPROACHING" ? "👀 APPROACHING ZONE" :
                 selectedOpportunity.status.replace("_", " ")}
              </span>

              {selectedOpportunity.support && (
                <span className="hud-level-item">
                  Demand: <strong>${selectedOpportunity.support.level.toLocaleString()}</strong>
                </span>
              )}

              {selectedOpportunity.resistance && (
                <span className="hud-level-item">
                  Supply: <strong>${selectedOpportunity.resistance.level.toLocaleString()}</strong>
                </span>
              )}

              {selectedPlan?.entry_reference && (
                <span className="hud-level-item hud-plan">
                  Entry: <strong>${selectedPlan.entry_reference.toLocaleString()}</strong> | 
                  SL: <strong>${selectedPlan.sl?.toLocaleString()}</strong> | 
                  TP: <strong>${selectedPlan.tp?.toLocaleString()}</strong>
                </span>
              )}
            </div>
          ) : (
            <div className="market-bot-hud neutral">
              <span className="hud-tag watch">📊 SYMBOL: {symbol}</span>
              <span className="hud-level-item">Select a candidate from the watchlist to view bot analysis zones.</span>
            </div>
          )}

          <div className="market-chart-area">
            <div ref={containerRef} className="market-chart-canvas" />
            <ChartDrawingLayer key={`${symbol}:${timeframe}`} symbol={symbol}
              timeframe={timeframe} projection={drawingProjectionRef.current}
              revision={drawingRevision} />
            {(loading || error) && (
              <div className="market-chart-overlay">
                <h3>{loading ? "Loading historical candles..." : "Chart data unavailable"}</h3>
                <p>{error || "Loading closed futures candles from Bybit."}</p>
              </div>
            )}
          </div>

          <div style={{
            margin: "12px 12px 4px",
            padding: 14,
            background: "#0c1013",
            border: "1px solid #272c32",
            borderRadius: 7,
            color: "#d4d4d8",
            fontSize: 12,
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              flexWrap: "wrap",
            }}>
              <strong>PRICE ACTION / AUTO LEVELS</strong>
              <span style={{ color: "#a1a1aa" }}>
                Committed cycle {timeText(rankingTime)} · {freshness}
              </span>
            </div>

            {!showEvidence && (
              <p style={{color:"#fbbf24",marginTop:10}}>
                Overlays hidden: {freshness !== "FRESH" ? "committed cycle is stale or unavailable" : "chart is not aligned to the same committed 15m candle"}.
                Refresh candles after the next scan. Historical structure markers remain visible.
              </p>
            )}
            {selectedOpportunity ? (
              <>
                <div style={{
                  display: "flex",
                  gap: 22,
                  flexWrap: "wrap",
                  marginTop: 12,
                }}>
                  <span>
                    1H Trend:{" "}
                    <strong>{selectedOpportunity.trend}</strong>
                  </span>
                  <span>
                    Support:{" "}
                    <strong style={{ color: "#21b981" }}>
                      {priceText(selectedOpportunity.support?.level)}
                    </strong>
                  </span>
                  <span>
                    Resistance:{" "}
                    <strong style={{ color: "#e56b73" }}>
                      {priceText(selectedOpportunity.resistance?.level)}
                    </strong>
                  </span>
                  <span>
                    Status:{" "}
                    <strong>{selectedOpportunity.status}</strong>
                  </span>
                </div>

                <p style={{
                  color: "#a1a1aa",
                  margin: "10px 0 0",
                }}>
                  Zone status: {selectedOpportunity.reason}
                </p>
                <p style={{color:"#d4d4d8",margin:"10px 0 0"}}>
                  Trade Plan: <strong>{selectedPlan?.status ?? NO_VALUE}</strong>
                  {" · "}Reason: {selectedPlan?.reason?.replaceAll("_"," ") ?? NO_VALUE}
                  {" · "}Pattern: {selectedPlan?.pattern ?? NO_VALUE}
                </p>
                <p style={{color:"#a1a1aa",margin:"8px 0 0"}}>
                  Reference Entry: {priceText(selectedPlan?.entry_reference)}
                  {" · "}SL: {priceText(selectedPlan?.sl)}
                  {" · "}TP: {priceText(selectedPlan?.tp)}
                  {" · "}RR: {selectedPlan?.rr ? `1:${selectedPlan.rr.toFixed(2)}` : NO_VALUE}
                  {" · "}Sweep candle: {candleText(selectedSweep?.candle_time)}
                </p>
              </>
            ) : (
              <p style={{ color: "#a1a1aa", marginBottom: 0 }}>
                {rankingError ||
                  "No directional ranking for this symbol. " +
                  "Auto levels are not available."}
              </p>
            )}

            <p style={{
              color: "#fbbf24",
              marginBottom: 0,
              marginTop: 10,
            }}>
              Read-only evidence. Entry/SL/TP overlays appear only for same-cycle,
              fresh PLAN_READY research. They are not orders or current executable quotes.
            </p>
          </div>

          <div className="market-footer">
            <span>Last saved candle: {dateText}</span>
            <span>{candles.length.toLocaleString()} candles loaded</span>
          </div>
        </section>

        <aside className="market-watchlist">
          <div className="market-watchlist-header">
            <strong>Candidates</strong>
            <small>{candidates.length} MONITORED</small>
          </div>
          <div className="market-watchlist-subtitle">BYBIT · USDT PERPETUAL</div>

          <div className="market-watchlist-filter-tabs">
            <button
              className={watchlistFilter === "all" ? "filter-tab active" : "filter-tab"}
              onClick={() => setWatchlistFilter("all")}
            >
              All ({candidates.length})
            </button>
            <button
              className={watchlistFilter === "waiting" ? "filter-tab active" : "filter-tab"}
              onClick={() => setWatchlistFilter("waiting")}
            >
              ⚡ Waiting to Hit ({waitingCount})
            </button>
          </div>

          <div className="market-watchlist-list">
            {displayedCandidates.map(c => {
              const isSelected = symbol === c.symbol;
              const isLong = c.direction === "LONG";
              const displayPrice = isSelected && last
                ? last.close.toLocaleString()
                : (typeof c.price === "number" ? c.price.toLocaleString() : "—");

              return (
                <button
                  key={c.symbol}
                  className={`candidate-card ${isSelected ? "selected" : ""}`}
                  onClick={() => chooseSymbol(c.symbol)}
                >
                  <div className="candidate-left">
                    <div className="candidate-symbol-row">
                      <strong className="cand-sym">{c.cleanSymbol}</strong>
                      <span className={`cand-dir ${isLong ? "dir-long" : "dir-short"}`}>
                        {isLong ? "LONG ▲" : "SHORT ▼"}
                      </span>
                    </div>
                    <span className={`cand-badge badge-${c.badgeType}`}>
                      {c.badgeLabel}
                    </span>
                  </div>

                  <div className="candidate-right">
                    <span className="cand-price">{displayPrice}</span>
                    <small className="cand-sub">{isSelected ? "Active chart" : c.subLabel}</small>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="market-watchlist-note">
            🤖 Auto-drawn analysis: Demand & Supply zones, Entry/SL/TP & structure levels.
          </div>
        </aside>
      </div>
    </div>
  );
}
