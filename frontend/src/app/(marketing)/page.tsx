import Link from 'next/link'
import { API_BASE } from '@/lib/api'
import CodeBlock from '../components/CodeBlock'
import WorkflowGraph from '../components/WorkflowGraph'

const WORKFLOW_YAML = `version: 1

runtimes:
  - name: claude-cli
    kind: cli
    image: ghcr.io/eco/claude-agent:latest
    command: ["claude", "-p"]
    resource_limits: { cpus: "1.0", memory: "512m" }
    timeout: 300

agents:
  coder:
    type: cli_agent
    runtime: claude-cli
    inputs:
      repos:
        - { repo: "https://gitlab.eco/eco/app.git", ref: "feature/ecocharge", path: "" }
      skills:
        - { repo: "https://gitlab.eco/eco/skills.git", ref: "main", path: "review.md" }
      prompt:
        { repo: "https://gitlab.eco/eco/skills.git", ref: "main", path: "prompt.md" }
      artifacts: ["**/*.md"]

  committer:
    type: git_commit
    inputs:
      source_nodes: [coding]

workflow:
  name: "EcoCharge CLI Pipeline"
  hitl_enabled: true
  nodes:
    - { id: coding, agent: coder, requires_approval: false }
    - { id: commit, agent: committer, requires_approval: true }
  edges:
    - { from: coding, to: commit, condition: success }
`

const CONCEPTS = [
  {
    index: '01',
    title: 'Executor type',
    body: 'A Python class that runs one kind of work. It declares its runtime kind, a JSON-Schema for its inputs, and an execute method. This is the only place code lives.',
  },
  {
    index: '02',
    title: 'Runtime',
    body: 'A named environment preset: the Docker image, command, endpoint, resource limits and timeout a type needs. Adding a runtime is a data change, not a code change.',
  },
  {
    index: '03',
    title: 'Agent',
    body: 'A concrete instance of a type: one runtime plus concrete input values. Its inputs are validated against the type schema before it can ever be referenced.',
  },
  {
    index: '04',
    title: 'Workflow',
    body: 'A graph of agent references — nodes and conditional edges. Nodes share one state bag, and each node’s artifacts are mounted for the next one automatically.',
  },
]

const FEATURES = [
  {
    index: '01',
    title: 'Self-contained YAML',
    body: 'One document declares runtimes, agents and the graph together. The same file is the share artifact and the executable definition — no hidden server config.',
  },
  {
    index: '02',
    title: 'Heterogeneous executors',
    body: 'A CLI agent in a container, a cloud model API, an MCP tool server, a plain script, a git commit. Different modes, one graph, one state model.',
  },
  {
    index: '03',
    title: 'Live event stream',
    body: 'Every run emits start, agent, review, resume, complete and error events over Server-Sent Events, so the UI reflects the graph as it executes.',
  },
  {
    index: '04',
    title: 'Human in the loop',
    body: 'Flag a node requires_approval and the graph compiles with an interrupt before it. The run pauses, then resumes only when a human approves.',
  },
  {
    index: '05',
    title: 'Verified handoffs',
    body: 'Upstream outputs are collected by glob and mounted for the next node under .agent/context/upstream, so dependencies are explicit, not implicit ordering.',
  },
  {
    index: '06',
    title: 'Local-first',
    body: 'FastAPI, LangGraph and PostgreSQL, all on your machine. No hosted control plane is required to import, run or inspect a workflow.',
  },
]

