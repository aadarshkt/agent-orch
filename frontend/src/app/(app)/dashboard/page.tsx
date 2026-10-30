'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { API_BASE } from '@/lib/api';

interface Workflow {
  id: string;
  name: string;
  description: string;
  hitl_enabled: boolean;
  node_count: number;
  created_at: string;
}

/* The stream is ephemeral, so each event keeps the wall-clock time it was
   received at. Deriving the time at render instead would stamp every row with
   the time of the last re-render, which is not what the log claims to show. */
interface StreamEvent {
  event: string;
  data: string;
  receivedAt: number;
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

/* The high-level view of a run: one row per node, in declared graph order,
   derived by the backend from the checkpoint rather than replayed from the
   event stream — so it is still correct after a reload. */
interface RunStep {
  id: string;
  index: number;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'failed';
  exit_code: number | null;
  requires_approval: boolean;
  artifact_count: number;
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
  approval_reviewer: string | null;
  approval_comment: string | null;
  next: string[];
  steps: RunStep[];
  nodes: Record<string, NodeState>;
}

/* ------------------------------------------------------------------ Status */

/* A status is an unknown string until the backend says otherwise, so every
   lookup falls back to a neutral tone rather than a wrong one. */
const BADGE_TONE: Record<string, string> = {
  completed: 'badge-success',
  failed: 'badge-error',
  paused: 'badge-warning',
  rejected: 'badge-error',
  running: 'badge-info',
};

const DOT_TONE: Record<string, string> = {
  completed: 'status-dot-success',
  failed: 'status-dot-danger',
  paused: 'status-dot-warning',
  rejected: 'status-dot-danger',
  running: 'status-dot-info',
};

const STEP_TONE: Record<string, string> = {
  completed: 'status-dot-success',
  failed: 'status-dot-danger',
  running: 'status-dot-info status-dot-live',
  waiting: 'status-dot-warning',
  pending: 'status-dot-idle',
};

function statusBadge(status: string) {
  return `badge ${BADGE_TONE[status] ?? 'badge-info'}`;
}

function statusDot(status: string) {
  return `status-dot ${DOT_TONE[status] ?? 'status-dot-idle'}`;
}

/* A node's dot reports what actually happened: exit 0 succeeded, a non-zero
   code failed, and no code at all means it never finished. */
function nodeExitState(node: NodeState) {
  if (node.exit_code === null || node.exit_code === undefined) return 'idle';
  return node.exit_code === 0 ? 'completed' : 'failed';
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '';
}

function formatClock(ms: number) {
  return new Date(ms).toLocaleTimeString();
}

function errorMessage(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

/* Pulls the node identifier out of an event payload for its own column. The
   payload itself is still rendered in full, so this only adds an index to it. */
function eventNodeId(data: string): string | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (parsed && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>;
      for (const key of ['node', 'node_id', 'agent', 'step']) {
        const value = record[key];
        if (typeof value === 'string' && value) return value;
      }
    }
  } catch {
    /* Not JSON — the payload column carries it on its own. */
  }
  return null;
}

function shortId(id: string) {
  return id.slice(0, 8);
}

/* -------------------------------------------------------------- Copy button */

function CopyButton({
  value,
  copyKey,
  label,
  copiedKey,
  onCopy,
}: {
  value: string;
  copyKey: string;
  label: string;
  copiedKey: string | null;
  onCopy: (value: string, key: string) => void;
}) {
  const copied = copiedKey === copyKey;
  return (
    <button
      type="button"
      className={`btn-copy ${copied ? 'btn-copy-copied' : ''}`}
      onClick={() => onCopy(value, copyKey)}
      title={value}
    >
      {copied ? 'copied' : label}
    </button>
  );
}

/* ------------------------------------------------------------------ Endpoint */

