/**
 * IdentityGraphPanel.jsx
 * Week 4 v2: Graph-based fraud ring visualization
 *
 * Improvements over v1:
 *   1. Fraud nodes rendered in RED (cards with fraud_count > 0)
 *   2. Seed node large, labeled, gold-ringed, always visible
 *   3. Power-curve sizing (txn_count^0.6) for dramatic contrast
 *   4. Filter bar: by node type and fraud status
 *   5. Click-to-isolate: click a node to see only its neighbors
 *
 * Props:
 *   graphData   — the dict returned by check_identity_graph tool
 *   compact     — boolean, render smaller panel (default false)
 */

import { useEffect, useRef, useState, useMemo } from "react";
import * as d3 from "d3";

// ─── Design tokens ───────────────────────────────────────────────────────────
const COLORS = {
  bg:         "#0a0d14",
  surface:    "#111827",
  border:     "#1f2937",
  muted:      "#374151",
  text:       "#e5e7eb",
  textDim:    "#6b7280",

  card:       "#3b82f6",
  email:      "#8b5cf6",
  address:    "#f59e0b",
  ip:         "#10b981",
  device:     "#ec4899",

  fraud:      "#ef4444",   // red — fraud-flagged nodes
  fraudGlow:  "#dc262688",

  riskLow:    "#10b981",
  riskMed:    "#f59e0b",
  riskHigh:   "#ef4444",
  riskCrit:   "#dc2626",

  edge:       "rgba(99,102,241,0.18)",
  edgeHot:    "rgba(239,68,68,0.45)",
  edgeFocus:  "rgba(250,204,21,0.6)",

  seed:       "#facc15",
  seedGlow:   "#facc1566",
};

const NODE_TYPE_CONFIG = {
  card:     { color: COLORS.card,    label: "Card",    icon: "💳", baseR: 5  },
  email:    { color: COLORS.email,   label: "Email",   icon: "✉",  baseR: 4  },
  address:  { color: COLORS.address, label: "Address", icon: "📍", baseR: 6  },
  ip:       { color: COLORS.ip,      label: "IP",      icon: "🌐", baseR: 3.5 },
  device:   { color: COLORS.device,  label: "Device",  icon: "📱", baseR: 3.5 },
};

// Power-curve radius: base + txn_count^0.6 * scale
// 1 txn → ~base+3,   10 → ~base+12,   100 → ~base+48,   300 → ~base+95
function nodeRadius(d) {
  const base = (NODE_TYPE_CONFIG[d.node_type] || { baseR: 4 }).baseR;
  return base + Math.pow(Math.max(d.txn_count, 1), 0.6) * 3;
}

function nodeColor(d, isSeed) {
  // Always use type color for fill — fraud is shown via red ring, not fill override
  if (isSeed) return COLORS.seed;
  return (NODE_TYPE_CONFIG[d.node_type] || { color: "#6b7280" }).color;
}

function riskColor(score) {
  if (score >= 0.75) return COLORS.riskCrit;
  if (score >= 0.50) return COLORS.riskHigh;
  if (score >= 0.25) return COLORS.riskMed;
  return COLORS.riskLow;
}

// ─── Legend ──────────────────────────────────────────────────────────────────
function Legend() {
  const items = [
    ...Object.entries(NODE_TYPE_CONFIG).map(([key, cfg]) => ({
      key, color: cfg.color, label: cfg.label, ring: null
    })),
    { key: "fraud", color: COLORS.card, label: "Fraud (any type)", ring: COLORS.fraud },
    { key: "seed", color: null, label: "Seed txn", ring: COLORS.seed },
  ];
  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: "10px",
      padding: "10px 14px",
      borderTop: `1px solid ${COLORS.border}`,
      background: COLORS.surface,
    }}>
      {items.map(item => (
        <span key={item.key} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: COLORS.textDim }}>
          <span style={{
            width: 10, height: 10, borderRadius: "50%",
            background: item.ring ? (item.color || "transparent") : item.color,
            border: item.ring ? `2.5px solid ${item.ring}` : "none",
            display: "inline-block", flexShrink: 0,
            boxSizing: "border-box",
          }} />
          {item.label}
        </span>
      ))}
      <span style={{ fontSize: 10, color: COLORS.textDim, marginLeft: "auto" }}>
        Bubble size = transaction count (power scale)
      </span>
    </div>
  );
}

