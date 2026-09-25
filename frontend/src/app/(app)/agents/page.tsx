'use client';

import { useState, useEffect } from 'react';
import DynamicForm from './components/DynamicForm';
import { API_BASE } from '@/lib/api';

interface NodeType {
  id: string;
  type_key: string;
  display_name: string;
  description: string;
  executor_key: string;
  config_schema: any;
  default_config: any;
  icon: string;
}

interface Agent {
  id: string;
  name: string;
  description: string;
  node_type_key: string;
  params: Record<string, any>;
  created_at: string;
  updated_at?: string;
}

export default function AgentsPage() {
  const [nodeTypes, setNodeTypes] = useState<NodeType[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create agent form state
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [selectedTypeKey, setSelectedTypeKey] = useState<string>('');
  const [agentName, setAgentName] = useState('');
  const [agentDescription, setAgentDescription] = useState('');
  const [agentParams, setAgentParams] = useState<Record<string, any>>({});
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Edit state
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      const [typesRes, agentsRes] = await Promise.all([
        fetch(`${API_BASE}/node-types/`),
        fetch(`${API_BASE}/agents/`),
      ]);
      if (!typesRes.ok) throw new Error('Failed to fetch node types');
      if (!agentsRes.ok) throw new Error('Failed to fetch agents');
      setNodeTypes(await typesRes.json());
      setAgents(await agentsRes.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function selectNodeType(typeKey: string) {
    setSelectedTypeKey(typeKey);
    setCreateError(null);
    const nt = nodeTypes.find((t) => t.type_key === typeKey);
    if (nt?.default_config) {
      setAgentParams({ ...nt.default_config });
    } else {
      setAgentParams({});
    }
  }

  async function handleCreateAgent(e: React.FormEvent) {
    e.preventDefault();
    if (!agentName.trim() || !selectedTypeKey) return;
    setCreating(true);
    setCreateError(null);

    try {
      const res = await fetch(`${API_BASE}/agents/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: agentName.trim(),
          description: agentDescription.trim() || null,
          node_type_key: selectedTypeKey,
          params: agentParams,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to create agent');
      }
      // Reset form & refresh
      setAgentName('');
      setAgentDescription('');
      setAgentParams({});
      setSelectedTypeKey('');
      setShowCreateForm(false);
      await fetchData();
    } catch (e: any) {
      setCreateError(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteAgent(agentId: string) {
    if (!confirm('Are you sure you want to delete this agent?')) return;
    try {
      const res = await fetch(`${API_BASE}/agents/${agentId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Failed to delete agent');
      await fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  }

  const selectedNodeType = nodeTypes.find((t) => t.type_key === selectedTypeKey);

  // Group agents by node_type_key
  const agentsByType: Record<string, Agent[]> = {};
  for (const agent of agents) {
    if (!agentsByType[agent.node_type_key]) agentsByType[agent.node_type_key] = [];
    agentsByType[agent.node_type_key].push(agent);
  }

  if (loading) {
    return (
      <main className="container">
        <div className="page-loading">
          <div className="spinner" />
          <p>Loading agent registry...</p>
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <div className="page-header">
        <div>
          <h1 className="heading page-title">Agent Registry</h1>
          <p className="text-muted">
            Create and manage agents from existing node types. Agents are reusable
            building blocks for workflows.
          </p>
        </div>
        <button
          className="btn btn-glow"
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          {showCreateForm ? 'Cancel' : 'Create Agent'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* ───── Create Agent Form ───── */}
      {showCreateForm && (
        <div className="card glass create-agent-card">
          <h2 className="heading">Create New Agent</h2>
          <form onSubmit={handleCreateAgent}>
            {/* Step 1: Pick Node Type */}
            <div className="form-section">
              <h3 className="form-section-title">1. Select node type</h3>
              <div className="node-type-grid">
                {nodeTypes.map((nt) => (
                  <button
                    key={nt.type_key}
                    type="button"
                    className={`node-type-card ${selectedTypeKey === nt.type_key ? 'node-type-card-selected' : ''}`}
                    onClick={() => selectNodeType(nt.type_key)}
                  >
                    <span className="tag tag--accent">{nt.executor_key}</span>
                    <span className="node-type-name">{nt.display_name}</span>
                    <span className="node-type-desc">{nt.description}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Step 2: Configure Agent */}
            {selectedNodeType && (
              <div className="form-section">
                <h3 className="form-section-title">2. Configure agent</h3>
                <div className="form-field">
                  <label className="form-label">
                    Agent Name <span className="form-required">*</span>
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    value={agentName}
                    onChange={(e) => setAgentName(e.target.value)}
                    placeholder="e.g., Design Reviewer, Code Writer..."
                    required
                  />
                </div>
                <div className="form-field">
                  <label className="form-label">Description</label>
                  <input
                    type="text"
                    className="form-input"
                    value={agentDescription}
                    onChange={(e) => setAgentDescription(e.target.value)}
                    placeholder="Brief description of what this agent does"
                  />
                </div>

                <div className="form-divider" />
                <p className="form-section-subtitle">
                  {selectedNodeType.display_name} Configuration
                </p>
                <DynamicForm
                  schema={selectedNodeType.config_schema}
                  values={agentParams}
                  onChange={setAgentParams}
                />
              </div>
            )}

            {/* Step 3: Submit */}
            {selectedNodeType && (
              <div className="form-actions">
                {createError && (
                  <div className="alert alert-error">{createError}</div>
                )}
                <button
                  type="submit"
                  className="btn btn-glow"
                  disabled={creating || !agentName.trim()}
                >
                  {creating ? 'Creating...' : 'Create Agent'}
                </button>
              </div>
            )}
          </form>
        </div>
      )}

      {/* ───── Registered Agents ───── */}
      {agents.length === 0 ? (
        <div className="empty-state">
          <h3>No agents yet</h3>
          <p className="text-muted">
            Create your first agent to start building workflows.
          </p>
        </div>
      ) : (
        Object.entries(agentsByType).map(([typeKey, typeAgents]) => {
          const nt = nodeTypes.find((t) => t.type_key === typeKey);
          return (
            <div key={typeKey} className="agent-type-group">
              <h2 className="agent-type-group-title">
                {nt?.display_name || typeKey}
                <span className="agent-type-group-count">
                  {typeAgents.length}
                </span>
              </h2>
              <div className="agent-grid">
                {typeAgents.map((agent) => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    nodeType={nt}
                    isEditing={editingAgentId === agent.id}
                    onToggleEdit={() =>
                      setEditingAgentId(
                        editingAgentId === agent.id ? null : agent.id
                      )
                    }
                    onDelete={() => handleDeleteAgent(agent.id)}
                  />
                ))}
              </div>
            </div>
          );
        })
      )}
    </main>
  );
}

function AgentCard({
  agent,
  nodeType,
  isEditing,
  onToggleEdit,
  onDelete,
}: {
  agent: Agent;
  nodeType?: NodeType;
  isEditing: boolean;
  onToggleEdit: () => void;
  onDelete: () => void;
}) {
  // Summarize params for display
  const paramSummary = Object.entries(agent.params)
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .slice(0, 3)
    .map(([k, v]) => {
      const display = Array.isArray(v) ? `${v.length} items` : String(v).slice(0, 40);
      return `${k}: ${display}`;
    });

  return (
    <div className={`card glass agent-card ${isEditing ? 'agent-card-expanded' : ''}`}>
      <div className="agent-card-header">
        <div className="agent-card-info">
          <h3 className="agent-card-name">{agent.name}</h3>
          {agent.description && (
            <p className="agent-card-desc">{agent.description}</p>
          )}
          <span className="badge badge-type">{nodeType?.display_name || agent.node_type_key}</span>
        </div>
        <div className="agent-card-actions">
          <button className="btn-icon" onClick={onToggleEdit}>
            {isEditing ? 'Hide' : 'Details'}
          </button>
          <button className="btn-icon btn-icon-danger" onClick={onDelete}>
            Delete
          </button>
        </div>
      </div>

      {!isEditing && paramSummary.length > 0 && (
        <div className="agent-card-params-summary">
          {paramSummary.map((p, i) => (
            <span key={i} className="param-chip">{p}</span>
          ))}
          {Object.keys(agent.params).length > 3 && (
            <span className="param-chip param-chip-more">
              +{Object.keys(agent.params).length - 3} more
            </span>
          )}
        </div>
      )}

      {isEditing && nodeType && (
        <div className="agent-card-details">
          <DynamicForm
            schema={nodeType.config_schema}
            values={agent.params}
            onChange={() => {}}
            disabled={true}
          />
          <p className="text-muted text-sm" style={{ marginTop: '0.75rem' }}>
            ID: <code>{agent.id}</code>
          </p>
        </div>
      )}
    </div>
  );
}
