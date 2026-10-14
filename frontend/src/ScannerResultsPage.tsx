
import { useEffect, useState } from "react";

const API = "http://127.0.0.1:8765";

type ScannerRow = {
  symbol: string;
  trend: string;
  status: string;
  direction: string | null;
  level: number | null;
  close: number | null;
  candle_time: number | null;
};

type ScannerSnapshot = {
  generated_at: string;
  total_scanned: number;
  candidate_count: number;
  failures: { symbol: string; error: string }[];
  results: ScannerRow[];
};

const panel: React.CSSProperties = {
  background: "#090b0e",
  border: "1px solid #25282d",
  borderRadius: 9,
  padding: 20,
};

function utcTime(value: number | null) {
  if (value === null) return "?";
  return new Date(value * 1000)
    .toISOString()
    .replace("T", " ")
    .slice(0, 16) + " UTC";
}

function price(value: number | null) {
  return value === null ? "?" : String(value);
}

export default function ScannerResultsPage() {
  const [snapshot, setSnapshot] =
    useState<ScannerSnapshot | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function loadResults() {
      try {
        const response = await fetch(
          `${API}/scanner/results`,
          {
            signal: controller.signal,
            cache: "no-store",
          }
        );

        if (!response.ok) {
          throw new Error(`Scanner API: HTTP ${response.status}`);
        }

        const data: ScannerSnapshot = await response.json();

        if (!Array.isArray(data.results)) {
          throw new Error("Invalid scanner response");
        }

        if (!controller.signal.aborted) {
          setSnapshot(data);
          setError("");
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(
            err instanceof Error ? err.message : "API unavailable"
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void loadResults();

    const timer = window.setInterval(() => {
      void loadResults();
    }, 15000);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refreshKey]);

  const rows = snapshot?.results ?? [];

  const visibleRows = rows.filter((item) => {
    if (filter === "ALL") return true;
    if (filter === "CANDIDATE") {
      return item.status === "CANDIDATE";
    }
    return item.trend === filter;
  });

  const generatedAt = snapshot
    ? new Date(snapshot.generated_at).getTime()
    : NaN;

  const ageMinutes = Number.isFinite(generatedAt)
    ? Math.floor((Date.now() - generatedAt) / 60000)
    : null;

  const isOld = ageMinutes === null || ageMinutes > 30;

  return (
    <div style={{ color: "#f4f4f5", padding: "8px 4px" }}>
      <p style={{
        color: "#8b929b",
        fontSize: 11,
        letterSpacing: 2,
      }}>
        MARKET INTELLIGENCE / BYBIT FUTURES
      </p>

      <h1 style={{ fontSize: 27, margin: "12px 0" }}>
        Market Scanner
      </h1>

      <p style={{ color: "#a1a1aa", fontSize: 13 }}>
        50 Symbols ? 1H Market Structure ? 15m Liquidity Sweep
      </p>

      <p style={{ color: "#fbbf24", fontSize: 12 }}>
        Saved scanner snapshot ? not live trade signals.
        No orders are placed here.
      </p>

      {snapshot && (
        <p style={{ color: "#a1a1aa", fontSize: 12 }}>
          Last scan: {snapshot.generated_at}
          {ageMinutes !== null && (
            <> ? Age: {Math.max(0, ageMinutes)} minutes</>
          )}
          {isOld && (
            <strong style={{ color: "#f59e0b" }}>
              {" "}? STALE DATA
            </strong>
          )}
        </p>
      )}

      {error && (
        <div style={{
          ...panel,
          color: "#f87171",
          marginTop: 15,
        }}>
          API Error: {error}
        </div>
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
        gap: 12,
        margin: "24px 0",
      }}>
        <div style={panel}>
          <div style={{ color: "#a1a1aa", fontSize: 12 }}>
            Scanned
          </div>
          <h2>{snapshot?.total_scanned ?? "?"}</h2>
        </div>

        <div style={panel}>
          <div style={{ color: "#a1a1aa", fontSize: 12 }}>
            Candidates
          </div>
          <h2>{snapshot?.candidate_count ?? "?"}</h2>
        </div>

        <div style={panel}>
          <div style={{ color: "#a1a1aa", fontSize: 12 }}>
            Errors
          </div>
          <h2>{snapshot?.failures?.length ?? "?"}</h2>
        </div>
      </div>

      <div style={{
        display: "flex",
        gap: 10,
        flexWrap: "wrap",
        marginBottom: 16,
      }}>
        <select
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          style={{
            background: "#15171a",
            color: "white",
            border: "1px solid #34373d",
            borderRadius: 6,
            padding: "10px 12px",
          }}
        >
          <option value="ALL">All Symbols</option>
          <option value="CANDIDATE">Candidates Only</option>
          <option value="BULLISH">Bullish Structure</option>
          <option value="BEARISH">Bearish Structure</option>
          <option value="NEUTRAL">Neutral Structure</option>
        </select>

        <button
          onClick={() => setRefreshKey((value) => value + 1)}
          style={{
            background: "#202327",
            color: "white",
            border: "1px solid #34373d",
            borderRadius: 6,
            padding: "10px 16px",
            cursor: "pointer",
          }}
        >
          Refresh Snapshot
        </button>
      </div>

      <div style={{ ...panel, overflowX: "auto" }}>
        {loading && !snapshot ? (
          <p>Loading scanner results...</p>
        ) : (
          <table style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 13,
            textAlign: "left",
            whiteSpace: "nowrap",
          }}>
            <thead>
              <tr style={{
                color: "#a1a1aa",
                borderBottom: "1px solid #303238",
              }}>
                {[
                  "Symbol", "1H Trend", "Status",
                  "Direction", "Sweep Level",
                  "Close", "Candle Time",
                ].map((name) => (
                  <th key={name} style={{ padding: 12 }}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {visibleRows.map((item) => (
                <tr
                  key={item.symbol}
                  style={{ borderBottom: "1px solid #202327" }}
                >
                  <td style={{ padding: 12, fontWeight: 600 }}>
                    {item.symbol}
                  </td>

                  <td style={{
                    padding: 12,
                    color: item.trend === "BULLISH"
                      ? "#22c55e"
                      : item.trend === "BEARISH"
                      ? "#ef4444" : "#a1a1aa",
                  }}>
                    {item.trend}
                  </td>

                  <td style={{ padding: 12 }}>
                    {item.status}
                  </td>

                  <td style={{
                    padding: 12,
                    color: item.direction === "LONG"
                      ? "#22c55e"
                      : item.direction === "SHORT"
                      ? "#ef4444" : "#a1a1aa",
                    fontWeight: 600,
                  }}>
                    {item.direction ?? "WAIT"}
                  </td>

                  <td style={{ padding: 12 }}>
                    {price(item.level)}
                  </td>

                  <td style={{ padding: 12 }}>
                    {price(item.close)}
                  </td>

                  <td style={{ padding: 12, color: "#a1a1aa" }}>
                    {utcTime(item.candle_time)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!loading && visibleRows.length === 0 && (
          <p style={{ color: "#a1a1aa" }}>
            No matching results in this snapshot.
          </p>
        )}
      </div>

      <p style={{
        color: "#71717a",
        fontSize: 12,
        marginTop: 14,
      }}>
        Refresh reads the saved JSON only. It does not run
        a new market scan.
      </p>
    </div>
  );
}
