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

  useEffect(() => {
    async function loadWorkflows() {
      try {
        const res = await fetch(`${API_BASE}/workflows/`);
        if (!res.ok) throw new Error('Failed to fetch workflows');
        setWorkflows(await res.json());
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }
    loadWorkflows();
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  async function executeWorkflow(workflowId: string) {
    setExecutingId(workflowId);
    setEvents([]);
    setThreadId(null);

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
        }
      };
      es.onerror = () => {
        console.error('SSE stream error');
      };
      eventSourceRef.current = es;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
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
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
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
    </main>
  );
}