// ─── Filter bar ──────────────────────────────────────────────────────────────
function FilterBar({ filters, setFilters, nodeCounts }) {
  const types = ["all", "card", "email", "address", "ip", "device"];
  const fraudOpts = ["all", "fraud only", "clean only"];

  const pillStyle = (active) => ({
    padding: "3px 10px", fontSize: 10, fontWeight: 600,
    letterSpacing: "0.04em", textTransform: "uppercase",
    borderRadius: 4, cursor: "pointer",
    background: active ? COLORS.muted : "transparent",
    color: active ? COLORS.text : COLORS.textDim,
    border: `0.5px solid ${active ? COLORS.border : "transparent"}`,
    transition: "all 0.15s",
  });

  return (
    <div style={{
      display: "flex", gap: 4, padding: "8px 14px", alignItems: "center",
      borderBottom: `1px solid ${COLORS.border}`, flexWrap: "wrap",
      background: COLORS.surface,
    }}>
      <span style={{ fontSize: 10, color: COLORS.textDim, marginRight: 4 }}>Type:</span>
      {types.map(t => (
        <button key={t} style={pillStyle(filters.type === t)}
          onClick={() => setFilters(f => ({ ...f, type: f.type === t ? "all" : t }))}>
          {t}{t !== "all" ? ` (${nodeCounts[t] || 0})` : ""}
        </button>
      ))}
      <span style={{ width: 1, height: 16, background: COLORS.border, margin: "0 6px" }} />
      <span style={{ fontSize: 10, color: COLORS.textDim, marginRight: 4 }}>Status:</span>
      {fraudOpts.map(f => (
        <button key={f} style={pillStyle(filters.fraud === f)}
          onClick={() => setFilters(prev => ({ ...prev, fraud: prev.fraud === f ? "all" : f }))}>
          {f}
        </button>
      ))}
    </div>
  );
}

