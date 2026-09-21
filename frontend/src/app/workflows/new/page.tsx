'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { API_BASE } from '@/lib/api';

interface Agent {
  id: string;
  name: string;
  description: string;
  node_type_key: string;
  params: Record<string, any>;
}

interface NodeType {
  type_key: string;
  display_name: string;
  icon: string;
}

interface WorkflowNode {
  id: string;
  agent_id: string;
  requires_approval: boolean;
}

interface WorkflowEdge {
  from_node: string;
  to_node: string;
  condition: string | null;
}

const ICON_MAP: Record<string, string> = {
  cloud: '☁',
  plug: '⚡',
  'clipboard-check': '📋',
  globe: '🌐',
  terminal: '⌨',
  clock: '⏱',
};

export default function NewWorkflowPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [nodeTypes, setNodeTypes] = useState<NodeType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Workflow state
  const [workflowName, setWorkflowName] = useState('');
  const [workflowDescription, setWorkflowDescription] = useState('');
  const [hitlEnabled, setHitlEnabled] = useState(false);
  const [nodes, setNodes] = useState<WorkflowNode[]>([]);
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
  const [openConditionIdx, setOpenConditionIdx] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  // Auto-generate sequential edges when nodes change, preserving existing conditions
  useEffect(() => {
    if (nodes.length < 2) {
      setEdges([]);
      return;
    }
    setEdges((prevEdges) => {
      const prevMap = new Map<string, string | null>();
      for (const e of prevEdges) {
        prevMap.set(`${e.from_node}->${e.to_node}`, e.condition);
      }
      const autoEdges: WorkflowEdge[] = [];
      for (let i = 0; i < nodes.length - 1; i++) {
        const key = `${nodes[i].id}->${nodes[i + 1].id}`;
        const existingCond = prevMap.has(key) ? prevMap.get(key)! : (prevEdges[i]?.condition || null);
        autoEdges.push({
          from_node: nodes[i].id,
          to_node: nodes[i + 1].id,
          condition: existingCond,
        });
      }
      return autoEdges;
    });
  }, [nodes]);

  function getConditionInfo(condition: string | null) {
    if (!condition || !condition.trim()) {
      return { type: 'always', label: 'Always', icon: '⚡', className: 'cond-always' };
    }
    const cond = condition.trim().toLowerCase();
    if (cond === 'success' || cond === 'completed') {
      return { type: 'success', label: 'On Success', icon: '✓', className: 'cond-success' };
    }
    if (cond === 'failed' || cond === 'failure' || cond === 'error') {
      return { type: 'failed', label: 'On Failure', icon: '✗', className: 'cond-failed' };
    }
    if (cond === 'approved') {
      return { type: 'approved', label: 'When Approved', icon: '🛡', className: 'cond-approved' };
    }
    return { type: 'custom', label: condition, icon: '⚙', className: 'cond-custom' };
  }

  async function fetchData() {
    setLoading(true);
    try {
      const [agentsRes, typesRes] = await Promise.all([
        fetch(`${API_BASE}/agents/`),
        fetch(`${API_BASE}/node-types/`),
      ]);
      if (!agentsRes.ok) throw new Error('Failed to fetch agents');
      if (!typesRes.ok) throw new Error('Failed to fetch node types');
      setAgents(await agentsRes.json());
      setNodeTypes(await typesRes.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function addNode(agentId: string) {
    const nodeId = `step_${nodes.length + 1}`;
    setNodes([...nodes, { id: nodeId, agent_id: agentId, requires_approval: false }]);
  }

  function removeNode(idx: number) {
    setNodes(nodes.filter((_, i) => i !== idx));
  }

  function toggleApproval(idx: number) {
    const updated = [...nodes];
    updated[idx] = { ...updated[idx], requires_approval: !updated[idx].requires_approval };
    setNodes(updated);
  }

  function moveNode(idx: number, direction: 'up' | 'down') {
    if (direction === 'up' && idx === 0) return;
    if (direction === 'down' && idx === nodes.length - 1) return;
    const updated = [...nodes];
    const targetIdx = direction === 'up' ? idx - 1 : idx + 1;
    [updated[idx], updated[targetIdx]] = [updated[targetIdx], updated[idx]];
    setNodes(updated);
  }

  function updateEdgeCondition(idx: number, condition: string) {
    const updated = [...edges];
    updated[idx] = { ...updated[idx], condition: condition || null };
    setEdges(updated);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!workflowName.trim() || nodes.length === 0) return;
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      const res = await fetch(`${API_BASE}/workflows/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: workflowName.trim(),
          description: workflowDescription.trim() || null,
          hitl_enabled: hitlEnabled,
          nodes,
          edges,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to create workflow');
      }
      setSaveSuccess(true);
      setTimeout(() => router.push('/'), 1500);
    } catch (e: any) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  // Group agents by type for the palette
  const agentsByType: Record<string, Agent[]> = {};
  for (const agent of agents) {
    if (!agentsByType[agent.node_type_key]) agentsByType[agent.node_type_key] = [];
    agentsByType[agent.node_type_key].push(agent);
  }

  function getAgentById(id: string) {
    return agents.find((a) => a.id === id);
  }
  function getNodeTypeByKey(key: string) {
    return nodeTypes.find((t) => t.type_key === key);
  }

  if (loading) {
    return (
      <main className="container">
        <div className="page-loading">
          <div className="spinner" />
          <p>Loading workflow builder...</p>
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <h1 className="heading page-title">Workflow Builder</h1>
      <p className="text-muted" style={{ marginBottom: '2rem' }}>
        Compose registered agents into an execution graph. Agents execute in
        sequence; edges are auto-generated. You can add conditions to edges and
        toggle human approval per node.
      </p>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="workflow-builder-layout">
        {/* ───── Sidebar: Agent Palette ───── */}
        <aside className="workflow-palette">
          <h2 className="heading" style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>
            Agent Palette
          </h2>
          {agents.length === 0 ? (
            <p className="text-muted text-sm">
              No agents registered.{' '}
              <a href="/agents" className="link">Create one →</a>
            </p>
          ) : (
            Object.entries(agentsByType).map(([typeKey, typeAgents]) => {
              const nt = getNodeTypeByKey(typeKey);
              return (
                <div key={typeKey} className="palette-group">
                  <h3 className="palette-group-title">
                    {ICON_MAP[nt?.icon || ''] || '●'} {nt?.display_name || typeKey}
                  </h3>
                  {typeAgents.map((agent) => (
                    <button
                      key={agent.id}
                      className="palette-agent-btn"
                      onClick={() => addNode(agent.id)}
                      title={agent.description || agent.name}
                    >
                      <span className="palette-agent-name">{agent.name}</span>
                      <span className="palette-add-icon">+</span>
                    </button>
                  ))}
                </div>
              );
            })
          )}
        </aside>

        {/* ───── Main: Workflow Canvas ───── */}
        <section className="workflow-canvas">
          <form onSubmit={handleSave}>
            {/* Workflow metadata */}
            <div className="card glass" style={{ marginBottom: '1.5rem' }}>
              <div className="workflow-meta-grid">
                <div className="form-field">
                  <label className="form-label">
                    Workflow Name <span className="form-required">*</span>
                  </label>
                  <input
                    type="text"
                    className="form-input"
                    value={workflowName}
                    onChange={(e) => setWorkflowName(e.target.value)}
                    placeholder="e.g., Design Review Pipeline"
                    required
                  />
                </div>
                <div className="form-field">
                  <label className="form-label">Description</label>
                  <input
                    type="text"
                    className="form-input"
                    value={workflowDescription}
                    onChange={(e) => setWorkflowDescription(e.target.value)}
                    placeholder="What this workflow does"
                  />
                </div>
              </div>
              <label className="form-toggle-wrapper" style={{ marginTop: '0.75rem' }}>
                <input
                  type="checkbox"
                  className="form-toggle"
                  checked={hitlEnabled}
                  onChange={(e) => setHitlEnabled(e.target.checked)}
                />
                <span className="form-toggle-label">
                  Human-in-the-Loop enabled
                </span>
              </label>
            </div>

            {/* Nodes + Edges */}
            {nodes.length === 0 ? (
              <div className="empty-state" style={{ padding: '3rem' }}>
                <p className="empty-state-icon">⊞</p>
                <h3>No steps added yet</h3>
                <p className="text-muted">
                  Click an agent in the palette to add it as a workflow step.
                </p>
              </div>
            ) : (
              <div className="workflow-nodes-list">
                {nodes.map((node, idx) => {
                  const agent = getAgentById(node.agent_id);
                  const nt = agent ? getNodeTypeByKey(agent.node_type_key) : null;
                  const edge = edges[idx]; // edge FROM this node to next

                  return (
                    <div key={node.id}>
                      {/* Node card */}
                      <div className="card glass workflow-node-card">
                        <div className="workflow-node-header">
                          <div className="workflow-node-info">
                            <span className="workflow-node-step">
                              Step {idx + 1}
                            </span>
                            <span className="workflow-node-icon">
                              {ICON_MAP[nt?.icon || ''] || '●'}
                            </span>
                            <div>
                              <h3 className="workflow-node-name">
                                {agent?.name || node.agent_id}
                              </h3>
                              <span className="badge badge-type-sm">
                                {nt?.display_name || agent?.node_type_key || '?'}
                              </span>
                            </div>
                          </div>
                          <div className="workflow-node-controls">
                            {hitlEnabled && (
                              <label className="form-toggle-wrapper compact">
                                <input
                                  type="checkbox"
                                  className="form-toggle"
                                  checked={node.requires_approval}
                                  onChange={() => toggleApproval(idx)}
                                />
                                <span className="form-toggle-label text-sm">
                                  Approval
                                </span>
                              </label>
                            )}
                            <button
                              type="button"
                              className="btn-icon"
                              onClick={() => moveNode(idx, 'up')}
                              disabled={idx === 0}
                              title="Move up"
                            >
                              ↑
                            </button>
                            <button
                              type="button"
                              className="btn-icon"
                              onClick={() => moveNode(idx, 'down')}
                              disabled={idx === nodes.length - 1}
                              title="Move down"
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              className="btn-icon btn-icon-danger"
                              onClick={() => removeNode(idx)}
                              title="Remove"
                            >
                              ✕
                            </button>
                          </div>
                        </div>
                        {agent && (
                          <div className="workflow-node-params">
                            {Object.entries(agent.params)
                              .filter(([, v]) => v !== '' && v !== null)
                              .slice(0, 2)
                              .map(([k, v]) => (
                                <span key={k} className="param-chip">
                                  {k}: {Array.isArray(v) ? `${v.length} items` : String(v).slice(0, 30)}
                                </span>
                              ))}
                          </div>
                        )}
                      </div>

                      {/* Edge connector (if not last node) */}
                      {idx < nodes.length - 1 && edge && (() => {
                        const condInfo = getConditionInfo(edge.condition);
                        const isOpen = openConditionIdx === idx;
                        return (
                          <div className="workflow-edge-connector">
                            <div className="workflow-edge-line" />
                            <div className="workflow-edge-condition">
                              <button
                                type="button"
                                className={`workflow-edge-condition-btn ${condInfo.className}`}
                                onClick={() => setOpenConditionIdx(isOpen ? null : idx)}
                                title="Click to choose transition condition"
                              >
                                <span className="cond-icon">{condInfo.icon}</span>
                                <span>{condInfo.label}</span>
                                <span className="cond-chevron">{isOpen ? '▲' : '▼'}</span>
                              </button>

                              {isOpen && (
                                <div className="workflow-cond-popover">
                                  <div className="workflow-cond-popover-header">
                                    <span>Transition Condition</span>
                                    <button
                                      type="button"
                                      className="btn-icon"
                                      style={{ width: '18px', height: '18px', fontSize: '10px' }}
                                      onClick={() => setOpenConditionIdx(null)}
                                    >
                                      ✕
                                    </button>
                                  </div>
                                  <div className="workflow-cond-options">
                                    <button
                                      type="button"
                                      className={`workflow-cond-option ${condInfo.type === 'always' ? 'active' : ''}`}
                                      onClick={() => {
                                        updateEdgeCondition(idx, '');
                                        setOpenConditionIdx(null);
                                      }}
                                    >
                                      <span>⚡ Always (Normal Flow)</span>
                                      {condInfo.type === 'always' && <span>✓</span>}
                                    </button>

                                    <button
                                      type="button"
                                      className={`workflow-cond-option ${condInfo.type === 'success' ? 'active' : ''}`}
                                      onClick={() => {
                                        updateEdgeCondition(idx, 'success');
                                        setOpenConditionIdx(null);
                                      }}
                                    >
                                      <span>✓ On Success</span>
                                      {condInfo.type === 'success' && <span>✓</span>}
                                    </button>

                                    <button
                                      type="button"
                                      className={`workflow-cond-option ${condInfo.type === 'failed' ? 'active' : ''}`}
                                      onClick={() => {
                                        updateEdgeCondition(idx, 'failed');
                                        setOpenConditionIdx(null);
                                      }}
                                    >
                                      <span>✗ On Failure</span>
                                      {condInfo.type === 'failed' && <span>✓</span>}
                                    </button>

                                    <button
                                      type="button"
                                      className={`workflow-cond-option ${condInfo.type === 'approved' ? 'active' : ''}`}
                                      onClick={() => {
                                        updateEdgeCondition(idx, 'approved');
                                        setOpenConditionIdx(null);
                                      }}
                                    >
                                      <span>🛡 When Approved</span>
                                      {condInfo.type === 'approved' && <span>✓</span>}
                                    </button>
                                  </div>

                                  <div className="workflow-cond-custom-input">
                                    <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginBottom: '0.35rem' }}>
                                      Or custom condition:
                                    </div>
                                    <input
                                      type="text"
                                      className="form-input form-input-sm"
                                      value={edge.condition || ''}
                                      onChange={(e) => updateEdgeCondition(idx, e.target.value)}
                                      placeholder="e.g. status == 'completed'"
                                    />
                                  </div>
                                </div>
                              )}
                            </div>
                            <div className="workflow-edge-line" />
                          </div>
                        );
                      })()}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Save */}
            {nodes.length > 0 && (
              <div className="form-actions" style={{ marginTop: '1.5rem' }}>
                {saveError && <div className="alert alert-error">{saveError}</div>}
                {saveSuccess && (
                  <div className="alert alert-success">
                    Workflow created! Redirecting...
                  </div>
                )}
                <button
                  type="submit"
                  className="btn btn-glow"
                  disabled={saving || !workflowName.trim()}
                >
                  {saving ? 'Saving...' : 'Save Workflow'}
                </button>
              </div>
            )}
          </form>
        </section>
      </div>
    </main>
  );
}
