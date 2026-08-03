import React, { useState, useEffect } from 'react';
import { getAdminKey, setAdminKey, adminAuthHeaders } from '../utils/api';

/**
 * ToolDashboard.jsx
 * =================
 * Registry browser for the fraud investigation tool catalog. Uses the
 * shared design system from index.css (CSS custom properties, .card/.btn
 * classes) — the previous version used Tailwind utility classes, but this
 * project has no Tailwind build step, so none of that styling ever
 * actually rendered.
 *
 * Role gating: `isAdmin` reflects whether an admin key is stored locally
 * (client/src/utils/api.js). This is a UI convenience only — the server is
 * the actual enforcement point (server/routes/fraud.py, PAYMENTLAB_ADMIN_KEY).
 * A stale/wrong key here just means the approve/reject calls 401 and the
 * key gets cleared; it can't grant access the server wouldn't grant.
 */

const CATEGORY_ICONS = {
  transaction_context: '💳',
  identity_history: '👤',
  card_velocity: '⚡',
  geo_locale: '🌍',
  address_shipping: '📦',
  behavioral_account: '📊',
  merchant_product: '🏪',
  external_intel: '🔍',
};

const categoryIcon = (categoryId) => CATEGORY_ICONS[categoryId] || '📋';

const STATUS_STYLE = {
  active: { bg: 'var(--success-soft)', text: 'var(--success)' },
  candidate: { bg: 'var(--warning-soft)', text: 'var(--warning)' },
  proposed: { bg: 'var(--surface-elevated)', text: 'var(--ink-muted)' },
  rejected: { bg: 'var(--danger-soft)', text: 'var(--danger)' },
};

const STATUS_LABEL = { active: 'Active', candidate: 'Candidate', proposed: 'Proposed', rejected: 'Rejected' };

const STATUS_TOOLTIP = {
  active: 'Implemented and live — the investigation agent can call this tool today.',
  candidate: 'Human-approved as worth building, but no implementation exists yet. Not callable until someone writes the InvestigationTools method and flips it to Active.',
  proposed: 'Auto-suggested by the tool scanner from a threat report. Not yet reviewed by a human — click Approve to endorse it as worth building (moves it to Candidate), or Dismiss to reject it.',
  rejected: 'Dismissed by a human reviewer as not worth building. Kept here (not deleted) in case the decision needs to be revisited.',
};

function StatusBadge({ status }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.proposed;
  return (
    <span
      title={STATUS_TOOLTIP[status] || ''}
      style={{
        display: 'inline-flex', alignItems: 'center', padding: '0.2rem 0.6rem',
        borderRadius: 100, fontSize: '0.78rem', fontWeight: 500,
        background: s.bg, color: s.text, cursor: 'help',
        borderBottom: '1px dotted currentColor',
      }}
    >
      {STATUS_LABEL[status] || status}
    </span>
  );
}

function SourceBadge({ source }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', padding: '0.2rem 0.6rem',
      borderRadius: 100, fontSize: '0.78rem', fontWeight: 500,
      background: source === 'builtin' ? 'var(--accent-soft)' : 'var(--surface-elevated)',
      color: source === 'builtin' ? 'var(--accent)' : 'var(--ink-secondary)',
    }}>
      {source === 'builtin' ? 'Built-in' : 'External API'}
    </span>
  );
}

