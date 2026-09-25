'use client';

import { useEffect, useState, type CSSProperties } from 'react';

/**
 * A minimalist, self-running illustration of a heterogeneous workflow drawn as
 * a tree: a root import, a fork into parallel branches running different
 * execution modes, a merge into a human approval gate, then a git commit.
 * Data pulses travel the connectors as each level executes. Pure CSS motion on
 * top of the design tokens.
 */

interface Step {
  id: number;
  name: string;
  sub: string;
  tag: string;
  tagClass: string;
  color: string;
}

const ROOT: Step = {
  id: 1,
  name: 'import workflow',
  sub: 'POST /workflows/import',
  tag: 'yaml',
  tagClass: 'tag--accent',
  color: 'var(--accent)',
};

const BRANCHES: Step[] = [
  {
    id: 2,
    name: 'code-agent',
    sub: 'cli_agent · docker',
    tag: 'cli',
    tagClass: 'tag--cli',
    color: 'var(--kind-cli)',
  },
  {
    id: 3,
    name: 'audit-agent',
    sub: 'cloud_agent',
    tag: 'cloud',
    tagClass: 'tag--cloud',
    color: 'var(--kind-cloud)',
  },
  {
    id: 4,
    name: 'docs-agent',
    sub: 'mcp_agent',
    tag: 'mcp',
    tagClass: 'tag--mcp',
    color: 'var(--kind-mcp)',
  },
];

const GATE: Step = {
  id: 5,
  name: 'review-gate',
  sub: 'requires_approval',
  tag: 'hitl',
  tagClass: 'tag--accent',
  color: 'var(--accent)',
};

const SINK: Step = {
  id: 6,
  name: 'git_commit',
  sub: 'feature/ecocharge',
  tag: 'git',
  tagClass: 'tag--git',
  color: 'var(--kind-git)',
};

/** Levels of the tree, in execution order. */
const PHASES = 4;
const INTERVAL = 2000;

const CAPTIONS = [
  'importing',
  '3 branches in parallel',
  'awaiting approval',
  'committing',
];

function stateFor(phase: number, level: number) {
  if (phase === level) return 'active';
  return phase > level ? 'done' : 'idle';
}

function Node({ step, state }: { step: Step; state: string }) {
  return (
    <div
      className="tree-node"
      data-state={state}
      style={{ '--node-color': step.color } as CSSProperties}
    >
      <span className="tree-node-marker">{step.id}</span>
      <span className="tree-node-name">
        {step.name}
        <span className="tree-node-sub">{step.sub}</span>
      </span>
      <span className={`tag ${step.tagClass}`}>{step.tag}</span>
    </div>
  );
}

export default function WorkflowGraph() {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), INTERVAL);
    return () => clearInterval(id);
  }, []);

  const phase = tick % PHASES;
  const cycle = Math.floor(tick / PHASES);
  const paused = phase === 2;

  const tint = (color: string) => ({ '--tint': color }) as CSSProperties;

  return (
    <div className="graph" aria-hidden="true">
      <div className="graph-head">
        <span className="graph-head-title">
          workflow · ecocharge-cli-pipeline
        </span>
        <span className={`badge ${paused ? 'badge-warning' : 'badge-info'}`}>
          {paused ? 'paused' : 'running'}
        </span>
      </div>

      <div className="tree-body">
        <Node step={ROOT} state={stateFor(phase, 0)} />
        <span
          className="tree-stem"
          data-state={phase === 0 ? 'flowing' : 'done'}
        />

        {/* Fork: one root into three parallel modes. */}
        <div
          className="tree-fork"
          data-state={phase === 1 ? 'flowing' : phase > 1 ? 'done' : 'idle'}
        >
          {BRANCHES.map((b) => (
            <span key={b.name} style={tint(b.color)} />
          ))}
        </div>

        <div className="tree-branches">
          {BRANCHES.map((b) => (
            <Node key={b.name} step={b} state={stateFor(phase, 1)} />
          ))}
        </div>

        {/* Merge: three branches back into one chain. */}
        <div
          className="tree-merge"
          data-state={phase === 2 ? 'flowing' : phase > 2 ? 'done' : 'idle'}
        >
          {BRANCHES.map((b) => (
            <span key={b.name} style={tint(b.color)} />
          ))}
          <span className="merge-stem" />
        </div>

        <Node step={GATE} state={stateFor(phase, 2)} />
        <span
          className="tree-stem"
          data-state={phase === 3 ? 'flowing' : 'idle'}
        />
        <Node step={SINK} state={stateFor(phase, 3)} />
      </div>

      <div className="graph-foot">
        <span className="graph-progress" key={cycle} />
        <span className="graph-caption">{CAPTIONS[phase]}</span>
      </div>
    </div>
  );
}
