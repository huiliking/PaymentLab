import { useState, useEffect, useCallback } from "react";
import IdentityGraphPanel from "./IdentityGraphPanel";

/**
 * FraudDashboard.jsx
 * ==================
 * FAA-Style Fraud Investigation Dashboard
 * 
 * Shows flagged transactions, lets user trigger investigations,
 * and displays evidence-backed reports with investigation steps.
 * 
 * API endpoints (from fraud_routes.py):
 *   GET  /api/fraud/flagged          - List flagged transactions
 *   GET  /api/fraud/stats            - Dashboard statistics
 *   POST /api/fraud/investigate/:id  - Run investigation
 *   GET  /api/fraud/reports/:id      - Get investigation report
 */

const RISK_COLORS = {
  LOW: { bg: "#e8f5e9", text: "#2e7d32", border: "#66bb6a" },
  MEDIUM: { bg: "#fff3e0", text: "#e65100", border: "#ffa726" },
  HIGH: { bg: "#ffebee", text: "#c62828", border: "#ef5350" },
  CRITICAL: { bg: "#4a0000", text: "#ff8a80", border: "#d50000" },
};

const RISK_ICONS = { LOW: "✅", MEDIUM: "⚠️", HIGH: "🔴", CRITICAL: "🚨" };
const VERDICT_ICONS = { LEGITIMATE: "✅", SUSPICIOUS: "⚠️", FRAUDULENT: "🚨" };

// ── Stat Card ──────────────────────────────────────────────────────────────

function StatCard({ label, value, sublabel, accent }) {
  return (
    <div style={{
      background: "var(--card-bg)",
      border: "1px solid var(--border)",
      borderTop: `3px solid ${accent || "var(--accent)"}`,
      borderRadius: 8,
      padding: "20px 24px",
      flex: 1,
      minWidth: 160,
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>{label}</div>
      {sublabel && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{sublabel}</div>}
    </div>
  );
}

// ── Risk Badge ─────────────────────────────────────────────────────────────

function RiskBadge({ level }) {
  const colors = RISK_COLORS[level] || RISK_COLORS.MEDIUM;
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 10px",
      borderRadius: 12,
      fontSize: 11,
      fontWeight: 700,
      fontFamily: "'JetBrains Mono', monospace",
      background: colors.bg,
      color: colors.text,
      border: `1px solid ${colors.border}`,
      letterSpacing: "0.5px",
    }}>
      {RISK_ICONS[level] || "?"} {level}
    </span>
  );
}

// ── Transaction Row ────────────────────────────────────────────────────────