const ToolDashboard = () => {
  const [registry, setRegistry] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [selectedStatus, setSelectedStatus] = useState('all');
  const [expandedTool, setExpandedTool] = useState(null);
  const [approving, setApproving] = useState(null);
  const [rejecting, setRejecting] = useState(null);
  const [adminKey, setAdminKeyLocal] = useState(getAdminKey());

  const isAdmin = Boolean(adminKey);

  useEffect(() => {
    fetchRegistry();
  }, []);

  const handleAdminLogin = () => {
    const key = window.prompt('Enter admin key:');
    if (key === null) return; // cancelled
    if (!key.trim()) return;
    setAdminKey(key.trim());
    setAdminKeyLocal(key.trim());
  };

  const handleAdminLogout = () => {
    setAdminKey(null);
    setAdminKeyLocal('');
  };

  // A 401 means the stored key is missing/wrong from the server's point of
  // view — clear it so the UI drops back to the viewer role instead of
  // showing admin controls that will keep failing.
  const handleUnauthorized = () => {
    setAdminKey(null);
    setAdminKeyLocal('');
    alert('Admin key is missing or invalid. Please re-enter it.');
  };

  const fetchRegistry = async () => {
    try {
      const response = await fetch('/api/fraud/tools');
      if (!response.ok) throw new Error('Failed to fetch registry');
      const data = await response.json();
      setRegistry(data);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const handleApprove = async (toolName) => {
    if (approving !== null) return;
    setApproving(toolName);
    try {
      const res = await fetch(`/api/fraud/tools/${toolName}/approve`, {
        method: 'POST',
        headers: adminAuthHeaders(),
      });
      if (res.status === 401) {
        handleUnauthorized();
        return;
      }
      if (res.status === 409) {
        const conflict = await res.json();
        alert(conflict.error || 'Registry is busy, try again');
        return;
      }
      if (!res.ok) throw new Error('Approve failed');
      await fetchRegistry();
    } catch (err) {
      alert(`Error: ${err.message}`);
    } finally {
      setApproving(null);
    }
  };

  const handleReject = async (toolName) => {
    if (rejecting !== null) return;
    if (!window.confirm(`Dismiss "${toolName}"? It will be marked Rejected (not deleted).`)) return;
    setRejecting(toolName);
    try {
      const res = await fetch(`/api/fraud/tools/${toolName}/reject`, {
        method: 'POST',
        headers: adminAuthHeaders(),
      });
      if (res.status === 401) {
        handleUnauthorized();
        return;
      }
      if (res.status === 409) {
        const conflict = await res.json();
        alert(conflict.error || 'Registry is busy, try again');
        return;
      }
      if (!res.ok) throw new Error('Dismiss failed');
      await fetchRegistry();
    } catch (err) {
      alert(`Error: ${err.message}`);
    } finally {
      setRejecting(null);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', paddingTop: '4rem' }}>
        <div className="spinner" style={{ borderTopColor: 'var(--accent)', borderColor: 'var(--border)', margin: '0 auto' }} />
        <p style={{ marginTop: '1rem', color: 'var(--ink-secondary)' }}>Loading tool registry...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="card" style={{ maxWidth: 480, margin: '4rem auto', textAlign: 'center' }}>
          <h2 style={{ color: 'var(--danger)', marginBottom: '0.5rem' }}>Error Loading Registry</h2>
          <p style={{ color: 'var(--ink-secondary)' }}>{error}</p>
          <button className="btn btn-primary" style={{ marginTop: '1rem' }} onClick={fetchRegistry}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Viewers (no admin key) don't get to see proposed/rejected tools at all —
  // those are internal review states, not part of the "what does your
  // system check?" transparency surface merchants get.
  const filteredTools = registry.tools.filter((tool) => {
    if (!isAdmin && (tool.status === 'proposed' || tool.status === 'rejected')) return false;
    if (selectedCategory && tool.category !== selectedCategory) return false;
    if (selectedStatus !== 'all' && tool.status !== selectedStatus) return false;
    return true;
  });

  const activeCategory = registry.categories.find((c) => c.id === selectedCategory);

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1.5rem' }}>
        <div>
          <h1>{registry.name}</h1>
          <p>Version {registry.version}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
          <div style={{ display: 'flex', gap: '1.5rem' }}>
            <StatPill value={registry.statistics.active} label="Active" color="var(--success)" tooltip={STATUS_TOOLTIP.active} />
            <StatPill value={registry.statistics.candidate} label="Candidate" color="var(--warning)" tooltip={STATUS_TOOLTIP.candidate} />
            {isAdmin && (
              <StatPill value={registry.statistics.proposed || 0} label="Proposed" color="var(--ink-muted)" tooltip={STATUS_TOOLTIP.proposed} />
            )}
            <StatPill value={registry.statistics.total_tools} label="Total" color="var(--ink)" />
          </div>
          {isAdmin ? (
            <button className="btn-ghost" style={{ padding: '0.35rem 0.7rem', fontSize: '0.78rem', whiteSpace: 'nowrap' }} onClick={handleAdminLogout}>
              Admin ✓ (log out)
            </button>
          ) : (
            <button className="btn-ghost" style={{ padding: '0.35rem 0.7rem', fontSize: '0.78rem', whiteSpace: 'nowrap' }} onClick={handleAdminLogin}>
              Admin sign-in
            </button>
          )}
        </div>
      </div>

      <div style={{ marginBottom: '1.5rem' }}>
        <select
          value={selectedStatus}
          onChange={(e) => setSelectedStatus(e.target.value)}
          disabled={!selectedCategory}
          title={!selectedCategory ? 'Select a category to filter by status' : undefined}
          style={{ maxWidth: 260, opacity: selectedCategory ? 1 : 0.5 }}
        >
          <option value="all">All Status ({registry.statistics.total_tools})</option>
          <option value="active">Active ({registry.statistics.active})</option>
          <option value="candidate">Candidate ({registry.statistics.candidate})</option>
          {isAdmin && <option value="proposed">Proposed ({registry.statistics.proposed || 0})</option>}
          {isAdmin && <option value="rejected">Rejected ({registry.statistics.rejected || 0})</option>}
        </select>
      </div>

      {/* Body: sidebar + content */}
      <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start' }}>
        <CategorySidebar
          categories={registry.categories}
          statistics={registry.statistics}
          selectedCategory={selectedCategory}
          onSelect={setSelectedCategory}
          isAdmin={isAdmin}
        />

        <div style={{ flex: 1, minWidth: 0 }}>
          {!selectedCategory ? (
            <CategoryOverviewGrid
              categories={registry.categories}
              statistics={registry.statistics}
              onSelect={setSelectedCategory}
              isAdmin={isAdmin}
            />
          ) : (
            <CategoryTable
              category={activeCategory}
              tools={filteredTools}
              expandedTool={expandedTool}
              setExpandedTool={setExpandedTool}
              approving={approving}
              isAdmin={isAdmin}
              onApprove={handleApprove}
              rejecting={rejecting}
              onReject={handleReject}
            />
          )}

          {selectedCategory && filteredTools.length === 0 && (
            <div style={{ textAlign: 'center', padding: '3rem 0', color: 'var(--ink-muted)' }}>
              No tools match the current filters
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

function StatPill({ value, label, color, tooltip }) {
  return (
    <div style={{ textAlign: 'center', cursor: tooltip ? 'help' : 'default' }} title={tooltip}>
      <div style={{ fontSize: '1.75rem', fontWeight: 600, color, fontFamily: 'var(--font-mono)' }}>{value}</div>
      <div style={{
        fontSize: '0.7rem', color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.04em',
        borderBottom: tooltip ? '1px dotted var(--ink-muted)' : 'none', display: 'inline-block',
      }}>
        {label}
      </div>
    </div>
  );
}

function CategorySidebar({ categories, statistics, selectedCategory, onSelect, isAdmin }) {
  const rowStyle = (isSelected) => ({
    width: '100%', textAlign: 'left', padding: '0.85rem 1rem',
    borderBottom: '1px solid var(--border)', background: isSelected ? 'var(--accent-soft)' : 'transparent',
    borderLeft: isSelected ? '3px solid var(--accent)' : '3px solid transparent',
    cursor: 'pointer', font: 'inherit', color: 'inherit',
  });

  return (
    <div style={{ width: 260, flexShrink: 0, position: 'sticky', top: '2rem' }} className="card">
      <div style={{ padding: 0 }}>
        <button style={{ ...rowStyle(!selectedCategory), borderTopLeftRadius: 'var(--radius-lg)', borderTopRightRadius: 'var(--radius-lg)' }} onClick={() => onSelect(null)}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>📋 All Categories</span>
            <span style={{ color: 'var(--ink-muted)', fontSize: '0.85rem' }}>{statistics.total_tools}</span>
          </div>
        </button>
        {categories.map((cat, idx) => {
          const stats = statistics.by_category[cat.id] || {};
          const isSelected = selectedCategory === cat.id;
          const isLast = idx === categories.length - 1;
          return (
            <button
              key={cat.id}
              onClick={() => onSelect(cat.id)}
              style={{
                ...rowStyle(isSelected),
                borderBottom: isLast ? 'none' : '1px solid var(--border)',
                borderBottomLeftRadius: isLast ? 'var(--radius-lg)' : 0,
                borderBottomRightRadius: isLast ? 'var(--radius-lg)' : 0,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 500, fontSize: '0.9rem' }}>
                  {categoryIcon(cat.id)} {cat.name}
                </span>
                <span style={{ color: 'var(--ink-muted)', fontSize: '0.85rem' }}>{stats.total || 0}</span>
              </div>
              <div style={{ marginTop: 4, display: 'flex', gap: 8, fontSize: '0.72rem', color: 'var(--ink-muted)' }}>
                <span>{stats.active || 0} active</span>
                <span>·</span>
                <span>{stats.candidate || 0} candidate</span>
                {isAdmin && (
                  <>
                    <span>·</span>
                    <span>{stats.proposed || 0} proposed</span>
                  </>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CategoryOverviewGrid({ categories, statistics, onSelect }) {
  return (
    <div className="product-grid">
      {categories.map((cat) => {
        const stats = statistics.by_category[cat.id] || {};
        return (
          <button
            key={cat.id}
            onClick={() => onSelect(cat.id)}
            className="card"
            style={{ textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit', transition: 'box-shadow 0.15s, border-color 0.15s' }}
            onMouseEnter={(e) => { e.currentTarget.style.boxShadow = 'var(--shadow-md)'; e.currentTarget.style.borderColor = 'var(--accent)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.boxShadow = 'var(--shadow-sm)'; e.currentTarget.style.borderColor = 'var(--border)'; }}
          >
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
              <span style={{ fontSize: '1.5rem' }}>{categoryIcon(cat.id)}</span>
              <div>
                <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>{cat.name}</h3>
                <p style={{ fontSize: '0.78rem', color: 'var(--ink-muted)', fontStyle: 'italic' }}>"{cat.question}"</p>
              </div>
            </div>
            <p style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: 'var(--ink-secondary)' }}>{cat.description}</p>
            <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', fontSize: '0.78rem' }}>
              <span style={{ color: 'var(--success)', fontWeight: 500 }}>{stats.active || 0} active</span>
              <span style={{ color: 'var(--warning)', fontWeight: 500 }}>{stats.candidate || 0} candidate</span>
              <span style={{ color: 'var(--ink-muted)', fontWeight: 500 }}>{stats.total || 0} total</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function CategoryTable({ category, tools, expandedTool, setExpandedTool, approving, onApprove, rejecting, onReject, isAdmin }) {
  return (
    <div>
      <div style={{
        background: 'var(--accent-soft)', padding: '1rem 1.5rem',
        borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
        border: '1px solid var(--border)', borderBottom: 'none',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <h2 style={{ fontSize: '1.15rem', fontWeight: 600 }}>
            {categoryIcon(category.id)} {category.name}
          </h2>
          <span style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)', fontStyle: 'italic' }}>"{category.question}"</span>
        </div>
        <div style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)', fontWeight: 500 }}>
          {tools.length} {tools.length === 1 ? 'tool' : 'tools'}
        </div>
      </div>

      <div style={{ background: 'var(--surface-card)', border: '1px solid var(--border)', borderRadius: '0 0 var(--radius-lg) var(--radius-lg)', overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 640, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--surface-elevated)' }}>
              <th style={{ ...thStyle, width: 200 }}>Tool Name</th>
              <th style={thStyle}>Description</th>
              <th style={{ ...thStyle, width: 110 }}>Status</th>
              <th style={{ ...thStyle, width: 130, textAlign: 'center' }}>{isAdmin ? 'Action' : 'Details'}</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((tool) => (
              <React.Fragment key={tool.name}>
                <tr style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={tdStyle}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600 }}>{tool.name}</div>
                    <div style={{ marginTop: 4 }}><SourceBadge source={tool.source} /></div>
                  </td>
                  <td style={tdStyle}>
                    <div style={{ fontSize: '0.88rem' }}>{tool.description}</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--ink-muted)', marginTop: 4 }}>
                      <strong>Detects:</strong> {tool.detects}
                    </div>
                  </td>
                  <td style={tdStyle}><StatusBadge status={tool.status} /></td>
                  <td style={{ ...tdStyle, textAlign: 'center' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', alignItems: 'stretch' }}>
                      {tool.status === 'proposed' && isAdmin && (
                        <button
                          onClick={() => onApprove(tool.name)}
                          disabled={approving !== null}
                          className="btn"
                          style={{
                            padding: '0.35rem 0.85rem', fontSize: '0.78rem',
                            background: approving !== null ? 'var(--surface-elevated)' : 'var(--success)',
                            color: approving !== null ? 'var(--ink-muted)' : '#fff',
                            cursor: approving !== null ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {approving === tool.name ? 'Approving…' : 'Approve'}
                        </button>
                      )}
                      {tool.status === 'proposed' && isAdmin && (
                        <button
                          onClick={() => onReject(tool.name)}
                          disabled={rejecting !== null}
                          className="btn"
                          style={{
                            padding: '0.35rem 0.85rem', fontSize: '0.78rem',
                            background: rejecting !== null ? 'var(--surface-elevated)' : 'var(--danger)',
                            color: rejecting !== null ? 'var(--ink-muted)' : '#fff',
                            cursor: rejecting !== null ? 'not-allowed' : 'pointer',
                          }}
                        >
                          {rejecting === tool.name ? 'Dismissing…' : 'Dismiss'}
                        </button>
                      )}
                      <button
                        onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
                        className="btn-ghost"
                        style={{ padding: '0.35rem 0.7rem', fontSize: '0.78rem', whiteSpace: 'nowrap' }}
                      >
                        {expandedTool === tool.name ? 'Hide details' : 'See details'}
                      </button>
                    </div>
                  </td>
                </tr>
                {expandedTool === tool.name && (
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td colSpan="4" style={{ padding: '1rem 1.5rem', background: 'var(--surface-elevated)' }}>
                      <div className="card">
                        <ToolDetails tool={tool} />
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const thStyle = { padding: '0.75rem 1.25rem', textAlign: 'left', fontSize: '0.72rem', fontWeight: 600, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' };
const tdStyle = { padding: '0.9rem 1.25rem', verticalAlign: 'top' };

const ToolDetails = ({ tool }) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div>
        <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>Input Parameters:</h4>
        <table style={{ width: '100%', fontSize: '0.85rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ textAlign: 'left', fontFamily: 'var(--font-mono)', paddingBottom: 6, paddingRight: 16 }}>Parameter</th>
              <th style={{ textAlign: 'left', fontFamily: 'var(--font-mono)', paddingBottom: 6, paddingRight: 16 }}>Type</th>
              <th style={{ textAlign: 'left', fontFamily: 'var(--font-mono)', paddingBottom: 6 }}>Description</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(tool.input_schema.properties || {}).map(([key, schema]) => (
              <tr key={key} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 16px 6px 0', fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>{key}</td>
                <td style={{ padding: '6px 16px 6px 0', fontFamily: 'var(--font-mono)', color: 'var(--ink-secondary)' }}>{schema.type}</td>
                <td style={{ padding: '6px 0', color: 'var(--ink-secondary)' }}>{schema.description || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {tool.references && tool.references.length > 0 && (
        <div>
          <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>References:</h4>
          <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {tool.references.map((ref, idx) => (
              <li key={idx} style={{ fontSize: '0.85rem' }}>
                {ref.url ? (
                  <a href={ref.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)', fontWeight: 500, textDecoration: 'none' }}>
                    {ref.name} ↗
                  </a>
                ) : (
                  <span style={{ color: 'var(--ink-secondary)', fontWeight: 500 }}>{ref.name}</span>
                )}
                {ref.relevance && (
                  <div style={{ color: 'var(--ink-muted)', marginLeft: '1rem', marginTop: 4 }}>→ {ref.relevance}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

export default ToolDashboard;