// ─── Cluster stats ───────────────────────────────────────────────────────────
function ClusterStats({ cluster, risk }) {
  const stats = [
    { label: "Cards in cluster",    value: cluster.size },
    { label: "Shared emails",       value: cluster.shared_emails },
    { label: "Shared addresses",    value: cluster.shared_addresses },
    { label: "Shared IPs",          value: cluster.shared_ips },
    { label: "Fraud neighbors",     value: cluster.known_fraud_neighbors },
  ];
  const labelColor = riskColor(risk.score);

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "repeat(5, 1fr)",
      gap: 1, background: COLORS.border,
      border: `1px solid ${COLORS.border}`, borderBottom: "none",
    }}>
      {stats.map(s => (
        <div key={s.label} style={{
          padding: "10px 12px", background: COLORS.surface,
          display: "flex", flexDirection: "column", gap: 2,
        }}>
          <span style={{
            fontSize: 20, fontWeight: 700,
            color: s.value > 0 ? labelColor : COLORS.textDim,
            fontFamily: "'JetBrains Mono', monospace",
          }}>{s.value}</span>
          <span style={{
            fontSize: 10, color: COLORS.textDim,
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}>{s.label}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Geographic Profile ──────────────────────────────────────────────────────
function GeographicProfile({ geo }) {
  if (!geo || Object.keys(geo).length === 0) return null;
  const coherence = geo.coherence_score ?? 1.0;
  const coherenceColor = coherence >= 0.7 ? COLORS.riskLow : coherence >= 0.4 ? COLORS.riskMed : COLORS.riskHigh;

  const countryGroups = [
    { label: "Card issuing", values: geo.card_issuing_countries || [] },
    { label: "Billing",      values: geo.billing_countries || [] },
    { label: "IP",           values: geo.ip_countries || [] },
  ].filter(g => g.values.length > 0);

  return (
    <div style={{
      padding: "10px 14px", background: COLORS.surface,
      borderTop: `1px solid ${COLORS.border}`, borderBottom: `1px solid ${COLORS.border}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color: COLORS.textDim,
          textTransform: "uppercase", letterSpacing: "0.05em",
        }}>Geographic Profile</span>
        {geo.merchant_count >= 2 && (
          <span style={{
            fontSize: 10, fontWeight: 700, color: COLORS.riskMed,
            background: COLORS.riskMed + "22", padding: "2px 8px", borderRadius: 3,
          }}>
            Spans {geo.merchant_count} merchants
          </span>
        )}
      </div>

      {countryGroups.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 10 }}>
          {countryGroups.map(g => (
            <div key={g.label} style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, color: COLORS.textDim }}>{g.label}:</span>
              {g.values.map(c => (
                <span key={c} style={{
                  fontSize: 10, fontWeight: 600, color: COLORS.text,
                  background: COLORS.muted, padding: "2px 6px", borderRadius: 3,
                }}>{c}</span>
              ))}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 10, color: COLORS.textDim, minWidth: 130 }}>
          Coherence ({geo.total_unique_countries ?? 1} countries)
        </span>
        <div style={{ flex: "0 1 160px", height: 6, background: COLORS.muted, borderRadius: 3 }}>
          <div style={{
            width: `${coherence * 100}%`, height: "100%",
            background: coherenceColor, borderRadius: 3,
          }} />
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, color: coherenceColor }}>
          {(coherence * 100).toFixed(0)}%
        </span>
      </div>
    </div>
  );
}

// ─── Signals ─────────────────────────────────────────────────────────────────
function Signals({ signals, riskLabel, riskScore }) {
  if (!signals || signals.length === 0) return null;
  const color = riskColor(riskScore);
  return (
    <div style={{
      padding: "10px 14px", background: COLORS.surface,
      borderTop: `1px solid ${COLORS.border}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{
          padding: "2px 8px", borderRadius: 3,
          background: color + "22", color, fontSize: 11,
          fontWeight: 700, letterSpacing: "0.08em",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {riskLabel} RISK · {(riskScore * 100).toFixed(0)}%
        </span>
        {(riskLabel === "CRITICAL" || riskLabel === "HIGH") && (
          <span style={{ fontSize: 11, color }}>⚠ Fraud ring detected</span>
        )}
      </div>
      <ul style={{ margin: 0, padding: "0 0 0 16px" }}>
        {signals.map((s, i) => (
          <li key={i} style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 3, lineHeight: 1.5 }}>{s}</li>
        ))}
      </ul>
    </div>
  );
}

// ─── Tooltip ─────────────────────────────────────────────────────────────────
function Tooltip({ node, position, isSeed }) {
  if (!node) return null;
  const cfg = NODE_TYPE_CONFIG[node.node_type] || {};
  const isFraud = node.fraud_count > 0;
  const borderColor = isFraud ? COLORS.fraud : isSeed ? COLORS.seed : cfg.color || COLORS.border;
  return (
    <div style={{
      position: "absolute", left: position.x + 14, top: position.y - 12,
      background: "#1f2937", border: `1.5px solid ${borderColor}`,
      borderRadius: 6, padding: "8px 12px",
      pointerEvents: "none", zIndex: 10, maxWidth: 300,
      boxShadow: "0 4px 24px rgba(0,0,0,0.6)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <span style={{ fontWeight: 700, fontSize: 12, color: borderColor }}>
          {cfg.icon} {cfg.label || node.node_type}
        </span>
        {isFraud && <span style={{
          fontSize: 9, fontWeight: 700, color: COLORS.fraud,
          background: COLORS.fraud + "22", padding: "1px 5px", borderRadius: 3,
        }}>FRAUD</span>}
        {isSeed && <span style={{
          fontSize: 9, fontWeight: 700, color: COLORS.seed,
          background: COLORS.seed + "22", padding: "1px 5px", borderRadius: 3,
        }}>SEED</span>}
      </div>
      <div style={{ fontSize: 11, color: COLORS.text, wordBreak: "break-all", marginBottom: 6 }}>
        {node.value}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "auto auto", gap: "2px 14px" }}>
        {[
          ["Transactions", node.txn_count],
          ["Known fraud", node.fraud_count],
          ["Risk score", (node.risk_score * 100).toFixed(0) + "%"],
        ].map(([k, v], i) => (
          <div key={i} style={{ display: "contents" }}>
            <span style={{ fontSize: 10, color: COLORS.textDim }}>{k}</span>
            <span style={{ fontSize: 10, color: k === "Known fraud" && node.fraud_count > 0 ? COLORS.fraud : COLORS.text, fontWeight: 600 }}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── D3 Graph Canvas (v2) ────────────────────────────────────────────────────
function GraphCanvas({ nodes, edges, seedEntityIds, width, height, focusNodeId, setFocusNodeId }) {
  const svgRef = useRef(null);
  const [tooltip, setTooltip] = useState({ node: null, position: { x: 0, y: 0 } });

  useEffect(() => {
    if (!nodes || nodes.length === 0 || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
    const links = edges
      .filter(e => nodeById[e.source] && nodeById[e.target])
      .map(e => ({ source: e.source, target: e.target, txn_count: e.shared_txn_ids?.length || 1 }));

    // Build neighbor index for click-to-isolate
    const neighborMap = {};
    links.forEach(l => {
      const sid = typeof l.source === "object" ? l.source.id : l.source;
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      if (!neighborMap[sid]) neighborMap[sid] = new Set();
      if (!neighborMap[tid]) neighborMap[tid] = new Set();
      neighborMap[sid].add(tid);
      neighborMap[tid].add(sid);
    });

    // Determine visibility based on focusNodeId
    const isVisible = (nodeId) => {
      if (!focusNodeId) return true;
      return nodeId === focusNodeId || (neighborMap[focusNodeId]?.has(nodeId));
    };

    const visibleNodes = nodes.filter(n => isVisible(n.id));
    const visibleNodeIds = new Set(visibleNodes.map(n => n.id));
    const visibleLinks = links.filter(l => {
      const sid = typeof l.source === "object" ? l.source.id : l.source;
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      return visibleNodeIds.has(sid) && visibleNodeIds.has(tid);
    });

    // Cap for performance
    const displayNodes = visibleNodes.slice(0, 200);
    const displayNodeIds = new Set(displayNodes.map(n => n.id));
    const displayLinks = visibleLinks.filter(l => {
      const sid = typeof l.source === "object" ? l.source.id : l.source;
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      return displayNodeIds.has(sid) && displayNodeIds.has(tid);
    });

    const chargeStrength = displayNodes.length > 100 ? -80 : -150;

    const simulation = d3.forceSimulation(displayNodes)
      .force("link", d3.forceLink(displayLinks).id(d => d.id).distance(40).strength(0.35))
      .force("charge", d3.forceManyBody().strength(chargeStrength))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide(d => nodeRadius(d) + 2));

    const g = svg.append("g");
    svg.call(d3.zoom().scaleExtent([0.2, 5]).on("zoom", e => g.attr("transform", e.transform)));

    // Defs: glow filters
    const defs = svg.append("defs");
    const seedGlow = defs.append("filter").attr("id", "seedGlow");
    seedGlow.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "blur");
    seedGlow.append("feFlood").attr("flood-color", COLORS.seed).attr("flood-opacity", "0.4").attr("result", "color");
    seedGlow.append("feComposite").attr("in", "color").attr("in2", "blur").attr("operator", "in").attr("result", "glow");
    const m1 = seedGlow.append("feMerge");
    m1.append("feMergeNode").attr("in", "glow");
    m1.append("feMergeNode").attr("in", "SourceGraphic");

    const fraudGlow = defs.append("filter").attr("id", "fraudGlow");
    fraudGlow.append("feGaussianBlur").attr("stdDeviation", "3").attr("result", "blur");
    fraudGlow.append("feFlood").attr("flood-color", COLORS.fraud).attr("flood-opacity", "0.35").attr("result", "color");
    fraudGlow.append("feComposite").attr("in", "color").attr("in2", "blur").attr("operator", "in").attr("result", "glow");
    const m2 = fraudGlow.append("feMerge");
    m2.append("feMergeNode").attr("in", "glow");
    m2.append("feMergeNode").attr("in", "SourceGraphic");

    // Links
    const link = g.append("g").selectAll("line")
      .data(displayLinks).enter().append("line")
      .attr("stroke", d => {
        const sid = typeof d.source === "object" ? d.source.id : d.source;
        const tid = typeof d.target === "object" ? d.target.id : d.target;
        if (focusNodeId && (sid === focusNodeId || tid === focusNodeId)) return COLORS.edgeFocus;
        return d.txn_count > 3 ? COLORS.edgeHot : COLORS.edge;
      })
      .attr("stroke-width", d => Math.min(d.txn_count * 0.4 + 0.3, 2.5));

    // Nodes
    const node = g.append("g").selectAll("g")
      .data(displayNodes).enter().append("g")
      .style("cursor", "pointer")
      .call(
        d3.drag()
          .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end",   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
      );

    // Circle for each node
    // Fill = always type color (blue card, orange address, etc.)
    // Stroke = red ring for fraud, gold ring for seed, type color otherwise
    node.append("circle")
      .attr("r", d => nodeRadius(d))
      .attr("fill", d => nodeColor(d, seedEntityIds.has(d.id)))
      .attr("fill-opacity", 0.8)
      .attr("stroke", d => {
        if (d.fraud_count > 0) return COLORS.fraud;      // red ring = fraud
        if (seedEntityIds.has(d.id)) return COLORS.seed;  // gold ring = seed
        return nodeColor(d, false) + "66";                // subtle type-color ring
      })
      .attr("stroke-width", d => {
        if (d.fraud_count > 0) return Math.max(3, nodeRadius(d) * 0.25);  // thick red ring, scales with size
        if (seedEntityIds.has(d.id)) return 3;
        return 0.5;
      })
      .attr("filter", d => {
        if (seedEntityIds.has(d.id)) return "url(#seedGlow)";
        if (d.fraud_count > 0) return "url(#fraudGlow)";
        return null;
      })
      // Dashed stroke for fraud — extra visual cue
      .attr("stroke-dasharray", d => d.fraud_count > 0 ? "4,2" : null);

    // Labels for seed nodes and very high-txn nodes
    node.filter(d => seedEntityIds.has(d.id) || d.txn_count > 50)
      .append("text")
      .attr("text-anchor", "middle")
      .attr("dy", d => nodeRadius(d) + 12)
      .attr("fill", d => seedEntityIds.has(d.id) ? COLORS.seed : COLORS.textDim)
      .attr("font-size", d => seedEntityIds.has(d.id) ? "10px" : "8px")
      .attr("font-weight", d => seedEntityIds.has(d.id) ? "700" : "400")
      .attr("font-family", "sans-serif")
      .text(d => {
        if (seedEntityIds.has(d.id)) {
          const cfg = NODE_TYPE_CONFIG[d.node_type] || {};
          return `${cfg.icon || "●"} ${d.value.length > 20 ? d.value.slice(0, 18) + "…" : d.value}`;
        }
        return d.value.length > 15 ? d.value.slice(0, 13) + "…" : d.value;
      });

    // Hover + click
    node
      .on("mousemove", (event, d) => {
        const rect = svgRef.current.getBoundingClientRect();
        setTooltip({
          node: d,
          position: { x: event.clientX - rect.left, y: event.clientY - rect.top }
        });
      })
      .on("mouseleave", () => setTooltip({ node: null, position: { x: 0, y: 0 } }))
      .on("click", (event, d) => {
        event.stopPropagation();
        setFocusNodeId(prev => prev === d.id ? null : d.id);
      });

    // Click background to clear focus
    svg.on("click", () => setFocusNodeId(null));

    simulation.on("tick", () => {
      link
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });

    return () => simulation.stop();
  }, [nodes, edges, seedEntityIds, width, height, focusNodeId, setFocusNodeId]);

  const tooltipSeed = tooltip.node ? seedEntityIds.has(tooltip.node.id) : false;

  return (
    <div style={{ position: "relative" }}>
      <svg ref={svgRef} width={width} height={height}
        style={{ background: COLORS.bg, display: "block" }} />
      <Tooltip node={tooltip.node} position={tooltip.position} isSeed={tooltipSeed} />
      {focusNodeId && (
        <div style={{
          position: "absolute", top: 8, right: 8,
          background: COLORS.seed + "22", color: COLORS.seed,
          fontSize: 10, fontWeight: 600, padding: "3px 10px",
          borderRadius: 4, cursor: "pointer",
        }} onClick={() => setFocusNodeId(null)}>
          ✕ Clear focus
        </div>
      )}
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────
export default function IdentityGraphPanel({ graphData, compact = false }) {
  const [tab, setTab] = useState("graph");
  const [filters, setFilters] = useState({ type: "all", fraud: "all" });
  const [focusNodeId, setFocusNodeId] = useState(null);
  const panelRef = useRef(null);
  const [dims, setDims] = useState({ width: 700, height: compact ? 300 : 480 });

  useEffect(() => {
    if (!panelRef.current) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      setDims({ width: w, height: compact ? 300 : Math.min(Math.max(w * 0.6, 340), 560) });
    });
    ro.observe(panelRef.current);
    return () => ro.disconnect();
  }, [compact]);

  if (!graphData) {
    return (
      <div style={{ padding: 20, color: COLORS.textDim, fontSize: 13, fontStyle: "italic" }}>
        No identity graph data available.
      </div>
    );
  }

  if (graphData.error) {
    return (
      <div style={{ padding: 20, color: "#f85149", fontSize: 13 }}>
        Identity graph tool call failed: {graphData.error}
      </div>
    );
  }

  const { graph, cluster, risk, seed_entities, transaction_id, geographic_profile } = graphData;
  const allNodes = graph?.nodes || [];
  const allEdges = graph?.edges || [];

  const seedEntityIds = useMemo(() => new Set(
    Object.entries(seed_entities || {}).map(([type, val]) => `${type}:${val}`)
  ), [seed_entities]);

  // Apply filters
  const filteredNodes = useMemo(() => {
    return allNodes.filter(n => {
      if (filters.type !== "all" && n.node_type !== filters.type) return false;
      if (filters.fraud === "fraud only" && n.fraud_count === 0) return false;
      if (filters.fraud === "clean only" && n.fraud_count > 0) return false;
      return true;
    });
  }, [allNodes, filters]);

  // Filter edges to match visible nodes
  const filteredEdges = useMemo(() => {
    const visibleIds = new Set(filteredNodes.map(n => n.id));
    return allEdges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target));
  }, [allEdges, filteredNodes]);

  // Node type counts for filter pills
  const nodeCounts = useMemo(() => {
    const counts = {};
    allNodes.forEach(n => { counts[n.node_type] = (counts[n.node_type] || 0) + 1; });
    return counts;
  }, [allNodes]);

  const tabStyle = (active) => ({
    padding: "6px 14px", fontSize: 11, fontWeight: 600,
    letterSpacing: "0.05em", textTransform: "uppercase",
    background: active ? COLORS.muted : "transparent",
    color: active ? COLORS.text : COLORS.textDim,
    border: "none", cursor: "pointer", borderRadius: 4,
  });

  return (
    <div ref={panelRef} style={{
      background: COLORS.bg, border: `1px solid ${COLORS.border}`,
      borderRadius: 8, overflow: "hidden",
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      width: "100%",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "10px 14px", background: COLORS.surface,
        borderBottom: `1px solid ${COLORS.border}`,
      }}>
        <div>
          <span style={{ fontSize: 12, fontWeight: 700, color: COLORS.text }}>Identity Graph</span>
          <span style={{ fontSize: 10, color: COLORS.textDim, marginLeft: 10 }}>{transaction_id}</span>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button style={tabStyle(tab === "graph")} onClick={() => setTab("graph")}>Graph</button>
          <button style={tabStyle(tab === "nodes")} onClick={() => setTab("nodes")}>
            Nodes ({filteredNodes.length}{filteredNodes.length !== allNodes.length ? `/${allNodes.length}` : ""})
          </button>
        </div>
      </div>

      {/* Cluster stats */}
      {cluster && risk && <ClusterStats cluster={cluster} risk={risk} />}

      {/* Geographic profile */}
      <GeographicProfile geo={geographic_profile} />

      {/* Filter bar */}
      <FilterBar filters={filters} setFilters={setFilters} nodeCounts={nodeCounts} />

      {/* Graph */}
      {tab === "graph" && (
        <GraphCanvas
          nodes={filteredNodes}
          edges={filteredEdges}
          seedEntityIds={seedEntityIds}
          width={dims.width}
          height={dims.height}
          focusNodeId={focusNodeId}
          setFocusNodeId={setFocusNodeId}
        />
      )}

      {/* Node table */}
      {tab === "nodes" && (
        <div style={{ height: dims.height, overflowY: "auto", background: COLORS.bg }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ background: COLORS.surface, position: "sticky", top: 0, zIndex: 2 }}>
                {["Type", "Value", "Txns", "Fraud", "Risk"].map(h => (
                  <th key={h} style={{
                    padding: "8px 10px", textAlign: "left", color: COLORS.textDim,
                    fontWeight: 600, borderBottom: `1px solid ${COLORS.border}`,
                    letterSpacing: "0.05em", textTransform: "uppercase",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...filteredNodes]
                .sort((a, b) => b.txn_count - a.txn_count)
                .map((n, i) => {
                  const cfg = NODE_TYPE_CONFIG[n.node_type] || {};
                  const isSeed = seedEntityIds.has(n.id);
                  const isFraud = n.fraud_count > 0;
                  return (
                    <tr key={n.id} style={{
                      background: isFraud
                        ? COLORS.fraud + "11"
                        : i % 2 === 0 ? COLORS.bg : COLORS.surface + "44",
                      borderLeft: isSeed ? `3px solid ${COLORS.seed}`
                        : isFraud ? `3px solid ${COLORS.fraud}`
                        : "3px solid transparent",
                    }}>
                      <td style={{ padding: "6px 10px" }}>
                        <span style={{
                          color: isFraud ? COLORS.fraud : cfg.color,
                          fontWeight: 600, fontSize: 10,
                          background: (isFraud ? COLORS.fraud : cfg.color || "#666") + "22",
                          padding: "2px 6px", borderRadius: 3,
                        }}>
                          {cfg.icon} {cfg.label || n.node_type}
                        </span>
                      </td>
                      <td style={{
                        padding: "6px 10px", color: COLORS.text, maxWidth: 200,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }} title={n.value}>
                        {n.value}
                        {isSeed && <span style={{
                          marginLeft: 6, fontSize: 9, color: COLORS.seed,
                          background: COLORS.seed + "22", padding: "1px 4px", borderRadius: 2,
                        }}>SEED</span>}
                      </td>
                      <td style={{ padding: "6px 10px", color: n.txn_count > 5 ? COLORS.riskHigh : COLORS.text }}>
                        {n.txn_count}
                      </td>
                      <td style={{
                        padding: "6px 10px", fontWeight: isFraud ? 700 : 400,
                        color: isFraud ? COLORS.fraud : COLORS.textDim,
                      }}>
                        {n.fraud_count}
                      </td>
                      <td style={{ padding: "6px 10px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <div style={{ width: 40, height: 3, background: COLORS.muted, borderRadius: 2 }}>
                            <div style={{
                              width: `${n.risk_score * 100}%`, height: "100%",
                              background: riskColor(n.risk_score), borderRadius: 2,
                            }} />
                          </div>
                          <span style={{ color: riskColor(n.risk_score), fontSize: 10 }}>
                            {(n.risk_score * 100).toFixed(0)}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}

      {/* Signals */}
      {risk && <Signals signals={risk.synthetic_identity_signals} riskLabel={risk.risk_label} riskScore={risk.score} />}

      {/* Legend */}
      <Legend />
    </div>
  );
}
