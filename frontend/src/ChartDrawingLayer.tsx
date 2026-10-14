import { useEffect, useRef, useState, type MouseEvent } from "react";

export type DrawingPoint = { time: number; price: number };
export type DrawingProjection = {
  toScreen: (point: DrawingPoint) => { x: number; y: number } | null;
  fromScreen: (x: number, y: number) => DrawingPoint | null;
  pricePaneHeight: () => number;
};

type Drawing =
  | { kind: "horizontal"; a: DrawingPoint }
  | { kind: "trend" | "rectangle"; a: DrawingPoint; b: DrawingPoint };
type Tool = "cursor" | "horizontal" | "trend" | "rectangle";

const KEY_PREFIX = "tradingos-v2-drawings-v1:";
function validPoint(p: unknown): p is DrawingPoint {
  if (!p || typeof p !== "object") return false;
  const q = p as Partial<DrawingPoint>;
  return typeof q.time === "number" && Number.isFinite(q.time) &&
    typeof q.price === "number" && Number.isFinite(q.price) && q.price > 0;
}
function load(key: string): Drawing[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(stored)) return [];
    return stored.filter((v): v is Drawing => {
      if (!v || typeof v !== "object") return false;
      const d = v as Partial<Drawing>;
      return (d.kind === "horizontal" && validPoint(d.a)) ||
        ((d.kind === "trend" || d.kind === "rectangle") && validPoint(d.a) &&
          validPoint((d as { b?: DrawingPoint }).b));
    }).slice(0, 100);
  } catch { return []; }
}

export default function ChartDrawingLayer({ symbol, timeframe, projection, revision }: {
  symbol: string;
  timeframe: string;
  projection: DrawingProjection | null;
  revision: number;
}) {
  const key = `${KEY_PREFIX}${symbol}:${timeframe}`;
  const [drawings, setDrawings] = useState<Drawing[]>(() => load(key));
  const [tool, setTool] = useState<Tool>("cursor");
  const [first, setFirst] = useState<DrawingPoint | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(drawings)); } catch { /* storage disabled */ }
  }, [key, drawings]);

  // In pan/cursor mode the SVG must be fully transparent so the lightweight-charts
  // canvas underneath can receive mouse events for panning and zooming.
  // pointer-events is applied via CSS: .tos-drawing-svg has none, .drawing-active has all.
  // We do NOT place a catch-all <rect pointerEvents="all"> inside the SVG because
  // that would block chart interaction even in cursor mode.
  function drawClick(e: MouseEvent<SVGSVGElement>) {
    if (tool === "cursor" || !projection) return;
    const bounds = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - bounds.left;
    const y = e.clientY - bounds.top;
    if (y < 0 || y >= projection.pricePaneHeight()) return;
    const point = projection.fromScreen(x, y);
    if (!point) return;
    if (tool === "horizontal") {
      setDrawings(prev => [...prev.slice(-99), { kind: "horizontal", a: point }]);
      setTool("cursor");
    } else if (!first) {
      setFirst(point);
    } else {
      setDrawings(prev => [...prev.slice(-99), { kind: tool, a: first, b: point }]);
      setFirst(null);
      setTool("cursor");
    }
  }

  void revision;
  const toScreen = (p: DrawingPoint) => projection?.toScreen(p) ?? null;
  const isDrawing = tool !== "cursor";

  return (
    <div className="tos-drawing-root">
      <div className="tos-drawing-toolbar" aria-label="Manual chart drawing tools">
        {([ ["cursor", "Pan"], ["horizontal", "H-Line"], ["trend", "Trend"], ["rectangle", "Zone"] ] as const).map(([id, label]) => (
          <button key={id} type="button" className={tool === id ? "selected" : ""}
            onClick={() => { setTool(id); setFirst(null); }} title={label}>{label}</button>
        ))}
        <button type="button" disabled={!drawings.length} title="Remove most recent drawing"
          onClick={() => { setDrawings(prev => prev.slice(0, -1)); setFirst(null); }}>Undo</button>
        <button type="button" disabled={!drawings.length} title="Clear drawings for this symbol and timeframe"
          onClick={() => { if (window.confirm("Clear drawings for this chart?")) { setDrawings([]); setFirst(null); } }}>Clear</button>
      </div>
      {/* TRADINGOS_DRAWING_POINTER_V3
          SVG is pointer-events:none in pan mode (CSS class controls it).
          No catch-all transparent rect — that would block chart pan/zoom.
          Only individual drawn elements (lines/rects) receive no pointer events. */}
      <svg ref={svgRef} className={`tos-drawing-svg${isDrawing ? " drawing-active" : ""}`}
        onClick={drawClick} aria-label="Click candle chart to draw" role="img">
        {isDrawing && (
          <rect x="0" y="0" width="100%" height="100%" fill="transparent" pointerEvents="all" />
        )}
        {drawings.map((d, i) => {
          const a = toScreen(d.a);
          if (!a) return null;
          if (d.kind === "horizontal") {
            return <line key={i} x1="0" y1={a.y} x2="100%" y2={a.y}
              stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="5 4"
              pointerEvents="none" />;
          }
          const b = toScreen(d.b);
          if (!b) return null;
          if (d.kind === "trend") return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke="#60a5fa" strokeWidth={2} pointerEvents="none" />;
          return <rect key={i} x={Math.min(a.x, b.x)} y={Math.min(a.y, b.y)}
            width={Math.abs(a.x - b.x)} height={Math.abs(a.y - b.y)}
            fill="rgba(96,165,250,.12)" stroke="#60a5fa" strokeWidth={1.5}
            pointerEvents="none" />;
        })}
      </svg>
      {first && <div className="tos-drawing-hint">Choose second point &middot; Pan cancels</div>}
    </div>
  );
}