function TransactionRow({ item, onInvestigate, investigating, report }) {
  const txn = item.transaction;
  const amount = txn.currency === "jpy" 
    ? `¥${txn.amount_cents.toLocaleString()}`
    : `$${(txn.amount_cents / 100).toFixed(2)}`;
  
  const triggerNames = item.triggers.map(t => t.rule.replace(/_/g, " ")).join(", ");

  return (
    <div style={{
      background: "var(--card-bg)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: 16,
      marginBottom: 8,
      borderLeft: `4px solid ${RISK_COLORS[item.max_risk]?.border || "#999"}`,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <code style={{ fontSize: 13, color: "var(--accent)", fontFamily: "'JetBrains Mono', monospace" }}>
              {txn.id}
            </code>
            <RiskBadge level={item.max_risk} />
          </div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            ****{txn.card_last4} · {amount} {txn.currency.toUpperCase()} · {txn.customer_email}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            Triggers: {triggerNames}
          </div>
        </div>
        
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {report && (
            <span style={{
              fontSize: 12,
              padding: "4px 10px",
              borderRadius: 6,
              background: report.verdict === "LEGITIMATE" ? "#e8f5e9" : 
                          report.verdict === "SUSPICIOUS" ? "#fff3e0" : "#ffebee",
              color: report.verdict === "LEGITIMATE" ? "#2e7d32" :
                     report.verdict === "SUSPICIOUS" ? "#e65100" : "#c62828",
              fontWeight: 600,
            }}>
              {VERDICT_ICONS[report.verdict]} {report.verdict}
            </span>
          )}
          <button
            onClick={() => onInvestigate(txn.id)}
            disabled={investigating === txn.id}
            style={{
              padding: "8px 16px",
              borderRadius: 6,
              border: "none",
              background: investigating === txn.id ? "var(--border)" : "var(--accent)",
              color: investigating === txn.id ? "var(--text-muted)" : "#fff",
              fontSize: 12,
              fontWeight: 600,
              cursor: investigating === txn.id ? "not-allowed" : "pointer",
              fontFamily: "'JetBrains Mono', monospace",
              minWidth: 100,
            }}
          >
            {investigating === txn.id ? "⏳ Working..." : report ? "Re-investigate" : "🔍 Investigate"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Investigation Report Panel ─────────────────────────────────────────────

function ReportPanel({ report, onClose }) {
  if (!report) return null;

  return (
    <div style={{
      position: "fixed",
      top: 0, left: 0, right: 0, bottom: 0,
      background: "rgba(0,0,0,0.6)",
      display: "flex",
      justifyContent: "center",
      alignItems: "flex-start",
      paddingTop: 40,
      zIndex: 1000,
      overflowY: "auto",
    }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        width: "90%",
        maxWidth: 720,
        maxHeight: "85vh",
        overflowY: "auto",
        padding: 32,
        marginBottom: 40,
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 20, color: "var(--text-primary)", fontFamily: "'JetBrains Mono', monospace" }}>
              Investigation Report
            </h2>
            <code style={{ fontSize: 12, color: "var(--text-muted)" }}>{report.transaction_id}</code>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "1px solid var(--border)", borderRadius: 6,
            padding: "4px 12px", cursor: "pointer", color: "var(--text-secondary)", fontSize: 16,
          }}>✕</button>
        </div>

        {/* Verdict banner */}
        <div style={{
          background: RISK_COLORS[report.risk_level]?.bg || "#f5f5f5",
          border: `1px solid ${RISK_COLORS[report.risk_level]?.border || "#ddd"}`,
          borderRadius: 8,
          padding: "16px 20px",
          marginBottom: 24,
        }}>
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>Verdict</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: RISK_COLORS[report.risk_level]?.text }}>
                {VERDICT_ICONS[report.verdict]} {report.verdict}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>Risk Level</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: RISK_COLORS[report.risk_level]?.text }}>
                {report.risk_level}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>Confidence</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: RISK_COLORS[report.risk_level]?.text }}>
                {(report.confidence * 100).toFixed(0)}%
              </div>
            </div>
          </div>
          {report.summary && (
            <p style={{ margin: "12px 0 0", fontSize: 13, lineHeight: 1.5, color: RISK_COLORS[report.risk_level]?.text }}>
              {report.summary}
            </p>
          )}
        </div>

        {/* Evidence */}
        {report.evidence && report.evidence.length > 0 && (
          <div style={{ marginBottom: 24 }}>
            <h3 style={{ fontSize: 14, color: "var(--text-primary)", marginBottom: 12, fontFamily: "'JetBrains Mono', monospace" }}>
              Evidence ({report.evidence.length} items)
            </h3>
            {report.evidence.map((e, i) => (
              <div key={i} style={{
                padding: "10px 14px",
                borderRadius: 6,
                marginBottom: 6,
                background: RISK_COLORS[e.risk_signal]?.bg || "#f5f5f5",
                borderLeft: `3px solid ${RISK_COLORS[e.risk_signal]?.border || "#ddd"}`,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{e.source}</code>
                  <RiskBadge level={e.risk_signal} />
                </div>
                <div style={{ fontSize: 13, color: "var(--text-primary)", marginTop: 4 }}>{e.finding}</div>
              </div>
            ))}
          </div>
        )}

        {/* Investigation Steps */}
        {report.steps && report.steps.length > 0 && (
          <div>
            <h3 style={{ fontSize: 14, color: "var(--text-primary)", marginBottom: 12, fontFamily: "'JetBrains Mono', monospace" }}>
              Investigation Steps
            </h3>
            <div style={{
              borderLeft: "2px solid var(--border)",
              paddingLeft: 16,
              marginLeft: 8,
            }}>
              {report.steps.map((s, i) => (
                <div key={i} style={{ marginBottom: 12, position: "relative" }}>
                  <div style={{
                    position: "absolute", left: -22, top: 2,
                    width: 12, height: 12, borderRadius: "50%",
                    background: s.phase === "VERDICT" ? "var(--accent)" : 
                                s.phase === "GATHER" ? "#66bb6a" :
                                s.phase === "PLAN" ? "#42a5f5" : "#bdbdbd",
                    border: "2px solid var(--bg)",
                  }} />
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span style={{
                      fontSize: 10, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace",
                      padding: "1px 6px", borderRadius: 4,
                      background: s.phase === "VERDICT" ? "#e8eaf6" :
                                  s.phase === "GATHER" ? "#e8f5e9" :
                                  s.phase === "PLAN" ? "#e3f2fd" : "#f5f5f5",
                      color: s.phase === "VERDICT" ? "#283593" :
                             s.phase === "GATHER" ? "#2e7d32" :
                             s.phase === "PLAN" ? "#1565c0" : "#757575",
                    }}>
                      {s.phase}
                    </span>
                    {s.tool_used && (
                      <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{s.tool_used}</code>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
                    {s.action?.substring(0, 120)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Identity Graph — only rendered when check_identity_graph was called */}
        {report.tool_results?.check_identity_graph && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{
              fontSize: 14, fontWeight: 600, margin: "0 0 10px",
              fontFamily: "'JetBrains Mono', monospace", color: "var(--text-primary)",
            }}>
              Identity Graph
            </h3>
            <IdentityGraphPanel
              graphData={report.tool_results.check_identity_graph}
              compact={false}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Dashboard ─────────────────────────────────────────────────────────

export default function FraudDashboard() {
  const [flagged, setFlagged] = useState([]);
  const [stats, setStats] = useState(null);
  const [reports, setReports] = useState({});
  const [investigating, setInvestigating] = useState(null);
  const [selectedReport, setSelectedReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch data
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [flaggedRes, statsRes, reportsRes] = await Promise.all([
        fetch("/api/fraud/flagged"),
        fetch("/api/fraud/stats"),
        fetch("/api/fraud/reports"),
      ]);

      const flaggedData = await flaggedRes.json();
      const statsData = await statsRes.json();
      const reportsData = await reportsRes.json();

      setFlagged(flaggedData.flagged || []);
      setStats(statsData);

      // Index reports by transaction_id
      const reportMap = {};
      for (const r of reportsData.reports || []) {
        reportMap[r.transaction_id] = r;
      }
      setReports(reportMap);
      setError(null);
    } catch (e) {
      setError("Could not connect to server. Make sure Flask is running on :5000");
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Run investigation
  const handleInvestigate = async (txnId) => {
    setInvestigating(txnId);
    try {
      const res = await fetch(`/api/fraud/investigate/${txnId}`, { method: "POST" });
      const report = await res.json();
      setReports(prev => ({ ...prev, [txnId]: report }));
      setSelectedReport(report);
    } catch (e) {
      console.error("Investigation failed:", e);
      alert("Investigation failed — is Ollama running?");
    } finally {
      setInvestigating(null);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <>
      <style>{`
        :root {
          --bg: #0d1117;
          --card-bg: #161b22;
          --border: #30363d;
          --text-primary: #e6edf3;
          --text-secondary: #8b949e;
          --text-muted: #484f58;
          --accent: #58a6ff;
        }
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');
      `}</style>

      <div style={{
        minHeight: "100vh",
        background: "var(--bg)",
        color: "var(--text-primary)",
        fontFamily: "'Inter', sans-serif",
        padding: "24px 32px",
      }}>
        {/* Header */}
        <div style={{ marginBottom: 32 }}>
          <h1 style={{
            fontSize: 24, fontWeight: 700, margin: 0,
            fontFamily: "'JetBrains Mono', monospace",
            display: "flex", alignItems: "center", gap: 10,
          }}>
            🔍 Fraud Investigation Dashboard
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "4px 0 0" }}>
            FAA-Style Iterative Investigation Agent · Plan → Gather → Analyze → Report
          </p>
        </div>

        {error && (
          <div style={{
            background: "#3d1c1c", border: "1px solid #6b3030", borderRadius: 8,
            padding: "12px 16px", marginBottom: 20, fontSize: 13, color: "#ff8a80",
          }}>
            {error}
          </div>
        )}

        {/* Stats */}
        {stats && (
          <div style={{ display: "flex", gap: 12, marginBottom: 28, flexWrap: "wrap" }}>
            <StatCard label="Transactions" value={stats.total_transactions} accent="#58a6ff" />
            <StatCard label="Flagged" value={flagged.length} accent="#ffa726" />
            <StatCard label="Investigated" value={stats.total_investigations} accent="#66bb6a" />
            <StatCard 
              label="Fraudulent" 
              value={stats.verdict_breakdown?.FRAUDULENT || 0}
              accent="#ef5350"
            />
            <StatCard 
              label="Total Volume" 
              value={`$${(stats.total_amount_usd || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              accent="#ab47bc"
            />
          </div>
        )}

        {/* Flagged Transactions */}
        <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, fontFamily: "'JetBrains Mono', monospace" }}>
            Flagged Transactions ({flagged.length})
          </h2>
          <button onClick={fetchData} style={{
            padding: "6px 14px", borderRadius: 6, border: "1px solid var(--border)",
            background: "transparent", color: "var(--text-secondary)", fontSize: 12,
            cursor: "pointer", fontFamily: "'JetBrains Mono', monospace",
          }}>
            ↻ Refresh
          </button>
        </div>

        {loading ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>
            Loading transactions...
          </div>
        ) : flagged.length === 0 ? (
          <div style={{
            textAlign: "center", padding: 40, color: "var(--text-muted)",
            background: "var(--card-bg)", borderRadius: 8, border: "1px solid var(--border)",
          }}>
            No flagged transactions. Run seed_transactions.py to generate test data.
          </div>
        ) : (
          flagged.map((item, i) => (
            <TransactionRow
              key={item.transaction.id}
              item={item}
              onInvestigate={handleInvestigate}
              investigating={investigating}
              report={reports[item.transaction.id]}
            />
          ))
        )}

        {/* Previously investigated */}
        {Object.keys(reports).length > 0 && (
          <div style={{ marginTop: 32 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 12px", fontFamily: "'JetBrains Mono', monospace" }}>
              Investigation Reports ({Object.keys(reports).length})
            </h2>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {Object.entries(reports).map(([txnId, report]) => (
                <button
                  key={txnId}
                  onClick={() => setSelectedReport(report)}
                  style={{
                    padding: "8px 14px", borderRadius: 6,
                    border: `1px solid ${RISK_COLORS[report.risk_level]?.border || "var(--border)"}`,
                    background: RISK_COLORS[report.risk_level]?.bg || "var(--card-bg)",
                    color: RISK_COLORS[report.risk_level]?.text || "var(--text-primary)",
                    fontSize: 12, cursor: "pointer",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  {VERDICT_ICONS[report.verdict]} {txnId.substring(0, 8)}… — {report.verdict}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Report Modal */}
        <ReportPanel report={selectedReport} onClose={() => setSelectedReport(null)} />
      </div>
    </>
  );
}
