import { useState, useEffect } from "react";

/**
 * UsageDashboard.jsx
 * ==================
 * AI Usage & Cost Dashboard — Sprint 1 Pillar 1
 *
 * Consumes:
 *   GET /api/metering/summary?customer_id=demo_merchant&days=30
 *   GET /api/metering/breakdown?customer_id=demo_merchant&group_by=day
 *
 * Styled with index.css design system (CSS vars + .card/.badge/.btn classes).
 * No Tailwind.
 */

const API_BASE = "/api/metering";
const CUSTOMER_ID = "demo_merchant";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmt$(n) {
  if (n == null) return "$0.00";
  return "$" + Number(n).toFixed(4);
}

function fmtK(n) {
  if (n == null || n === 0) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function fmtPct(num, den) {
  if (!den) return "—";
  return ((num / den) * 100).toFixed(1) + "%";
}

// ── Stat Card ──────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, accentColor }) {
  return (
    <div className="card" style={{
      borderTop: `3px solid ${accentColor || "var(--accent)"}`,
      flex: "1 1 180px",
      minWidth: 160,
    }}>
      <div style={{
        fontSize: "1.75rem",
        fontWeight: 700,
        fontFamily: "var(--font-mono)",
        color: "var(--ink)",
        letterSpacing: "-0.02em",
      }}>
        {value}
      </div>
      <div style={{ fontSize: "0.82rem", color: "var(--ink-secondary)", marginTop: "0.25rem", fontWeight: 500 }}>
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", marginTop: "0.2rem" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ── Section Heading ────────────────────────────────────────────────────────

function SectionHead({ title, sub }) {
  return (
    <div style={{ marginBottom: "1rem" }}>
      <h2 style={{ fontSize: "1rem", fontWeight: 600, color: "var(--ink)" }}>{title}</h2>
      {sub && <p style={{ fontSize: "0.82rem", color: "var(--ink-muted)", marginTop: "0.15rem" }}>{sub}</p>}
    </div>
  );
}

// ── Daily Bar Chart (pure CSS) ─────────────────────────────────────────────

function DailyChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: "2rem", color: "var(--ink-muted)", fontSize: "0.88rem" }}>
        No daily data yet — run investigations first.
      </div>
    );
  }

  const maxCost = Math.max(...data.map(d => d.cost_usd || 0), 0.0001);

  return (
    <div>
      {/* Bars */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: "4px", height: "100px" }}>
        {data.map((d, i) => {
          const pct = ((d.cost_usd || 0) / maxCost) * 100;
          return (
            <div
              key={i}
              style={{ flex: 1, height: `${Math.max(pct, 2)}%`, background: "var(--accent)", borderRadius: "3px 3px 0 0", opacity: 0.75, transition: "height 0.3s ease", cursor: "default" }}
              title={`${d.period}: ${fmt$(d.cost_usd)} · ${fmtK(d.tokens)} tokens`}
            />
          );
        })}
      </div>
      {/* Labels — show first, middle, last only to avoid crowding */}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px" }}>
        {[data[0], data[Math.floor(data.length / 2)], data[data.length - 1]].filter(Boolean).map((d, i) => (
          <span key={i} style={{ fontSize: "0.7rem", color: "var(--ink-muted)", fontFamily: "var(--font-mono)" }}>
            {d.period ? d.period.slice(5) : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Provider Table ─────────────────────────────────────────────────────────

function ProviderTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p style={{ color: "var(--ink-muted)", fontSize: "0.88rem" }}>No provider data yet.</p>;
  }
  const totalCost = rows.reduce((s, r) => s + (r.cost_usd || 0), 0);

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
      <thead>
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          {["Provider", "Model", "Events", "Tokens", "Cost", "Share"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "0.4rem 0.6rem", fontWeight: 600, color: "var(--ink-secondary)", fontSize: "0.78rem" }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
            <td style={{ padding: "0.5rem 0.6rem" }}>
              <span className="badge" style={{ background: "var(--accent-soft)", color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                {r.provider}
              </span>
            </td>
            <td style={{ padding: "0.5rem 0.6rem", fontFamily: "var(--font-mono)", fontSize: "0.78rem", color: "var(--ink-secondary)" }}>
              {r.model || "—"}
            </td>
            <td style={{ padding: "0.5rem 0.6rem", color: "var(--ink-muted)" }}>{r.events?.toLocaleString()}</td>
            <td style={{ padding: "0.5rem 0.6rem", color: "var(--ink-muted)" }}>{fmtK(r.tokens)}</td>
            <td style={{ padding: "0.5rem 0.6rem", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{fmt$(r.cost_usd)}</td>
            <td style={{ padding: "0.5rem 0.6rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <div style={{ flex: 1, height: "6px", background: "var(--border)", borderRadius: "3px", maxWidth: "60px" }}>
                  <div style={{
                    width: totalCost ? `${((r.cost_usd / totalCost) * 100).toFixed(1)}%` : "0%",
                    height: "100%",
                    background: "var(--accent)",
                    borderRadius: "3px",
                  }} />
                </div>
                <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", fontFamily: "var(--font-mono)" }}>
                  {fmtPct(r.cost_usd, totalCost)}
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Cache Efficiency Panel ──────────────────────────────────────────────────

function CachePanel({ totals }) {
  const cacheRead = totals?.total_cache_read_tokens || 0;
  const cacheCreation = totals?.total_cache_creation_tokens || 0;
  const totalInput = totals?.total_input_tokens || 0;
  const savings = totals?.estimated_cache_savings_usd || 0;
  const totalCacheTokens = cacheRead + cacheCreation;
  const hitRate = fmtPct(cacheRead, totalCacheTokens || 1);

  if (!cacheRead && !cacheCreation) {
    return (
      <div style={{ padding: "1.5rem", textAlign: "center", color: "var(--ink-muted)", fontSize: "0.88rem" }}>
        No cache data yet — re-run investigations with <code style={{ fontFamily: "var(--font-mono)" }}>--backend claude</code> to populate.
      </div>
    );
  }

  const rows = [
    { label: "Cache read tokens", value: fmtK(cacheRead), badge: "badge-success", badgeText: "reads" },
    { label: "Cache write tokens", value: fmtK(cacheCreation), badge: null, badgeText: null },
    { label: "Uncached input tokens", value: fmtK(totalInput), badge: null, badgeText: null },
    { label: "Cache hit rate", value: hitRate, badge: "badge-success", badgeText: "of cached" },
    { label: "Estimated savings", value: fmt$(savings), badge: "badge-success", badgeText: "vs full price" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "0.75rem" }}>
      {rows.map(r => (
        <div key={r.label} style={{
          background: "var(--surface-elevated)",
          borderRadius: "var(--radius-sm)",
          padding: "0.75rem 1rem",
        }}>
          <div style={{ fontSize: "1.1rem", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
            {r.value}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", marginTop: "0.2rem" }}>{r.label}</div>
          {r.badge && (
            <span className={`badge ${r.badge}`} style={{ marginTop: "0.35rem", fontSize: "0.7rem" }}>
              {r.badgeText}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 10_000; // refresh every 10s

export default function UsageDashboard() {
  const [summary, setSummary] = useState(null);
  const [breakdown, setBreakdown] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchData = (showSpinner = false) => {
    if (showSpinner) setLoading(true);
    setError(null);

    Promise.all([
      fetch(`${API_BASE}/summary?customer_id=${CUSTOMER_ID}&days=${days}`).then(r => r.json()),
      fetch(`${API_BASE}/breakdown?customer_id=${CUSTOMER_ID}&group_by=day`).then(r => r.json()),
    ])
      .then(([sum, brk]) => {
        setSummary(sum);
        setBreakdown(brk.data || []);
        setLastUpdated(new Date());
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  };

  // Initial load + re-fetch when period changes
  useEffect(() => {
    fetchData(true);
  }, [days]);

  // Auto-refresh poll
  useEffect(() => {
    const timer = setInterval(() => fetchData(false), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [days]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", paddingTop: "4rem" }}>
        <div className="spinner" style={{ margin: "0 auto", borderColor: "rgba(0,0,0,0.1)", borderTopColor: "var(--accent)" }} />
        <p style={{ marginTop: "1rem", color: "var(--ink-muted)", fontSize: "0.9rem" }}>Loading usage data…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ borderTop: "3px solid var(--danger)", marginTop: "2rem", textAlign: "center" }}>
        <p style={{ color: "var(--danger)", fontWeight: 600 }}>Failed to load metering data</p>
        <p style={{ color: "var(--ink-muted)", fontSize: "0.85rem", marginTop: "0.5rem" }}>{error}</p>
        <p style={{ color: "var(--ink-muted)", fontSize: "0.82rem", marginTop: "0.5rem" }}>
          Is the Flask server running? Check <code style={{ fontFamily: "var(--font-mono)" }}>/api/metering/health</code>
        </p>
      </div>
    );
  }

  const totals = summary?.totals || {};
  const perSession = summary?.per_session || {};
  const byProvider = summary?.by_provider || [];

  return (
    <div className="animate-in">
      {/* Header */}
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
        <div>
          <h1>AI Usage Dashboard</h1>
          <p style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
            Cost and token metering for fraud investigation runs ·{" "}
            <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{CUSTOMER_ID}</code>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.75rem", color: "var(--success)", fontFamily: "var(--font-mono)" }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--success)", display: "inline-block", animation: "spin 2s linear infinite" }} />
              live · refreshes every 10s
              {lastUpdated && (
                <span style={{ color: "var(--ink-muted)" }}>
                  · updated {lastUpdated.toLocaleTimeString()}
                </span>
              )}
            </span>
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <label style={{ marginBottom: 0, fontSize: "0.82rem", color: "var(--ink-muted)" }}>Period:</label>
          {[7, 30, 90].map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={days === d ? "btn btn-primary" : "btn btn-ghost"}
              style={{ padding: "0.35rem 0.85rem", fontSize: "0.82rem" }}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Headline stat cards */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", marginBottom: "2rem" }}>
        <StatCard
          label="Total Cost"
          value={fmt$(totals.total_cost_usd)}
          sub={`${totals.total_events?.toLocaleString() || 0} API calls`}
          accentColor="var(--accent)"
        />
        <StatCard
          label="Sessions"
          value={totals.total_sessions?.toLocaleString() || "0"}
          sub={`avg ${fmt$(perSession.avg_cost_usd)} / session`}
          accentColor="var(--success)"
        />
        <StatCard
          label="Total Tokens"
          value={fmtK(totals.total_tokens)}
          sub={`${fmtK(totals.total_input_tokens)} in · ${fmtK(totals.total_output_tokens)} out`}
          accentColor="var(--warning)"
        />
        <StatCard
          label="Cache Savings"
          value={fmt$(totals.estimated_cache_savings_usd)}
          sub={`${fmtK(totals.total_cache_read_tokens || 0)} tokens read from cache`}
          accentColor="var(--success)"
        />
      </div>

      {/* Daily cost trend */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <SectionHead
          title="Daily Cost Trend"
          sub="Cost per day across all fraud investigation sessions"
        />
        <DailyChart data={breakdown} />
      </div>

      {/* Provider breakdown */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <SectionHead
          title="Cost by Provider / Model"
          sub="Token usage and cost broken down by AI backend"
        />
        <ProviderTable rows={byProvider} />
      </div>

      {/* Cache efficiency */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <SectionHead
          title="Cache Efficiency"
          sub="Prompt cache hits reduce input cost by ~90%. Cache writes cost 1.25× normal input."
        />
        <CachePanel totals={totals} />
      </div>

      {/* Per-session stats */}
      <div className="card" style={{ marginBottom: "2rem" }}>
        <SectionHead
          title="Per-Session Stats"
          sub="Cost distribution across individual investigation sessions"
        />
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap" }}>
          {[
            { label: "Avg cost / session", value: fmt$(perSession.avg_cost_usd) },
            { label: "Min cost / session", value: fmt$(perSession.min_cost_usd) },
            { label: "Max cost / session", value: fmt$(perSession.max_cost_usd) },
          ].map(s => (
            <div key={s.label} style={{ flex: "1 1 140px" }}>
              <div style={{ fontSize: "1.25rem", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
                {s.value}
              </div>
              <div style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginTop: "0.2rem" }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Disclaimer */}
      <p style={{ fontSize: "0.75rem", color: "var(--ink-muted)", textAlign: "center", borderTop: "1px solid var(--border)", paddingTop: "1rem" }}>
        Cost figures reflect prompt-cache-corrected pricing. Cache savings estimated at $3.00/M baseline input rate.
        Data covers the last {days} days for customer <code style={{ fontFamily: "var(--font-mono)" }}>{CUSTOMER_ID}</code>.
      </p>
    </div>
  );
}