const CATALOG = [
  { name: 'cli_agent', kind: 'cli', tagClass: 'tag--cli', status: 'shipped', note: 'Runs a CLI agent in a Docker container from repo, skill, context and prompt sources.' },
  { name: 'git_commit', kind: 'git', tagClass: 'tag--git', status: 'shipped', note: 'Commits and pushes a prior node’s workspace.' },
  { name: 'script_runner', kind: 'script', tagClass: 'tag--script', status: 'shipped', note: 'Runs a Python script or inline code as a subprocess.' },
  { name: 'sleep', kind: 'none', tagClass: 'tag--none', status: 'shipped', note: 'A delay node, used to exercise the engine.' },
  { name: 'cloud_agent', kind: 'cloud', tagClass: 'tag--cloud', status: 'planned', note: 'Calls a cloud-hosted agent endpoint with a model and env.' },
  { name: 'mcp_agent', kind: 'mcp', tagClass: 'tag--mcp', status: 'planned', note: 'A cloud agent bound to MCP tool servers.' },
  { name: 'reviewer', kind: 'none', tagClass: 'tag--none', status: 'planned', note: 'Produces a review report and posts it to the source.' },
  { name: 'api_call', kind: 'none', tagClass: 'tag--none', status: 'planned', note: 'An arbitrary HTTP request as a graph node.' },
  { name: 'pipeline_trigger', kind: 'cloud', tagClass: 'tag--cloud', status: 'planned', note: 'Triggers a CI or build job — the documented extension pattern.' },
]

const STEPS = [
  {
    index: '01',
    title: 'Import a workflow',
    body: 'POST /workflows/import with a yaml_path or inline yaml_content. A new runtime kind registers itself, every agent input is schema-validated, and the graph is projected into Postgres.',
  },
  {
    index: '02',
    title: 'Execute the graph',
    body: 'POST /workflows/{id}/execute returns a thread_id and runs the compiled graph on a worker thread, off the async event loop, checkpointing each step.',
  },
  {
    index: '03',
    title: 'Stream and approve',
    body: 'Subscribe to /events/stream over SSE to watch each node in real time. When a node needs approval the run pauses until POST /workflows/{thread_id}/resume.',
  },
]

