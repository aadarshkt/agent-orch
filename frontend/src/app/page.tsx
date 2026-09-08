'use client';

import { useState, useEffect } from 'react';

export default function Dashboard() {
  const [capabilities, setCapabilities] = useState<any>(null);
  const [executing, setExecuting] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [events, setEvents] = useState<any[]>([]);

  useEffect(() => {
    // Fetch capabilities on load
    fetch('http://localhost:8000/capabilities')
      .then(res => res.json())
      .then(data => setCapabilities(data))
      .catch(err => console.error("Error fetching capabilities", err));
  }, []);

  const launchWorkflow = () => {
    setExecuting(true);
    fetch('http://localhost:8000/workflows/config_123/execute', { method: 'POST' })
      .then(res => res.json())
      .then(data => {
        setThreadId(data.thread_id);
        // Start listening to SSE for this thread
        const eventSource = new EventSource(`http://localhost:8000/events/stream?thread_id=${data.thread_id}`);
        eventSource.onmessage = (e) => {
          const parsed = JSON.parse(e.data);
          setEvents(prev => [...prev, parsed]);
        };
      })
      .catch(err => {
        console.error(err);
        setExecuting(false);
      });
  };

  const resumeWorkflow = () => {
    if (!threadId) return;
    setExecuting(true);
    fetch(`http://localhost:8000/workflows/${threadId}/resume`, { method: 'POST' })
      .then(res => res.json())
      .then(data => {
        console.log("Resumed workflow", data);
        setExecuting(false);
      })
      .catch(err => {
        console.error("Failed to resume workflow", err);
        setExecuting(false);
      });
  };

  const isPendingReview = events.length > 0 && events[events.length - 1].event === 'review';

  return (
    <main className="container">
      <h1 className="heading" style={{ fontSize: '2.5rem', marginBottom: '2rem' }}>Agent Orchestrator</h1>
      
      <div className="flex gap-4" style={{ marginBottom: '2rem' }}>
        <div className="card glass flex-col justify-between" style={{ flex: 1 }}>
          <h2 className="heading">Capabilities</h2>
          {capabilities ? (
            <ul style={{ paddingLeft: '1.2rem', color: 'var(--text-muted)' }}>
              <li>Agents: {capabilities.agents.join(', ')}</li>
              <li>Pipelines: {capabilities.pipelines.join(', ')}</li>
              <li>MCP Tools: {capabilities.mcp_tools.length}</li>
            </ul>
          ) : (
            <p className="text-muted">Loading capabilities...</p>
          )}
        </div>
        
        <div className="card glass flex-col items-center justify-between" style={{ flex: 1, textAlign: 'center' }}>
          <h2 className="heading">Workflow Launcher</h2>
          <p className="text-muted text-sm mt-4" style={{ marginBottom: '1.5rem' }}>
            Execute the master configuration workflow and monitor live progression.
          </p>
          <button className="btn" onClick={launchWorkflow} disabled={executing}>
            {executing ? 'Executing...' : 'Launch Master Workflow'}
          </button>
        </div>
      </div>

      {threadId && (
        <div className="card glass">
          <div className="flex items-center justify-between">
            <h2 className="heading">Execution Monitor <span className="text-sm text-muted">({threadId})</span></h2>
            {isPendingReview && (
              <button 
                className="btn" 
                onClick={resumeWorkflow} 
                disabled={executing}
                style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
              >
                {executing ? 'Resuming...' : 'Approve & Resume'}
              </button>
            )}
          </div>
          <div style={{ marginTop: '1rem', background: '#000', padding: '1rem', borderRadius: '8px', minHeight: '200px', fontFamily: 'monospace' }}>
            {events.length === 0 ? (
              <span className="text-muted">Waiting for events...</span>
            ) : (
              events.map((ev, idx) => (
                <div key={idx} style={{ color: ev.event === 'error' ? '#ef4444' : ev.event === 'review' ? '#eab308' : '#10b981', marginBottom: '0.5rem' }}>
                  [{new Date().toLocaleTimeString()}] {ev.event}: {ev.data}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </main>
  );
}
