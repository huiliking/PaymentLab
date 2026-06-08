import React, { useState, useEffect } from 'react';

const ToolDashboard = () => {
  const [registry, setRegistry] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [selectedStatus, setSelectedStatus] = useState('all');
  const [expandedTool, setExpandedTool] = useState(null);

  useEffect(() => {
    fetchRegistry();
  }, []);

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

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading tool registry...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <h2 className="text-red-800 font-semibold mb-2">Error Loading Registry</h2>
          <p className="text-red-600">{error}</p>
          <button 
            onClick={fetchRegistry}
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const filteredTools = registry.tools.filter(tool => {
    if (selectedCategory && tool.category !== selectedCategory) return false;
    if (selectedStatus !== 'all' && tool.status !== selectedStatus) return false;
    return true;
  });

  const categoryIcon = (categoryId) => {
    const icons = {
      'transaction_context': '💳',
      'identity_history': '👤',
      'card_velocity': '⚡',
      'geo_locale': '🌍',
      'address_shipping': '📦',
      'behavioral_account': '📊',
      'merchant_product': '🏪',
      'external_intelligence': '🔍'
    };
    return icons[categoryId] || '📋';
  };

  const statusBadge = (status) => {
    const styles = {
      active: 'bg-green-100 text-green-800 border-green-300',
      candidate: 'bg-yellow-100 text-yellow-800 border-yellow-300',
      proposed: 'bg-gray-100 text-gray-800 border-gray-300'
    };
    const labels = {
      active: 'Active',
      candidate: 'Candidate',
      proposed: 'Proposed'
    };
    return (
      <span className={`px-2 py-1 text-xs font-medium rounded border ${styles[status] || styles.proposed}`}>
        {labels[status] || status}
      </span>
    );
  };

  const sourceBadge = (source) => {
    const styles = {
      builtin: 'bg-blue-100 text-blue-800',
      external: 'bg-purple-100 text-purple-800'
    };
    return (
      <span className={`px-2 py-1 text-xs font-medium rounded ${styles[source] || styles.builtin}`}>
        {source === 'builtin' ? 'Built-in' : 'External API'}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">{registry.name}</h1>
              <p className="mt-1 text-sm text-gray-500">Version {registry.version}</p>
            </div>
            <div className="flex gap-6">
              <div className="text-center">
                <div className="text-3xl font-bold text-green-600">{registry.statistics.active}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">Active</div>
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-yellow-600">{registry.statistics.candidate}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">Candidate</div>
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-gray-600">{registry.statistics.total_tools}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">Total</div>
              </div>
            </div>
          </div>

          {/* Filters */}
          <div className="mt-6 flex gap-4">
            <select
              value={selectedCategory || ''}
              onChange={(e) => setSelectedCategory(e.target.value || null)}
              className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option value="">All Categories ({registry.statistics.total_tools})</option>
              {registry.categories.map(cat => {
                const categoryCount = registry.statistics.by_category[cat.id]?.total || 
                                    registry.statistics.by_category[cat.id] || 0;
                return (
                  <option key={cat.id} value={cat.id}>
                    {categoryIcon(cat.id)} {cat.name} ({categoryCount})
                  </option>
                );
              })}
            </select>

            <select
              value={selectedStatus}
              onChange={(e) => setSelectedStatus(e.target.value)}
              className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option value="all">All Status ({registry.statistics.total_tools})</option>
              <option value="active">Active ({registry.statistics.active})</option>
              <option value="candidate">Candidate ({registry.statistics.candidate})</option>
            </select>
          </div>
        </div>
      </div>

      {/* Main Content - Table */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {selectedCategory ? (
          // Single category view
          <CategoryTable 
            category={registry.categories.find(c => c.id === selectedCategory)}
            tools={filteredTools}
            statusBadge={statusBadge}
            sourceBadge={sourceBadge}
            categoryIcon={categoryIcon}
            expandedTool={expandedTool}
            setExpandedTool={setExpandedTool}
          />
        ) : (
          // All categories view
          registry.categories.map(category => {
            const categoryTools = filteredTools.filter(t => t.category === category.id);
            if (categoryTools.length === 0) return null;
            
            return (
              <CategoryTable 
                key={category.id}
                category={category}
                tools={categoryTools}
                statusBadge={statusBadge}
                sourceBadge={sourceBadge}
                categoryIcon={categoryIcon}
                expandedTool={expandedTool}
                setExpandedTool={setExpandedTool}
              />
            );
          })
        )}

        {filteredTools.length === 0 && (
          <div className="text-center py-12">
            <p className="text-gray-500">No tools match the current filters</p>
          </div>
        )}
      </div>
    </div>
  );
};

const CategoryTable = ({ category, tools, statusBadge, sourceBadge, categoryIcon, expandedTool, setExpandedTool }) => {
  return (
    <div className="mb-8">
      {/* Category Header */}
      <div className="bg-gradient-to-r from-blue-50 to-blue-100 px-6 py-4 rounded-t-lg border border-blue-200">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-bold text-gray-900">
              {categoryIcon(category.id)} {category.name}
            </h2>
            <span className="text-sm text-gray-600 italic">"{category.question}"</span>
          </div>
          <div className="text-sm font-medium text-gray-600">
            {tools.length} {tools.length === 1 ? 'tool' : 'tools'}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white shadow-sm rounded-b-lg border border-t-0 border-gray-200 overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-64">
                Tool Name
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Description
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-32">
                Status
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-32">
                Source
              </th>
              <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider w-20">
                Details
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {tools.map(tool => (
              <React.Fragment key={tool.name}>
                <tr className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="font-mono text-sm font-semibold text-gray-900">{tool.name}</div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="text-sm text-gray-900">{tool.description}</div>
                    <div className="text-xs text-gray-500 mt-1">
                      <span className="font-semibold">Detects:</span> {tool.detects}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {statusBadge(tool.status)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {sourceBadge(tool.source)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-center">
                    <button
                      onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
                      className="text-blue-600 hover:text-blue-800 font-medium text-sm"
                    >
                      {expandedTool === tool.name ? '▼' : '▶'}
                    </button>
                  </td>
                </tr>
                {expandedTool === tool.name && (
                  <tr>
                    <td colSpan="5" className="px-6 py-4 bg-gray-50">
                      <div className="border-2 border-blue-200 rounded-lg bg-white p-4">
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
};

const ToolDetails = ({ tool }) => {
  return (
    <div className="space-y-4">
      {/* Input Schema */}
      <div>
        <h4 className="text-sm font-semibold text-gray-700 mb-2">Input Parameters:</h4>
        <div className="bg-white rounded border border-gray-200 p-3">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left font-mono font-semibold text-gray-700 pb-2 pr-4">Parameter</th>
                <th className="text-left font-mono font-semibold text-gray-700 pb-2 pr-4">Type</th>
                <th className="text-left font-mono font-semibold text-gray-700 pb-2">Description</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tool.input_schema.properties || {}).map(([key, schema]) => (
                <tr key={key} className="border-b border-gray-100 last:border-0">
                  <td className="py-2 pr-4 font-mono text-blue-600">{key}</td>
                  <td className="py-2 pr-4 font-mono text-gray-600">{schema.type}</td>
                  <td className="py-2 text-gray-600">{schema.description || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* References */}
      {tool.references && tool.references.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-gray-700 mb-2">References:</h4>
          <ul className="space-y-2">
            {tool.references.map((ref, idx) => (
              <li key={idx} className="text-sm">
                {ref.url ? (
                  <a 
                    href={ref.url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline font-medium"
                  >
                    {ref.name} ↗
                  </a>
                ) : (
                  <span className="text-gray-700 font-medium">{ref.name}</span>
                )}
                {ref.relevance && (
                  <div className="text-gray-500 ml-4 mt-1">→ {ref.relevance}</div>
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
