import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, Database, ArrowUpRight } from "lucide-react";
import "./ScannerPage.css";

type Market = {
  symbol: string;
  timeframe: string;
  price: number;
  candle_change_pct: number;
  rsi14: number | null;
  volume: number;
  average_volume20: number;
  volume_ratio: number | null;
  candle_time: number;
  data_age_seconds: number;
  trend: null;
  signal: null;
  data_type: string;
};

type ScannerResponse = {
  source: string;
  is_live: boolean;
  count: number;
  markets: Market[];
};

const API = "http://127.0.0.1:8765";

const tfLabels: Record<string, string> = {
  "15": "15m",
  "60": "1H",
  "240": "4H",
};

export default function ScannerPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<Market[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [symbol, setSymbol] = useState("ALL");
  const [timeframe, setTimeframe] = useState("ALL");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${API}/api/scanner`, { signal });
      if (!response.ok) throw new Error(`API error ${response.status}`);

      const data: ScannerResponse = await response.json();
      if (!Array.isArray(data.markets)) {
        throw new Error("Invalid scanner response");
      }

      setRows(data.markets);
    } catch (e) {
      if (signal?.aborted) return;
      setError(e instanceof Error ? e.message : "Scanner unavailable");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const filtered = rows.filter(row =>
    (symbol === "ALL" || row.symbol === symbol) &&
    (timeframe === "ALL" || row.timeframe === timeframe)
  );

  const number = (n: number, digits = 2) =>
    n.toLocaleString("en-US", { maximumFractionDigits: digits });

  return (
    <div className="scanner-workspace">
      <div className="scanner-heading">
        <div>
          <span className="scanner-eyebrow">MARKET INTELLIGENCE</span>
          <h1>Market Scanner</h1>
          <p>BTC · ETH · SOL — Historical multi-timeframe market measurements.</p>
        </div>
        <span className="scanner-historical"><Database size={14} /> HISTORICAL · NOT LIVE</span>
      </div>

      <div className="scanner-controls">
        <div className="scanner-selectors">
          <select value={symbol} onChange={e => setSymbol(e.target.value)}>
            <option value="ALL">All Symbols</option>
            <option value="BTCUSDT">BTCUSDT</option>
            <option value="ETHUSDT">ETHUSDT</option>
            <option value="SOLUSDT">SOLUSDT</option>
          </select>

          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            <option value="ALL">All Timeframes</option>
            <option value="15">15m</option>
            <option value="60">1H</option>
            <option value="240">4H</option>
          </select>
        </div>

        <button onClick={() => void load()} disabled={loading}>
          <RefreshCw size={15} /> {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      <section className="scanner-table-panel">
        <div className="scanner-table-heading">
          <div>
            <h2>Market Overview</h2>
            <p>All measurements use completed historical candles.</p>
          </div>
          <span>{filtered.length} ROWS</span>
        </div>

        {error && <div className="scanner-error">{error} — Check Backend Port 8765.</div>}

        <div className="scanner-table-scroll">
          <table className="scanner-table">
            <thead>
              <tr>
                <th>SYMBOL</th>
                <th>TIMEFRAME</th>
                <th>SAVED CLOSE</th>
                <th>CANDLE %</th>
                <th>RSI (14)</th>
                <th>VOLUME / AVG20</th>
                <th>DATA TIME (UTC)</th>
                <th>TREND</th>
                <th>SIGNAL</th>
                <th>CHART</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(row => (
                <tr key={`${row.symbol}-${row.timeframe}`}>
                  <td className="scanner-symbol">{row.symbol}</td>
                  <td>{tfLabels[row.timeframe]}</td>
                  <td>{number(row.price, 4)}</td>
                  <td className={row.candle_change_pct >= 0 ? "scanner-green" : "scanner-red"}>
                    {row.candle_change_pct >= 0 ? "+" : ""}
                    {row.candle_change_pct.toFixed(3)}%
                  </td>
                  <td>{row.rsi14 === null ? "—" : row.rsi14.toFixed(2)}</td>
                  <td>{row.volume_ratio === null ? "—" : `${row.volume_ratio.toFixed(2)}×`}</td>
                  <td>
                    {new Date(row.candle_time * 1000)
                      .toISOString().slice(0, 16).replace("T", " ")}
                  </td>
                  <td className="scanner-muted">Not configured</td>
                  <td className="scanner-muted">No rules</td>
                  <td>
                    <button
                      className="scanner-chart-link"
                      onClick={() => navigate(
                        `/terminal?symbol=${row.symbol}&tf=${row.timeframe}`
                      )}
                    >
                      Open <ArrowUpRight size={13} />
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && !error && filtered.length === 0 && (
                <tr><td colSpan={10} className="scanner-empty">No matching market rows.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <div className="scanner-footnote">
        Historical scanner only. Trend and trade signals stay disabled until you define strategy rules.
        Scanner Refresh re-reads saved database data; it does not download new market candles.
      </div>
    </div>
  );
}
