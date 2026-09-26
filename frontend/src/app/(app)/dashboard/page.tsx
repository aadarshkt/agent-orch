'use client';

import { useState, useEffect, useRef } from 'react';
import { API_BASE } from '@/lib/api';

interface Workflow {
  id: string;
  name: string;
  description: string;
  hitl_enabled: boolean;
  node_count: number;
  created_at: string;
}

interface WorkflowEvent {
  event: string;
  data: string;
}

interface RunSummary {
  thread_id: string;
  workflow_id: string;
  status: string;
  current_step: string | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
}

interface NodeState {
  output: string | null;
  artifacts: string[];
  exit_code: number | null;
  commit: string | null;
  commit_url: string | null;
  workspace: string | null;
}

interface RunState {
  thread_id: string;
  workflow_id: string;
  status: string;
  current_step: string | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
  graph_status: string | null;
  approval_status: string | null;
  next: string[];
  nodes: Record<string, NodeState>;
}

function statusBadge(status: string) {
  if (status === 'completed') return 'badge badge-success';
  if (status === 'failed') return 'badge badge-error';
  if (status === 'paused') return 'badge badge-warning';
  return 'badge badge-info';
}

function statusDot(status: string) {
  if (status === 'completed') return 'status-dot status-dot-success';
  if (status === 'failed') return 'status-dot status-dot-danger';
  if (status === 'paused') return 'status-dot status-dot-warning';
  return 'status-dot status-dot-info';
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '';
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export default function Dashboard() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Execution state
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [isResuming, setIsResuming] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Persisted run outputs (read from the checkpointer)
  const [runs, setRuns] = useState<Record<string, RunSummary[]>>({});
  const [openRunsId, setOpenRunsId] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [stateLoading, setStateLoading] = useState(false);

  useEffect(() => {
    async function loadWorkflows() {
      try {
        const res = await fetch(`${API_BASE}/workflows/`);
        if (!res.ok) throw new Error('Failed to fetch workflows');
        setWorkflows(await res.json());
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    }
    loadWorkflows();
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  async function loadRuns(workflowId: string) {
    try {
      const res = await fetch(`${API_BASE}/workflows/${workflowId}/executions`);
      if (!res.ok) throw new Error('Failed to fetch runs');
      const data: RunSummary[] = await res.json();
      setRuns((prev) => ({ ...prev, [workflowId]: data }));
      setOpenRunsId((prev) => (prev === workflowId ? null : workflowId));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function loadRunState(id: string) {
    setStateLoading(true);
    try {
      const res = await fetch(`${API_BASE}/workflows/${id}/state`);
      if (!res.ok) throw new Error('Failed to fetch run output');
      setRunState(await res.json());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setStateLoading(false);
    }
  }

  async function executeWorkflow(workflowId: string) {
    setExecutingId(workflowId);
    setEvents([]);
    setThreadId(null);
    setRunState(null);

    try {
      const res = await fetch(`${API_BASE}/workflows/${workflowId}/execute`, {
        method: 'POST',
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to execute workflow');
      }
      const data = await res.json();
      setThreadId(data.thread_id);

      // Start SSE listener
      eventSourceRef.current?.close();
      const es = new EventSource(
        `${API_BASE}/events/stream?thread_id=${data.thread_id}`
      );
      es.onmessage = (e) => {
        const parsed = JSON.parse(e.data);
        if (parsed.event === 'ping') return;
        setEvents((prev) => [...prev, parsed]);
        if (parsed.event === 'complete' || parsed.event === 'error') {
          setExecutingId(null);
          // The stream is ephemeral — load the durable copy once it settles.
          loadRunState(data.thread_id);
          loadRuns(workflowId);
        }
      };
      es.onerror = () => {
        console.error('SSE stream error');
      };
      eventSourceRef.current = es;
    } catch (e) {
      setError(errorMessage(e));
      setExecutingId(null);
    }
  }

  async function resumeWorkflow() {
    if (!threadId) return;
    setIsResuming(true);
    try {
      const res = await fetch(`${API_BASE}/workflows/${threadId}/resume`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to resume workflow');
      }
      setIsResuming(false);
    } catch (e) {
      setError(errorMessage(e));
      setIsResuming(false);
    }
  }

  const lastEvent = events.filter((e) => e.event !== 'ping').slice(-1)[0];
  const isPendingReview = lastEvent?.event === 'review';
  const isComplete = lastEvent?.event === 'complete';
  const isError = lastEvent?.event === 'error';

  if (loading) {
    return (
      <main className="container">
        <div className="page-loading">
          <div className="spinner" />
          <p>Loading dashboard...</p>
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <div className="page-header">
        <div>
          <h1 className="heading page-title">Dashboard</h1>
          <p className="text-muted">
            Select a workflow to execute and monitor its progress in real-time.
          </p>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* ───── Workflow List ───── */}
      {workflows.length === 0 ? (
        <div className="empty-state">
          <h3>No workflows yet</h3>
          <p className="text-muted">
            <a href="/agents" className="link">Create agents</a>, then{' '}
            <a href="/workflows/new" className="link">build a workflow</a> to get started.
          </p>
        </div>
      ) : (
        <div className="workflow-list-grid">
          {workflows.map((w) => (
            <div key={w.id} className="card glass workflow-list-card">
              <div className="workflow-list-card-header">
                <div>
                  <h3 className="heading" style={{ fontSize: '1.1rem' }}>
                    {w.name}
                  </h3>
                  {w.description && (
                    <p className="text-muted text-sm">{w.description}</p>
                  )}
                </div>
                <div className="workflow-list-card-meta">
                  <span className="badge badge-info">{w.node_count} steps</span>
                  {w.hitl_enabled && (
                    <span className="badge badge-warning">HITL</span>
                  )}
                </div>
              </div>
              <button
                className="btn btn-glow btn-block mt-4"
                onClick={() => executeWorkflow(w.id)}
                disabled={executingId !== null}
              >
                {executingId === w.id ? 'Running...' : 'Execute'}
              </button>
              <button
                className="btn btn-ghost btn-block mt-2"
                onClick={() => loadRuns(w.id)}
              >
                {openRunsId === w.id ? 'Hide runs' : 'Runs'}
              </button>

              {openRunsId === w.id && (
                <div className="run-list">
                  {(runs[w.id] || []).length === 0 ? (
                    <span className="text-muted text-sm">No runs yet.</span>
                  ) : (
                    runs[w.id].map((run) => (
                      <button
                        key={run.thread_id}
                        className={`run-item ${
                          runState?.thread_id === run.thread_id ? 'run-item-active' : ''
                        }`}
                        onClick={() => {
                          setThreadId(run.thread_id);
                          loadRunState(run.thread_id);
                        }}
                      >
                        <span className={statusDot(run.status)} />
                        <span>{run.thread_id.slice(0, 8)}</span>
                        <span className="text-muted">{run.status}</span>
                        <span className="run-item-time">
                          {formatTime(run.created_at)}
                        </span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ───── Execution Monitor ───── */}
      {threadId && (
        <div className="card glass" style={{ marginTop: '2rem' }}>
          <div className="flex items-center justify-between">
            <h2 className="heading">
              Execution Monitor{' '}
              <span className="text-sm text-muted">({threadId.slice(0, 8)}...)</span>
            </h2>
            <div className="flex gap-4 items-center">
              {isPendingReview && (
                <button
                  className="btn btn-success"
                  onClick={resumeWorkflow}
                  disabled={isResuming}
                >
                  {isResuming ? 'Resuming...' : 'Approve & Resume'}
                </button>
              )}
              {isComplete && <span className="badge badge-success">Completed</span>}
              {isError && <span className="badge badge-error">Error</span>}
            </div>
          </div>
          <div className="event-log">
            {events.length === 0 ? (
              <span className="text-muted">Waiting for events...</span>
            ) : (
              events.map((ev, idx) => (
                <div
                  key={idx}
                  className={`event-log-entry event-${ev.event}`}
                >
                  <span className="event-log-time">
                    {new Date().toLocaleTimeString()}
                  </span>
                  <span className="event-log-type">{ev.event}</span>
                  <span className="event-log-data">{ev.data}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* ───── Run Output (persisted) ───── */}
      {(runState || stateLoading) && (
        <div className="card glass" style={{ marginTop: '2rem' }}>
          <div className="flex items-center justify-between">
            <h2 className="heading">
              Run Output{' '}
              <span className="text-sm text-muted">
                {runState ? `(${runState.thread_id.slice(0, 8)}...)` : '...'}
              </span>
            </h2>
            <div className="flex gap-4 items-center">
              {runState && (
                <span className={statusBadge(runState.status)}>
                  {runState.status}
                </span>
              )}
              {runState && runState.thread_id && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => loadRunState(runState.thread_id)}
                >
                  Refresh
                </button>
              )}
            </div>
          </div>

          {stateLoading && !runState ? (
            <p className="text-muted mt-4">Loading run output...</p>
          ) : runState ? (
            <>
              <p className="text-muted text-sm mt-2">
                Persisted from the checkpointer — survives page reloads.
                {runState.error ? ` Error: ${runState.error}` : ''}
              </p>
              <div className="run-nodes">
                {Object.entries(runState.nodes).map(([nodeId, node]) => (
                  <div key={nodeId} className="run-node">
                    <div className="run-node-header">
                      <span className={statusDot(node.exit_code === 0 ? 'completed' : 'info')} />
                      <span className="run-node-id">{nodeId}</span>
                      {node.exit_code !== null && node.exit_code !== undefined && (
                        <span className="badge badge-muted">
                          exit {node.exit_code}
                        </span>
                      )}
                      {node.artifacts.length > 0 && (
                        <span className="badge badge-info">
                          {node.artifacts.length} artifacts
                        </span>
                      )}
                    </div>

                    {node.commit && (
                      <p className="text-muted text-sm mt-2">{node.commit}</p>
                    )}
                    {node.commit_url && (
                      <a
                        className="link run-commit"
                        href={node.commit_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {node.commit_url}
                      </a>
                    )}

                    {node.artifacts.length > 0 && (
                      <ul className="run-artifacts">
                        {node.artifacts.map((path) => (
                          <li key={path}>{path}</li>
                        ))}
                      </ul>
                    )}

                    {node.output && (
                      <details className="run-output">
                        <summary>Output</summary>
                        <pre>{node.output}</pre>
                      </details>
                    )}

                    {!node.output && node.artifacts.length === 0 && !node.commit && (
                      <p className="text-muted text-sm mt-2">
                        No output recorded for this node.
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      )}
    </main>
  );
}