function Endpoint({
  method,
  path,
  copyValue,
  copyKey,
  copiedKey,
  onCopy,
  copyLabel = 'curl',
  displayPath,
  stacked = false,
}: {
  method: 'GET' | 'POST';
  path: string;
  copyValue: string;
  copyKey: string;
  copiedKey: string | null;
  onCopy: (value: string, key: string) => void;
  copyLabel?: string;
  /* Abbreviated form for narrow containers. The full path stays in the tooltip
     and in the copied command, so nothing is hidden by the shortening. */
  displayPath?: string;
  /* Give the path its own full-width line instead of sharing one with the
     method chip and the copy control. */
  stacked?: boolean;
}) {
  return (
    <div className={`endpoint ${stacked ? 'endpoint-wrap' : ''}`}>
      <span
        className={`endpoint-method endpoint-method-${method.toLowerCase()}`}
      >
        {method}
      </span>
      <span className="endpoint-path" title={path}>
        {displayPath ?? path}
      </span>
      <CopyButton
        value={copyValue}
        copyKey={copyKey}
        label={copyLabel}
        copiedKey={copiedKey}
        onCopy={onCopy}
      />
    </div>
  );
}

export default function Dashboard() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // Execution state
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  /* Which verdict is in flight, so both buttons can disable together. */
  const [reviewing, setReviewing] = useState<'approve' | 'reject' | null>(null);
  const [reviewComment, setReviewComment] = useState('');
  const [reviewerName, setReviewerName] = useState('');
  /* The thread the currently open SSE stream belongs to. Null when no stream is
     open — including when a past run is merely being inspected. */
  const [streamedThreadId, setStreamedThreadId] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Persisted run outputs (read from the checkpointer)
  const [runs, setRuns] = useState<Record<string, RunSummary[]>>({});
  const [openRunsId, setOpenRunsId] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [stateLoading, setStateLoading] = useState(false);

  // Console affordances
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

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
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  // Tail the log, unless the reader has scrolled up to look at something.
  useEffect(() => {
    if (!follow) return;
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events, follow]);

  function onLogScroll() {
    const el = logRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollow(distanceFromBottom < 24);
  }

  async function copyText(value: string, key: string) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard access can be denied; the value is still selectable on screen.
      return;
    }
    setCopiedKey(key);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopiedKey(null), 1400);
  }

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
    setStreamedThreadId(null);
    setRunState(null);
    setFollow(true);

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
      setStreamedThreadId(data.thread_id);

      // Start SSE listener
      eventSourceRef.current?.close();
      const es = new EventSource(
        `${API_BASE}/events/stream?thread_id=${data.thread_id}`
      );
      es.onmessage = (e) => {
        const parsed = JSON.parse(e.data);
        if (parsed.event === 'ping') return;
        setEvents((prev) => [
          ...prev,
          { event: parsed.event, data: parsed.data, receivedAt: Date.now() },
        ]);
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

  /* Record a human verdict on a run paused at an approval gate.

     The gate is driven by the persisted execution row, never by the event
     stream: SSE has no replay buffer, so a reviewer who reloads the page
     mid-run would otherwise be told nothing is waiting. */
  async function submitReview(decision: 'approve' | 'reject') {
    if (!threadId) return;
    setReviewing(decision);
    try {
      const res = await fetch(`${API_BASE}/workflows/${threadId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          comment: reviewComment.trim() || null,
          reviewer: reviewerName.trim() || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || `Failed to ${decision} run`);
      }
      setReviewComment('');
      setRunState(null);
      // The verdict is durable now; re-read it rather than trusting the call.
      loadRunState(threadId);
      if (runState) loadRuns(runState.workflow_id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setReviewing(null);
    }
  }

  const lastEvent = events[events.length - 1];
  const isComplete = lastEvent?.event === 'complete';
  const isError = lastEvent?.event === 'error';
  const isRunning = executingId !== null;

  /* The gate is a property of the run, not of this page's event buffer. A run
     can be paused whether or not anyone is watching the stream. */
  const selectedRun = runState?.thread_id === threadId ? runState : null;
  const isPendingReview = runState?.status === 'paused';
  const pendingStep = isPendingReview
    ? selectedRun?.steps.find((s) => s.status === 'waiting')
    : undefined;

  /* Selecting a run from history sets `threadId` without opening a stream, so
     the monitor can be showing a run it has no events for. SSE is live-only and
     is never replayed, and saying "waiting" there would imply otherwise. */
  const hasLiveStream = streamedThreadId !== null && streamedThreadId === threadId;

  const needle = query.trim().toLowerCase();
  const visibleWorkflows = needle
    ? workflows.filter(
        (w) =>
          w.name.toLowerCase().includes(needle) ||
          (w.description || '').toLowerCase().includes(needle) ||
          w.id.toLowerCase().includes(needle)
      )
    : workflows;

  const totalSteps = workflows.reduce((sum, w) => sum + (w.node_count || 0), 0);
  const hitlCount = workflows.filter((w) => w.hitl_enabled).length;

  if (loading) {
    return (
      <main className="container">
        <div className="page-loading">
          <div className="spinner" />
          <p>Loading run console...</p>
        </div>
      </main>
    );
  }

  return (
    <main className="container">
      <div className="page-header">
        <div>
          <p className="eyebrow">Run console</p>
          <h1 className="heading page-title">Dashboard</h1>
          <p className="text-muted">
            Execute a workflow and watch the graph step through. Every action
            below shows the request it makes, so anything the UI does can be
            reproduced from your terminal.
          </p>
        </div>
        <div className="row gap-2 flex-wrap">
          <a
            className="btn btn-secondary"
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
          >
            API docs
          </a>
          <Link className="btn btn-primary" href="/workflows/new">
            New workflow
          </Link>
        </div>
      </div>

      {error && (
        <div className="alert alert-error">
          <span className="flex-1">{error}</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setError(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ───── Counts ───── */}
      {workflows.length > 0 && (
        <div className="stat-strip">
          <div className="stat-cell">
            <div className="stat-value">{workflows.length}</div>
            <div className="stat-label">workflows</div>
          </div>
          <div className="stat-cell">
            <div className="stat-value">{totalSteps}</div>
            <div className="stat-label">graph steps</div>
          </div>
          <div className="stat-cell">
            <div className="stat-value">{hitlCount}</div>
            <div className="stat-label">hitl enabled</div>
          </div>
          <div className="stat-cell">
            <div className="stat-value">{isRunning ? 'live' : 'idle'}</div>
            <div className="stat-label">runner state</div>
          </div>
        </div>
      )}

      {/* ───── Workflows ───── */}
      <section className="dash-section">
        <div className="dash-section-header">
          <h2 className="dash-section-title">Workflows</h2>
          {workflows.length > 0 && (
            <span className="filter-count">
              {visibleWorkflows.length} of {workflows.length} shown
            </span>
          )}
        </div>

        {workflows.length === 0 ? (
          <div className="empty-state">
            <h3>No workflows yet</h3>
            <p className="text-muted">
              <Link href="/agents" className="link">
                Create agents
              </Link>
              , then{' '}
              <Link href="/workflows/new" className="link">
                build a workflow
              </Link>{' '}
              to get started.
            </p>
          </div>
        ) : (
          <>
            <div className="filter-bar">
              <input
                type="search"
                className="filter-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="filter by name, description or id..."
                aria-label="Filter workflows"
              />
            </div>

            {visibleWorkflows.length === 0 ? (
              <div className="empty-state">
                <h3>No matches</h3>
                <p className="text-muted">
                  No workflow matches{' '}
                  <span className="mono">{query}</span>. Clear the filter to see
                  all {workflows.length}.
                </p>
              </div>
            ) : (
              <div className="workflow-list-grid">
                {visibleWorkflows.map((w) => {
                  const isThisOne = executingId === w.id;
                  const runList = runs[w.id] || [];
                  return (
                    <div key={w.id} className="card workflow-list-card">
                      <div className="workflow-list-card-header">
                        <div className="flex-1">
                          <h3 className="workflow-list-card-title">{w.name}</h3>
                          {w.description && (
                            <p className="text-muted text-sm">{w.description}</p>
                          )}
                        </div>
                        <div className="workflow-list-card-meta">
                          <span className="badge badge-muted">
                            {w.node_count} steps
                          </span>
                          {w.hitl_enabled && (
                            <span className="badge badge-warning">hitl</span>
                          )}
                        </div>
                      </div>

                      <Endpoint
                        method="POST"
                        path={`/workflows/${w.id}/execute`}
                        displayPath={`/workflows/${shortId(w.id)}…/execute`}
                        stacked
                        copyValue={`curl -X POST ${API_BASE}/workflows/${w.id}/execute`}
                        copyKey={`exec-${w.id}`}
                        copiedKey={copiedKey}
                        onCopy={copyText}
                      />

                      <div className="workflow-list-card-actions">
                        <button
                          className="btn btn-primary btn-block"
                          onClick={() => executeWorkflow(w.id)}
                          disabled={isRunning}
                        >
                          {isThisOne ? 'Running...' : 'Execute'}
                        </button>
                        <button
                          className="btn btn-secondary"
                          onClick={() => loadRuns(w.id)}
                        >
                          {openRunsId === w.id ? 'Hide' : 'Runs'}
                        </button>
                      </div>

                      {openRunsId === w.id && (
                        <div className="workflow-list-card-runs">
                          <Endpoint
                            method="GET"
                            path={`/workflows/${w.id}/executions`}
                            displayPath={`/workflows/${shortId(w.id)}…/executions`}
                            stacked
                            copyValue={`curl ${API_BASE}/workflows/${w.id}/executions`}
                            copyKey={`runs-${w.id}`}
                            copiedKey={copiedKey}
                            onCopy={copyText}
                            copyLabel="curl"
                          />

                          {runList.length === 0 ? (
                            <p className="text-muted text-sm mt-2">
                              No runs recorded for this workflow yet.
                            </p>
                          ) : (
                            <div className="run-list">
                              {runList.map((run) => (
                                <button
                                  key={run.thread_id}
                                  title={run.thread_id}
                                  aria-current={
                                    runState?.thread_id === run.thread_id
                                  }
                                  className={`run-item ${
                                    runState?.thread_id === run.thread_id
                                      ? 'run-item-active'
                                      : ''
                                  }`}
                                  onClick={() => {
                                    setThreadId(run.thread_id);
                                    loadRunState(run.thread_id);
                                  }}
                                >
                                  <span className={statusDot(run.status)} />
                                  <span className="run-item-id">
                                    {shortId(run.thread_id)}
                                  </span>
                                  <span className="run-item-meta">
                                    <span className="run-item-status">
                                      {run.status}
                                    </span>
                                    <span className="run-item-time">
                                      {formatTime(run.created_at)}
                                    </span>
                                  </span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </section>

      {/* ───── Execution Monitor ───── */}
      {threadId && (
        <section className="dash-section">
          <div className="panel">
            <div className="panel-header">
              <div className="row gap-3 items-center flex-wrap">
                <span className="panel-title panel-title-live">
                  {isRunning ? (
                    <span className="status-dot status-dot-live" />
                  ) : null}
                  Execution monitor
                </span>
                <span className="text-xs text-muted mono">
                  thread {shortId(threadId)}
                </span>
              </div>
              <div className="row gap-2 items-center flex-wrap">
                {isPendingReview && (
                  <span className="badge badge-warning">awaiting review</span>
                )}
                {runState?.status === 'rejected' && (
                  <span className="badge badge-error">rejected</span>
                )}
                {isComplete && (
                  <span className="badge badge-success">completed</span>
                )}
                {isError && <span className="badge badge-error">error</span>}
                {!isRunning && !hasLiveStream && (
                  <span className="badge badge-muted">no live stream</span>
                )}
              </div>
            </div>

            {/* The gate lives with the run's own state, so it stays put across
                reloads and can be acted on later by whoever opens the run. */}
            {isPendingReview && (
              <div className="review-gate">
                <div className="review-gate-head">
                  <span className="review-gate-title">
                    Waiting for a decision
                    {pendingStep ? (
                      <span className="review-gate-step mono">
                        at step {pendingStep.index + 1} · {pendingStep.id}
                      </span>
                    ) : null}
                  </span>
                  <span className="text-xs text-muted">
                    Approving runs the step. Rejecting ends the run — nothing
                    after this point executes.
                  </span>
                </div>

                <div className="review-gate-fields">
                  <label className="review-field">
                    <span className="review-field-label">Your name</span>
                    <input
                      className="input"
                      value={reviewerName}
                      onChange={(e) => setReviewerName(e.target.value)}
                      placeholder="optional"
                      disabled={reviewing !== null}
                    />
                  </label>
                  <label className="review-field">
                    <span className="review-field-label">
                      {pendingStep?.id ? `Notes on ${pendingStep.id}` : 'Notes'}
                    </span>
                    <input
                      className="input"
                      value={reviewComment}
                      onChange={(e) => setReviewComment(e.target.value)}
                      placeholder="What did you check, and why?"
                      disabled={reviewing !== null}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && reviewing === null) {
                          submitReview('approve');
                        }
                      }}
                    />
                  </label>
                </div>

                <div className="review-gate-actions">
                  <button
                    className="btn btn-success btn-sm"
                    onClick={() => submitReview('approve')}
                    disabled={reviewing !== null}
                  >
                    {reviewing === 'approve' ? 'Resuming...' : 'Approve & resume'}
                  </button>
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => submitReview('reject')}
                    disabled={reviewing !== null}
                  >
                    {reviewing === 'reject' ? 'Rejecting...' : 'Reject'}
                  </button>
                  {reviewing !== null && (
                    <span className="text-xs text-muted">Recording decision...</span>
                  )}
                </div>
              </div>
            )}

            <div className="panel-toolbar">
              <Endpoint
                method="GET"
                path={`/events/stream?thread_id=${threadId}`}
                copyValue={`curl -N "${API_BASE}/events/stream?thread_id=${threadId}"`}
                copyKey="stream"
                copiedKey={copiedKey}
                onCopy={copyText}
              />
              <CopyButton
                value={threadId}
                copyKey="thread"
                label="thread id"
                copiedKey={copiedKey}
                onCopy={copyText}
              />
              <div className="panel-toolbar-spacer" />
              <button
                type="button"
                className={`btn-icon ${follow ? 'btn-icon-active' : ''}`}
                onClick={() => setFollow((prev) => !prev)}
                title="Follow the tail of the stream"
              >
                {follow ? 'following' : 'paused'}
              </button>
              <button
                type="button"
                className="btn-icon"
                onClick={() => setEvents([])}
                disabled={events.length === 0}
                title="Clear the buffered stream (the durable copy lives under Run output)"
              >
                clear
              </button>
            </div>

            <div
              className="event-log"
              ref={logRef}
              onScroll={onLogScroll}
              role="log"
              aria-live="polite"
              aria-label="Workflow event stream"
            >
              {events.length === 0 ? (
                <span className="event-log-placeholder">
                  {hasLiveStream
                    ? 'Waiting for events...'
                    : 'No stream for this run. Events are live only and are not replayed — run the command above to watch one.'}
                </span>
              ) : (
                events.map((ev, idx) => (
                  <div
                    key={idx}
                    className={`event-log-entry event-${ev.event}`}
                  >
                    <span className="event-log-time">
                      {formatClock(ev.receivedAt)}
                    </span>
                    <span className="event-log-type" title={ev.event}>
                      {ev.event}
                    </span>
                    <span className="event-log-node">
                      {eventNodeId(ev.data) || ''}
                    </span>
                    <span className="event-log-data">{ev.data}</span>
                    <CopyButton
                      value={ev.data}
                      copyKey={`ev-${idx}`}
                      label="copy"
                      copiedKey={copiedKey}
                      onCopy={copyText}
                    />
                  </div>
                ))
              )}
            </div>
          </div>
        </section>
      )}

      {/* ───── Run Output (persisted) ───── */}
      {(runState || stateLoading) && (
        <section className="dash-section">
          <div className="panel">
            <div className="panel-header">
              <div className="row gap-3 items-center flex-wrap">
                <span className="panel-title">Run output</span>
                {runState && (
                  <>
                    <span className={statusBadge(runState.status)}>
                      {runState.status}
                    </span>
                    <span className="text-xs text-muted mono">
                      {shortId(runState.thread_id)}
                    </span>
                  </>
                )}
              </div>
              <div className="row gap-2 items-center flex-wrap">
                {runState && (
                  <>
                    <CopyButton
                      value={runState.thread_id}
                      copyKey="run-thread"
                      label="thread id"
                      copiedKey={copiedKey}
                      onCopy={copyText}
                    />
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => loadRunState(runState.thread_id)}
                    >
                      Refresh
                    </button>
                  </>
                )}
              </div>
            </div>

            <div className="panel-toolbar">
              {runState && (
                <Endpoint
                  method="GET"
                  path={`/workflows/${runState.thread_id}/state`}
                  copyValue={`curl ${API_BASE}/workflows/${runState.thread_id}/state`}
                  copyKey="state"
                  copiedKey={copiedKey}
                  onCopy={copyText}
                />
              )}
            </div>

            <div className="panel-body">
              {stateLoading && !runState ? (
                <div className="page-loading">
                  <div className="spinner" />
                  <p>Loading run output...</p>
                </div>
              ) : runState ? (
                <>
                  {/* High level: where the run is, one row per step. The raw
                      output of each step stays collapsed until asked for. */}
                  {runState.steps.length > 0 && (
                    <div className="run-progress">
                      <div className="run-progress-head">
                        <span className="run-progress-title">
                          {runState.steps.filter((s) => s.status === 'completed').length}{' '}
                          of {runState.steps.length} steps complete
                        </span>
                        {runState.steps.some((s) => s.status === 'running') && (
                          <span className="text-xs text-muted">
                            running {runState.current_step}
                          </span>
                        )}
                        {runState.steps.some((s) => s.status === 'waiting') && (
                          <span className="text-xs text-muted">
                            waiting at {runState.current_step}
                          </span>
                        )}
                      </div>
                      <ol className="run-steps">
                        {runState.steps.map((step) => {
                          const detail = runState.nodes[step.id];
                          const hasDetail = Boolean(
                            detail?.output ||
                              detail?.artifacts?.length ||
                              detail?.commit
                          );
                          return (
                            <li
                              key={step.id}
                              className={`run-step run-step-${step.status}`}
                            >
                              <span
                                className={`status-dot ${STEP_TONE[step.status] ?? 'status-dot-idle'}`}
                              />
                              <span className="run-step-name mono">{step.id}</span>
                              <span className="run-step-status">{step.status}</span>
                              {step.requires_approval && (
                                <span className="badge badge-warning">gate</span>
                              )}
                              {step.exit_code !== null &&
                                step.exit_code !== undefined && (
                                  <span
                                    className={`badge ${
                                      step.exit_code === 0
                                        ? 'badge-success'
                                        : 'badge-error'
                                    }`}
                                  >
                                    exit {step.exit_code}
                                  </span>
                                )}
                              {step.artifact_count > 0 && (
                                <span className="badge badge-info">
                                  {step.artifact_count}{' '}
                                  {step.artifact_count === 1
                                    ? 'artifact'
                                    : 'artifacts'}
                                </span>
                              )}
                              {hasDetail && (
                                <details className="run-step-detail">
                                  <summary>detail</summary>
                                  <div className="run-step-detail-body">
                                    {detail.commit && (
                                      <p className="text-muted text-sm">
                                        {detail.commit}
                                      </p>
                                    )}
                                    {detail.commit_url && (
                                      <a
                                        className="link run-commit"
                                        href={detail.commit_url}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        {detail.commit_url}
                                      </a>
                                    )}
                                    {detail.artifacts?.length > 0 && (
                                      <ul className="run-artifacts">
                                        {detail.artifacts.map((path) => (
                                          <li key={path}>{path}</li>
                                        ))}
                                      </ul>
                                    )}
                                    {detail.output && (
                                      <pre className="run-step-output">
                                        {detail.output}
                                      </pre>
                                    )}
                                  </div>
                                </details>
                              )}
                            </li>
                          );
                        })}
                      </ol>
                    </div>
                  )}

                  <div className="run-meta-grid">
                    <div className="run-meta-cell">
                      <div className="run-meta-key">thread_id</div>
                      <div className="run-meta-value">{runState.thread_id}</div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">workflow_id</div>
                      <div className="run-meta-value">{runState.workflow_id}</div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">graph_status</div>
                      <div
                        className={`run-meta-value ${
                          runState.graph_status ? '' : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.graph_status || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">approval_status</div>
                      <div
                        className={`run-meta-value ${
                          runState.approval_status
                            ? ''
                            : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.approval_status || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">reviewed_by</div>
                      <div
                        className={`run-meta-value ${
                          runState.approval_reviewer
                            ? ''
                            : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.approval_reviewer || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">review_note</div>
                      <div
                        className={`run-meta-value ${
                          runState.approval_comment
                            ? ''
                            : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.approval_comment || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">current_step</div>
                      <div
                        className={`run-meta-value ${
                          runState.current_step ? '' : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.current_step || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">created</div>
                      <div
                        className={`run-meta-value ${
                          runState.created_at ? '' : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.created_at || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">completed</div>
                      <div
                        className={`run-meta-value ${
                          runState.completed_at ? '' : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.completed_at || 'null'}
                      </div>
                    </div>
                    <div className="run-meta-cell">
                      <div className="run-meta-key">next</div>
                      <div
                        className={`run-meta-value ${
                          runState.next.length ? '' : 'run-meta-value-empty'
                        }`}
                      >
                        {runState.next.length ? runState.next.join(', ') : '[]'}
                      </div>
                    </div>
                  </div>

                  {runState.error && (
                    <div className="alert alert-error mt-4">{runState.error}</div>
                  )}

                  <details className="disclosure">
                    <summary>
                      Raw response
                      <span className="disclosure-count">
                        GET /workflows/{shortId(runState.thread_id)}/state
                      </span>
                    </summary>
                    <pre className="disclosure-body">
                      {JSON.stringify(runState, null, 2)}
                    </pre>
                  </details>

                  <div className="run-nodes">
                    {Object.entries(runState.nodes).map(([nodeId, node]) => {
                      const artifacts = node.artifacts ?? [];
                      const exit = node.exit_code;
                      const hasExit =
                        exit !== null && exit !== undefined;
                      return (
                        <div key={nodeId} className="run-node">
                          <div className="run-node-header">
                            <span
                              className={statusDot(nodeExitState(node))}
                            />
                            <span className="run-node-id">{nodeId}</span>
                            {hasExit && (
                              <span
                                className={`badge ${
                                  exit === 0 ? 'badge-success' : 'badge-error'
                                }`}
                              >
                                exit {exit}
                              </span>
                            )}
                            {artifacts.length > 0 && (
                              <span className="badge badge-info">
                                {artifacts.length}{' '}
                                {artifacts.length === 1
                                  ? 'artifact'
                                  : 'artifacts'}
                              </span>
                            )}
                            {node.workspace && (
                              <span className="badge badge-muted">
                                workspace
                              </span>
                            )}
                          </div>

                          {node.workspace && (
                            <p className="text-muted text-sm mt-2">
                              {node.workspace}
                            </p>
                          )}

                          {node.commit && (
                            <p className="text-muted text-sm mt-2 mono">
                              {node.commit}
                            </p>
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

                          {artifacts.length > 0 && (
                            <ul className="chip-list">
                              {artifacts.map((path) => (
                                <li
                                  key={path}
                                  className="chip"
                                  title={path}
                                >
                                  {path}
                                </li>
                              ))}
                            </ul>
                          )}

                          {node.output && (
                            <details className="disclosure">
                              <summary>
                                Output
                                <span className="disclosure-count">
                                  {node.output.length} chars
                                </span>
                              </summary>
                              <pre className="disclosure-body">
                                {node.output}
                              </pre>
                            </details>
                          )}

                          {!node.output &&
                            artifacts.length === 0 &&
                            !node.commit && (
                              <p className="text-muted text-sm mt-2">
                                No output recorded for this node.
                              </p>
                            )}
                        </div>
                      );
                    })}
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