export default function LandingPage() {
  return (
    <main>
      {/* ───────────────────────── Hero ───────────────────────── */}
      <section className="hero">
        <div className="container hero-inner">
          <div className="hero-copy">
            <p className="eyebrow">Config-driven agent orchestration</p>
            <h1 className="display">
              Declare an agentic workflow in <em>one YAML file</em>. Run it
              locally.
            </h1>
            <p className="lede">
              Agent Orchestrator compiles a self-contained document — runtimes,
              agents and a graph — into an executable workflow. Mix CLI agents
              in Docker, cloud APIs, MCP tools and scripts, stream every step
              live, and pause for a human when it matters.
            </p>
            <div className="hero-actions">
              <Link className="btn btn-primary btn-lg" href="/dashboard">
                Open the dashboard
              </Link>
              <a className="btn btn-secondary btn-lg" href={`${API_BASE}/docs`}>
                Read the API docs
              </a>
            </div>
            <div className="hero-meta">
              <div className="stat">
                <div className="stat-value">1</div>
                <div className="stat-label">file per workflow</div>
              </div>
              <div className="stat">
                <div className="stat-value">9</div>
                <div className="stat-label">executor types</div>
              </div>
              <div className="stat">
                <div className="stat-value">SSE</div>
                <div className="stat-label">live event stream</div>
              </div>
            </div>
          </div>

          <WorkflowGraph />
        </div>
      </section>

      {/* ─────────────────────── The rule ─────────────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">The one rule</p>
            <h2 className="section-title">A type is code. A node is data.</h2>
            <p className="section-lede">
              Adding an agent is a data change. Adding a capability is a code
              change. The boundary between them is the executor class, and it is
              the only line worth defending.
            </p>
          </div>
          <div className="rule">
            <div className="rule-cell">
              <div className="rule-term">code</div>
              <p className="rule-body">
                The executor type defines the shape of an agent — its runtime
                kind, its input schema, and how it executes. New field, new
                behaviour: one class, registered once.
              </p>
            </div>
            <div className="rule-cell">
              <div className="rule-term">data</div>
              <p className="rule-body">
                An agent instance supplies values only, and a workflow wires
                instances into a graph. No code, no seed rows, no server
                restart to add one.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ─────────────────────── Concepts ─────────────────────── */}
      <section className="section">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Mental model</p>
            <h2 className="section-title">Four nouns, one direction of flow</h2>
            <p className="section-lede">
              Everything in the system is one of these. Learn them once and the
              rest of the config reads itself.
            </p>
          </div>
          <div className="feature-grid">
            {CONCEPTS.map((c) => (
              <article className="feature" key={c.title}>
                <span className="feature-index">{c.index}</span>
                <h3 className="feature-title">{c.title}</h3>
                <p className="feature-body">{c.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ─────────────────────── Features ─────────────────────── */}
      <section className="section">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Capabilities</p>
            <h2 className="section-title">Built for heterogeneous execution</h2>
            <p className="section-lede">
              The engine assumes your steps are not all the same shape — and
              gives them one state, one event stream, and one approval model.
            </p>
          </div>
          <div className="feature-grid">
            {FEATURES.map((f) => (
              <article className="feature" key={f.title}>
                <span className="feature-index">{f.index}</span>
                <h3 className="feature-title">{f.title}</h3>
                <p className="feature-body">{f.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ─────────────────────── Code sample ─────────────────────── */}
      <section className="section">
        <div className="container">
          <div className="hero-inner">
            <div className="hero-copy">
              <p className="eyebrow">One file</p>
              <h2 className="section-title">Shareable by construction</h2>
              <p className="section-lede">
                The document you write is the document you share and the
                document you run. Import it by path or paste it inline; the
                same validation runs either way.
              </p>
              <ul className="rule-body" style={{ marginTop: 'var(--space-6)', paddingLeft: '1.1rem' }}>
                <li>Runtimes referenced by name are registered on import.</li>
                <li>Every agent input is validated against its type schema.</li>
                <li>Node and edge references are checked before the graph is stored.</li>
                <li>The UI renders forms directly from that schema.</li>
              </ul>
            </div>
            <div>
              <CodeBlock
                filename="ecocharge_cli_workflow.yaml"
                lang="yaml"
                code={WORKFLOW_YAML}
              />
            </div>
          </div>
        </div>
      </section>

      {/* ─────────────────── Executor catalog ─────────────────── */}
      <section className="section">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Executor catalog</p>
            <h2 className="section-title">What runs today</h2>
            <p className="section-lede">
              Executors register themselves on startup. Shipped types are live
              in the engine; planned types mark the documented extension path.
            </p>
          </div>
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">Registered executor types</span>
              <span className="badge badge-muted">4 shipped · 5 planned</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Executor</th>
                    <th>Kind</th>
                    <th>Status</th>
                    <th>Responsibility</th>
                  </tr>
                </thead>
                <tbody>
                  {CATALOG.map((row) => (
                    <tr key={row.name}>
                      <td className="cell-mono">{row.name}</td>
                      <td>
                        <span className={`tag ${row.tagClass}`}>{row.kind}</span>
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            row.status === 'shipped'
                              ? 'badge-success'
                              : 'badge-muted'
                          }`}
                        >
                          {row.status}
                        </span>
                      </td>
                      <td>{row.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* ─────────────────────── How it works ─────────────────────── */}
      <section className="section">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Lifecycle</p>
            <h2 className="section-title">From file to finished run</h2>
          </div>
          <div className="step-list">
            {STEPS.map((s) => (
              <div className="step" key={s.title}>
                <span className="step-index">{s.index}</span>
                <h3 className="step-title">{s.title}</h3>
                <p className="step-body">{s.body}</p>
              </div>
            ))}
          </div>

          <div className="stack-strip" style={{ marginTop: 'var(--space-12)' }}>
            <span className="stack-item">FastAPI</span>
            <span className="stack-item">LangGraph</span>
            <span className="stack-item">PostgreSQL</span>
            <span className="stack-item">Docker</span>
            <span className="stack-item">Server-Sent Events</span>
            <span className="stack-item">Next.js</span>
          </div>
        </div>
      </section>

      {/* ─────────────────────── CTA ─────────────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="cta-band">
            <div>
              <h2 className="cta-title">Run your first workflow</h2>
              <p className="cta-body">
                Point the importer at backend/config/ecocharge_cli_workflow.yaml
                and execute it from the dashboard.
              </p>
            </div>
            <div className="row gap-3 flex-wrap">
              <Link className="btn btn-primary" href="/dashboard">
                Open the dashboard
              </Link>
              <a className="btn btn-secondary" href={`${API_BASE}/docs`}>
                Read the API docs
              </a>
            </div>
          </div>
        </div>
      </section>
    </main>
  )
}
