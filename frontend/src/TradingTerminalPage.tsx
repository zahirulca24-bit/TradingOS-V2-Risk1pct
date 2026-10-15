import { API_ROOT } from "./api";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
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
const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

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
  // Only draw Phase 2 evidence on a 15m chart that contains the SAME closed candle.
  const chartAligned = timeframe === "15" && candles.length > 0 &&
    candles[candles.length - 1].time + 900 === closedCycleSeconds;
  const showEvidence = cycleFresh && chartAligned;
  const planReady = showEvidence && selectedPlan?.status === "PLAN_READY";

  const watchSymbols = Array.from(new Set([
    symbol,
    ...opportunities.slice(0, 10).map((item) => item.symbol),
    ...SYMBOLS,
  ]));

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

      for (const line of priceLines) price.removePriceLine(line);
      priceLines.length = 0;
      const addLevel = (level: number | null | undefined, title: string, color: string) => {
        if (typeof level !== "number" || !Number.isFinite(level)) return;
        priceLines.push(price.createPriceLine({
          price: level, color, lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title
        }));
      };
      if (state.showEvidence && state.selectedOpportunity) {
        addLevel(state.selectedOpportunity.support?.level, "AUTO SUPPORT", "#21b981");
        addLevel(state.selectedOpportunity.resistance?.level, "AUTO RESISTANCE", "#e56b73");
      }
      if (state.planReady && state.selectedPlan) {
        addLevel(state.selectedPlan.entry_reference, "ENTRY REF", "#60a5fa");
        addLevel(state.selectedPlan.sl, "SL", "#ef4444");
        addLevel(state.selectedPlan.tp, "TP", "#22c55e");
      }

      const markers: ChartMarker[] = confirmedStructure(data);
      const sweepTime = state.selectedSweep?.candle_time;
      if (state.showEvidence && typeof sweepTime === "number" &&
          data.some(c => c.time === sweepTime)) {
        markers.push({
          time: sweepTime as UTCTimestamp,
          position: state.selectedSweep?.direction === "SHORT" ? "aboveBar" : "belowBar",
          color: state.selectedPlan?.status === "PLAN_READY" ? "#34d399" : "#fbbf24",
          shape: state.selectedSweep?.direction === "SHORT" ? "arrowDown" : "arrowUp",
          text: state.selectedPlan?.status === "PLAN_READY"
            ? `SWEEP + ${state.selectedPlan.pattern ?? "PATTERN"} (PLAN)`
            : "SWEEP (NOT ENTRY)"
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
                      {showEvidence ? priceText(selectedOpportunity.support?.level) : NO_VALUE}
                    </strong>
                  </span>
                  <span>
                    Resistance:{" "}
                    <strong style={{ color: "#e56b73" }}>
                      {showEvidence ? priceText(selectedOpportunity.resistance?.level) : NO_VALUE}
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
                  Reference Entry: {showEvidence ? priceText(selectedPlan?.entry_reference) : NO_VALUE}
                  {" · "}SL: {showEvidence ? priceText(selectedPlan?.sl) : NO_VALUE}
                  {" · "}TP: {showEvidence ? priceText(selectedPlan?.tp) : NO_VALUE}
                  {" · "}RR: {showEvidence ? priceText(selectedPlan?.rr) : NO_VALUE}
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
            <strong>Watchlist</strong>
            <small>{watchSymbols.length} SYMBOLS</small>
          </div>
          <div className="market-watchlist-subtitle">BYBIT · USDT PERPETUAL</div>

          {watchSymbols.map(s => (
            <button
              key={s}
              className={symbol === s ? "selected" : ""}
              onClick={() => chooseSymbol(s)}
            >
              <span>
                <strong>{s.replace("USDT", "")}</strong>
                <small>USDT</small>
              </span>
              <span>
                {symbol === s && last ? last.close.toLocaleString() : "—"}
                <small>{symbol === s ? "Saved close" : "Select symbol"}</small>
              </span>
            </button>
          ))}

          <div className="market-watchlist-note">
            Prices are saved historical closes, not live quotes.
          </div>
        </aside>
      </div>
    </div>
  );
}
