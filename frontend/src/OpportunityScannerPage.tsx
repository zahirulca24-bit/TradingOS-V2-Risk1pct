import { useState } from "react";
import { Link } from "react-router-dom";
import { NO_VALUE, countdownToClose, priceText, timeText, useCycleFeed, type Opportunity, type PlanRow } from "./CycleData";

const box: React.CSSProperties = {background:"#0a0c0f",border:"1px solid #292b30",borderRadius:9,padding:17};
const text: React.CSSProperties = {color:"#a1a1aa",fontSize:12};

function OpportunityRows({rows, planIndex}: {rows: Opportunity[];planIndex: Map<string,PlanRow>}) {
  if (!rows.length) return <p style={text}>No matching directional opportunities.</p>;
  return <div style={{overflowX:"auto"}}><table style={{width:"100%",borderCollapse:"collapse",whiteSpace:"nowrap",fontSize:12,textAlign:"left"}}>
    <thead><tr style={{color:"#a1a1aa",borderBottom:"1px solid #303238"}}>
      {["Symbol","1H","Zone Status","Support","Resistance","Distance","Plan / Why","Entry Ref","SL","TP","RR","Chart"].map(x=><th key={x} style={{padding:"11px 9px"}}>{x}</th>)}
    </tr></thead>
    <tbody>{rows.map(row=>{
      const plan=planIndex.get(row.symbol);
      return <tr key={row.symbol} style={{borderBottom:"1px solid #25272c"}}>
        <td style={{padding:9,fontWeight:700}}>{row.symbol}</td>
        <td style={{padding:9,color:row.trend==="BULLISH"?"#22c55e":"#f87171"}}>{row.trend}</td>
        <td style={{padding:9,color:row.status==="ENTRY_WATCH"?"#fbbf24":"#d4d4d8"}}>{row.status.replaceAll("_"," ")}</td>
        <td style={{padding:9,color:"#22c55e"}}>{priceText(row.support?.level)}</td>
        <td style={{padding:9,color:"#f87171"}}>{priceText(row.resistance?.level)}</td>
        <td style={{padding:9}}>{row.distance_pct == null ? NO_VALUE : `${row.distance_pct.toFixed(3)}%`}</td>
        <td style={{padding:9,maxWidth:330,whiteSpace:"normal",lineHeight:1.5}}>
          <strong style={{color:plan?.status==="PLAN_READY"?"#22c55e":plan?.status==="REJECTED"?"#f87171":"#a1a1aa"}}>{plan?.status ?? "NO PLAN"}</strong>
          <div style={text}>{plan?.reason?.replaceAll("_"," ") ?? NO_VALUE}</div>
          <div style={text}>{row.reason}</div>
        </td>
        <td style={{padding:9}}>{priceText(plan?.entry_reference)}</td>
        <td style={{padding:9}}>{priceText(plan?.sl)}</td>
        <td style={{padding:9}}>{priceText(plan?.tp)}</td>
        <td style={{padding:9}}>{priceText(plan?.rr)}</td>
        <td style={{padding:9}}><Link style={{color:"#93c5fd"}} to={`/terminal?symbol=${encodeURIComponent(row.symbol)}&tf=15`}>Open Chart ↗</Link></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

export default function OpportunityScannerPage(){
  const {cycle,pipeline,error,freshness,now,refresh}=useCycleFeed();
  const [expanded,setExpanded]=useState(false);
  const plans=new Map((cycle?.trade_plans.results ?? []).map(p=>[p.symbol,p]));
  const top=cycle?.opportunities.top_10 ?? [];
  const rest=cycle?.opportunities.remaining ?? [];
  return <div style={{color:"#f4f4f5",padding:"8px 4px"}}>
    <p style={{...text,letterSpacing:2}}>MARKET INTELLIGENCE / COMMITTED 15M CYCLE</p>
    <h1 style={{fontSize:27,margin:"12px 0"}}>Top 10 Opportunity Scanner</h1>
    <p style={{fontSize:12,color:"#fbbf24"}}>ENTRY WATCH means price is near a zone, not an approved entry. Planned prices are research references only; no orders.</p>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(160px,1fr))",gap:12,margin:"20px 0"}}>
      {[
        ["Directional",cycle?.opportunities.directional_count ?? NO_VALUE],
        ["Top Ranked",top.length || (cycle?0:NO_VALUE)],
        ["Remaining",rest.length || (cycle?0:NO_VALUE)],
        ["Plan Ready",cycle?.trade_plans.ready_count ?? NO_VALUE],
        ["Next 15m Close",countdownToClose(now)],
      ].map(([label,value])=><div key={String(label)} style={box}><div style={text}>{label}</div><h2 style={{marginBottom:0,fontVariantNumeric:"tabular-nums"}}>{value}</h2></div>)}
    </div>
    <p style={text}>Committed candle: {timeText(cycle?.manifest.candle_close_utc)} · Cycle: {cycle?.manifest.cycle_id ?? NO_VALUE} · Status: <strong style={{color:freshness==="FRESH"?"#22c55e":"#f59e0b"}}>{freshness}</strong></p>
    {pipeline?.last_attempt?.status==="failed" && <p style={{color:"#f87171",fontSize:12}}>Latest scheduler attempt failed. Displayed data is from the last successful commit.</p>}
    {error && <p style={{color:"#f87171",fontSize:13}}>{error}. Start Phase 2 scheduler; no old snapshot is substituted.</p>}
    <p style={text}>Next 15m candle countdown does not guarantee scan completion. Ranking uses confirmed 1H trend and swing zones.</p>
    <button onClick={refresh} style={{background:"#202327",color:"#fff",border:"1px solid #363a40",borderRadius:6,padding:"9px 15px",cursor:"pointer",marginBottom:16}}>Refresh Committed Cycle</button>
    <section style={box}><h2 style={{fontSize:17,marginTop:0}}>Top 10 · Ranked Opportunities</h2><OpportunityRows rows={top} planIndex={plans}/></section>
    <section style={{...box,marginTop:16}}>
      <button onClick={()=>setExpanded(v=>!v)} style={{background:"transparent",border:0,color:"#fff",padding:0,cursor:"pointer",fontWeight:650,fontSize:15}}>{expanded?"▾ Hide":"▸ Show"} Remaining ({rest.length})</button>
      {expanded && <div style={{marginTop:16}}><OpportunityRows rows={rest} planIndex={plans}/></div>}
    </section>
    <p style={{...text,marginTop:15}}>Zone status and Trade Plan status are different. A valid trade still requires Sweep + Pin Bar/Engulfing + RR check. Only one atomic cycle is displayed.</p>
  </div>;
}
