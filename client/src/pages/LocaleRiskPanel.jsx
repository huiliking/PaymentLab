/**
 * LocaleRiskPanel.jsx
 * ===================
 * Visualizes geographic/locale signals from check_locale_consistency
 * and the identity graph's geographic_profile.
 *
 * Rendered inside ReportPanel in FraudDashboard.jsx.
 */

const RISK_COLORS = {
  LOW: { bg: "#e8f5e9", text: "#2e7d32", border: "#66bb6a" },
  MEDIUM: { bg: "#fff3e0", text: "#e65100", border: "#ffa726" },
  HIGH: { bg: "#ffebee", text: "#c62828", border: "#ef5350" },
  CRITICAL: { bg: "#4a0000", text: "#ff8a80", border: "#d50000" },
};

function RiskBadge({ level }) {
  const colors = RISK_COLORS[level] || RISK_COLORS.LOW;
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      fontFamily: "'JetBrains Mono', monospace",
      background: colors.bg,
      color: colors.text,
      border: `1px solid ${colors.border}`,
    }}>
      {level}
    </span>
  );
}

function CountryTag({ code, label, match }) {
  const bg = match ? "rgba(102,187,106,0.15)" : "rgba(239,83,80,0.15)";
  const color = match ? "#66bb6a" : "#ef5350";
  const border = match ? "#66bb6a" : "#ef5350";
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 4,
      padding: "3px 8px",
      borderRadius: 4,
      fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace",
      background: bg,
      color,
      border: `1px solid ${border}`,
      marginRight: 6,
      marginBottom: 4,
    }}>
      <span style={{ fontSize: 9, opacity: 0.7 }}>{label}</span>
      <strong>{code || "—"}</strong>
    </span>
  );
}

function CoherenceBar({ score }) {
  const pct = Math.round(score * 100);
  const color = score >= 0.7 ? "#66bb6a" : score >= 0.3 ? "#ffa726" : "#ef5350";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{
        flex: 1,
        height: 6,
        borderRadius: 3,
        background: "var(--border, #30363d)",
        overflow: "hidden",
      }}>
        <div style={{
          width: `${pct}%`,
          height: "100%",
          borderRadius: 3,
          background: color,
          transition: "width 0.3s ease",
        }} />
      </div>
      <span style={{
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
        color,
        fontWeight: 600,
        minWidth: 36,
      }}>
        {pct}%
      </span>
    </div>
  );
}

export default function LocaleRiskPanel({ localeData, geographicProfile }) {
  if (!localeData) return null;

  const billing = localeData.billing_country || "";
  const ip = localeData.ip_country || "";
  const card = localeData.card_country || "";
  const localeStr = localeData.browser_locale || "";
  const localeCountry = localeStr.includes("-") ? localeStr.split("-").pop().toUpperCase() : "";
  const mismatches = localeData.mismatches || [];
  const risk = localeData.risk || "LOW";
  const crossTxnCount = localeData.cross_txn_count || 0;
  const crossMerchantCount = localeData.cross_merchant_count || 0;
  const geo = geographicProfile || {};

  const cardStyle = {
    background: "var(--card-bg, #161b22)",
    border: "1px solid var(--border, #30363d)",
    borderRadius: 8,
    padding: 14,
    flex: "1 1 0",
    minWidth: 200,
  };
  const labelStyle = {
    fontSize: 10,
    fontFamily: "'Inter', sans-serif",
    color: "var(--text-muted, #484f58)",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: 6,
  };
  const valueStyle = {
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    color: "var(--text-primary, #e6edf3)",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Row 1: Country tags + risk */}
      <div style={cardStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={labelStyle}>Country Signals</div>
          <RiskBadge level={risk} />
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", marginBottom: 8 }}>
          <CountryTag code={billing} label="BILLING" match={true} />
          <CountryTag code={ip} label="IP" match={ip === billing} />
          <CountryTag code={card} label="CARD" match={card === billing} />
          <CountryTag code={localeCountry} label="LOCALE" match={localeCountry === billing} />
        </div>

        {mismatches.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {mismatches.map((m, i) => (
              <div key={i} style={{
                padding: "6px 10px",
                marginBottom: 4,
                borderLeft: "3px solid #ef5350",
                background: "rgba(239,83,80,0.08)",
                borderRadius: "0 4px 4px 0",
                fontSize: 12,
                fontFamily: "'Inter', sans-serif",
                color: "var(--text-secondary, #8b949e)",
              }}>
                {m}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Row 2: Cross-merchant + geographic coherence */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {/* Cross-merchant intelligence */}
        <div style={cardStyle}>
          <div style={labelStyle}>Cross-Merchant Intelligence</div>
          {crossMerchantCount >= 2 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: 4,
                background: RISK_COLORS.CRITICAL.bg,
                color: RISK_COLORS.CRITICAL.text,
                border: `1px solid ${RISK_COLORS.CRITICAL.border}`,
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', monospace",
                alignSelf: "flex-start",
              }}>
                Seen across {crossMerchantCount} merchants
              </div>
              <div style={valueStyle}>
                {crossTxnCount} matching transactions
              </div>
              {localeData.pattern_description && (
                <div style={{ fontSize: 11, color: "var(--text-secondary, #8b949e)", fontFamily: "'Inter', sans-serif" }}>
                  {localeData.pattern_description}
                </div>
              )}
            </div>
          ) : (
            <div style={{ ...valueStyle, color: "var(--text-muted, #484f58)" }}>
              {crossTxnCount > 0 ? `${crossTxnCount} matching txn(s), single merchant` : "No cross-merchant patterns"}
            </div>
          )}
        </div>

        {/* Geographic coherence */}
        {geo.coherence_score !== undefined && (
          <div style={cardStyle}>
            <div style={labelStyle}>Geographic Coherence (Identity Graph)</div>
            <CoherenceBar score={geo.coherence_score} />
            <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
              {(geo.card_issuing_countries || []).map(c => (
                <span key={`ci-${c}`} style={{
                  padding: "1px 6px",
                  borderRadius: 3,
                  fontSize: 10,
                  fontFamily: "'JetBrains Mono', monospace",
                  background: "rgba(88,166,255,0.12)",
                  color: "var(--accent, #58a6ff)",
                  border: "1px solid rgba(88,166,255,0.25)",
                }}>
                  {c}
                </span>
              ))}
            </div>
            {geo.merchant_count >= 2 && (
              <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-secondary, #8b949e)", fontFamily: "'Inter', sans-serif" }}>
                Cluster spans {geo.merchant_count} merchant(s): {(geo.merchants || []).join(", ")}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
