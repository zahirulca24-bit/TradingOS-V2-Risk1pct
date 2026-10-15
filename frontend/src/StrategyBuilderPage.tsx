import {
  Blocks, Activity, ShieldCheck, Clock, CheckCircle2,
  AlertCircle, Lock, Target, TrendingUp, Layers
} from "lucide-react";
import "./App.css";

export default function StrategyBuilderPage() {
  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">MODULAR TRADING LOGIC</div>
          <h1>Strategy Builder</h1>
          <p>
            Verified scanner rules, 1H market structure, 15m liquidity sweeps, and 1% risk boundaries.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <span className="outline-pill">
            <CheckCircle2 size={13} style={{ color: "#4ade80" }} /> READ-ONLY SPECIFICATION
          </span>
          <span className="outline-pill" style={{ borderColor: "#444", color: "#888" }}>
            <Lock size={13} /> VISUAL EDITOR: NOT AVAILABLE
          </span>
        </div>
      </div>

      {/* Safety Notice Banner */}
      <div className="info-note" style={{ marginBottom: "24px", background: "#0c0c0c", borderColor: "#262626" }}>
        <ShieldCheck size={18} style={{ color: "#4ade80", flexShrink: 0 }} />
        <div>
          <strong>Deterministic Execution Safety: </strong>
          Interactive rule editing and parameter overrides are disabled in TradingOS V2.
          All scanner signals and trade plans are generated strictly by the verified Python core engine.
        </div>
      </div>

      {/* Strategy Core Architecture Grid */}
      <div className="dashboard-grid">
        {/* Module 1: Timeframes & Universe */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Timeframe & Universe Setup</h2>
              <p>Core environment and scanning cadence</p>
            </div>
            <span className="subtle-tag">CORE CONFIG</span>
          </div>
          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><Layers size={15} /> Asset Universe</span>
              <b>Top 50 USDT Perpetuals (Bybit)</b>
            </div>
            <div className="system-row">
              <span><Clock size={15} /> Higher-Timeframe Trend</span>
              <b>1-Hour (1H) Closed Candles</b>
            </div>
            <div className="system-row">
              <span><Clock size={15} /> Setup / Trigger Timeframe</span>
              <b>15-Minute (15m) Closed Candles</b>
            </div>
            <div className="system-row">
              <span><Activity size={15} /> Cycle Interval</span>
              <b>Every 15m at :00, :15, :30, :45 UTC</b>
            </div>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Lookahead Protection</span>
              <b style={{ color: "#4ade80" }}>Closed Candles Only (Zero Leakage)</b>
            </div>
          </div>
        </section>

        {/* Module 2: 1H Trend Filter */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>1H Market Structure & Trend Logic</h2>
              <p><code>detect_trend()</code> higher-timeframe directional bias</p>
            </div>
            <span className="subtle-tag">TREND FILTER</span>
          </div>
          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><TrendingUp size={15} /> Bullish Structure</span>
              <b style={{ color: "#4ade80" }}>Higher High (HH) + Higher Low (HL)</b>
            </div>
            <div className="system-row">
              <span><TrendingUp size={15} style={{ transform: "rotate(90deg)" }} /> Bearish Structure</span>
              <b style={{ color: "#f87171" }}>Lower High (LH) + Lower Low (LL)</b>
            </div>
            <div className="system-row">
              <span><AlertCircle size={15} /> Neutral / Sideways</span>
              <b style={{ color: "#9299a3" }}>EXCLUDED (NEUTRAL_1H_STRUCTURE)</b>
            </div>
            <div className="system-row">
              <span><Lock size={15} /> Permitted Directions</span>
              <b>BULLISH &rarr; LONG only | BEARISH &rarr; SHORT only</b>
            </div>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Minimum 1H History</span>
              <b>&ge; 5 Closed 1H Candles Required</b>
            </div>
          </div>
        </section>
      </div>

      <div className="dashboard-grid" style={{ marginTop: "24px" }}>
        {/* Module 3: Liquidity Sweeps */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>15m Liquidity Sweep Protocol</h2>
              <p><code>find_confirmed_levels()</code> pivot validation</p>
            </div>
            <span className="subtle-tag">TRIGGER LOGIC</span>
          </div>
          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><Target size={15} /> Swing Level Confirmation</span>
              <b>Confirmed strictly before signal candle</b>
            </div>
            <div className="system-row">
              <span><Activity size={15} /> Long Liquidity Sweep</span>
              <b>Low &lt; Support level AND Close &gt; Support level</b>
            </div>
            <div className="system-row">
              <span><Activity size={15} /> Short Liquidity Sweep</span>
              <b>High &gt; Resistance level AND Close &lt; Resistance level</b>
            </div>
            <div className="system-row">
              <span><AlertCircle size={15} /> Unswept Candle Action</span>
              <b style={{ color: "#fbbf24" }}>WAIT (NO_SWEEP_ON_LAST_CLOSED_CANDLE)</b>
            </div>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Minimum 15m History</span>
              <b>&ge; 6 Closed 15m Candles Required</b>
            </div>
          </div>
        </section>

        {/* Module 4: Candle Pattern Confirmation */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Candle Pattern Confirmation</h2>
              <p><code>detect_pattern()</code> sweep validation</p>
            </div>
            <span className="subtle-tag">CONFIRMATION</span>
          </div>
          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><Activity size={15} /> Pin Bar Rejection</span>
              <b>Wick &ge; 2&times; Body, Wick &gt; Opposing Wick, Body &ge; 5% Range</b>
            </div>
            <div className="system-row">
              <span><Activity size={15} /> Engulfing Candle</span>
              <b>Current body completely covers previous opposing body</b>
            </div>
            <div className="system-row">
              <span><AlertCircle size={15} /> Missing Pattern Policy</span>
              <b style={{ color: "#f87171" }}>REJECTED (SWEEP_WITHOUT_REQUIRED_PATTERN)</b>
            </div>
            <div className="system-row">
              <span><Lock size={15} /> Doji Protection</span>
              <b>Tiny body (&lt; 5% range) excluded from pin bars</b>
            </div>
            <div className="system-row">
              <span><CheckCircle2 size={15} /> Direction Matching</span>
              <b>Bullish pattern for LONG, Bearish for SHORT</b>
            </div>
          </div>
        </section>
      </div>

      <div className="dashboard-grid" style={{ marginTop: "24px" }}>
        {/* Module 5: Trade Plan & 1% Risk Engine */}
        <section className="panel dashboard-main">
          <div className="panel-header">
            <div>
              <h2>Trade Plan &amp; Risk Parameters</h2>
              <p><code>build_trade_plan()</code> &amp; <code>demo_risk_engine.py</code> verified rules</p>
            </div>
            <span className="subtle-tag">RISK SAFEGUARDS</span>
          </div>
          <div style={{ padding: "20px", display: "grid", gap: "14px", fontSize: "13px" }}>
            <div className="system-row">
              <span><Target size={15} /> Entry Reference</span>
              <b>Close price of the triggering 15m candle</b>
            </div>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Stop Loss Level</span>
              <b>Signal candle extreme &plusmn; 0.1% buffer (Low for Long, High for Short)</b>
            </div>
            <div className="system-row">
              <span><Target size={15} /> Take Profit Level</span>
              <b>Closest confirmed opposing swing level</b>
            </div>
            <div className="system-row">
              <span><AlertCircle size={15} /> Minimum Risk / Reward Ratio</span>
              <b style={{ color: "#4ade80" }}>&ge; 2.0 (Strict - trades with R/R &lt; 2.0 REJECTED)</b>
            </div>
            <div className="system-row">
              <span><Activity size={15} /> Account Risk Budget</span>
              <b style={{ color: "#4ade80" }}>Strictly 1.0% equity per trade leg</b>
            </div>
            <div className="system-row">
              <span><Lock size={15} /> Max Exposure Capacity</span>
              <b>Maximum 3 concurrent open positions + pending entry orders</b>
            </div>
            <div className="system-row">
              <span><ShieldCheck size={15} /> Execution Status</span>
              <b style={{ color: "#fbbf24" }}>DRY_RUN_ONLY / ORDER_EXECUTION_DISABLED</b>
            </div>
          </div>
        </section>

        {/* Module 6: Unimplemented Visual Editor Panel */}
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Interactive Strategy Editor</h2>
              <p>Visual rule customization &amp; parameter overrides</p>
            </div>
            <span className="subtle-tag" style={{ borderColor: "#ef444444", color: "#f87171" }}>NOT AVAILABLE</span>
          </div>
          <div style={{ padding: "24px", display: "grid", gap: "16px", color: "#9299a3", fontSize: "13px" }}>
            <div style={{
              background: "#080808",
              border: "1px dashed #282828",
              borderRadius: "8px",
              padding: "20px",
              textAlign: "center"
            }}>
              <Blocks size={32} style={{ color: "#555", margin: "0 auto 12px" }} />
              <div style={{ color: "#dedede", fontWeight: 600, marginBottom: "6px" }}>
                Visual Rule Builder Disabled
              </div>
              <p style={{ margin: 0, fontSize: "12px", lineHeight: "1.6" }}>
                Drag-and-drop rule creation, slider-based parameter tweaking, and interactive rule editing
                are deliberately omitted in this release to preserve immutable, deterministic verification of the 1% risk model.
              </p>
            </div>

            <div style={{ display: "grid", gap: "10px", fontSize: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", color: "#666" }}>
                <span>Custom Indicator Rules:</span>
                <span className="status-label" style={{ fontSize: "9px" }}>NOT AVAILABLE</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", color: "#666" }}>
                <span>Interactive Backtesting:</span>
                <span className="status-label" style={{ fontSize: "9px" }}>NOT AVAILABLE</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", color: "#666" }}>
                <span>Live Rule Modification:</span>
                <span className="status-label" style={{ fontSize: "9px" }}>DISABLED</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
