import CodeBlock from '../../components/CodeBlock'

function Swatch({ token }: { token: string }) {
  return (
    <div>
      <div
        style={{
          height: '56px',
          borderRadius: 'var(--radius)',
          border: '1px solid var(--line)',
          background: `var(${token})`,
        }}
      />
      <div className="mono text-2xs subtle mt-2">{token}</div>
    </div>
  )
}

function Row({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div style={{ marginBottom: 'var(--space-8)' }}>
      <div className="eyebrow mb-4">{label}</div>
      <div className="row flex-wrap items-center gap-3">{children}</div>
    </div>
  )
}

export default function DesignPage() {
  return (
    <main>
      <section className="section-tight">
        <div className="container">
          <p className="eyebrow">Design system</p>
          <h1 className="page-title" style={{ fontSize: 'var(--text-3xl)' }}>
            Tokens, primitives, and motion
          </h1>
          <p className="section-lede" style={{ maxWidth: '640px' }}>
            Every visual decision in the product resolves to a token in
            <span className="code"> src/styles/tokens.css</span>. Components
            compose those tokens; nothing invents its own value. The system is
            icon-free by design — meaning is carried by type, color and
            structure.
          </p>
        </div>
      </section>

      {/* ───────────────── Colour ───────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Foundations</p>
            <h2 className="section-title">Colour</h2>
          </div>

          <div className="eyebrow mb-4">Surfaces</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-8)',
            }}
          >
            {['--canvas', '--surface', '--surface-2', '--surface-3'].map((t) => (
              <Swatch key={t} token={t} />
            ))}
          </div>

          <div className="eyebrow mb-4">Text</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-8)',
            }}
          >
            {['--text', '--text-2', '--text-3'].map((t) => (
              <Swatch key={t} token={t} />
            ))}
          </div>

          <div className="eyebrow mb-4">Accent</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-8)',
            }}
          >
            {['--accent', '--accent-hover', '--accent-press', '--accent-soft'].map(
              (t) => (
                <Swatch key={t} token={t} />
              )
            )}
          </div>

          <div className="eyebrow mb-4">Semantic</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-8)',
            }}
          >
            {['--success', '--warning', '--danger', '--info'].map((t) => (
              <Swatch key={t} token={t} />
            ))}
          </div>

          <div className="eyebrow mb-4">Execution kinds (domain)</div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
              gap: 'var(--space-4)',
            }}
          >
            {[
              '--kind-cli',
              '--kind-cloud',
              '--kind-mcp',
              '--kind-script',
              '--kind-git',
              '--kind-none',
            ].map((t) => (
              <Swatch key={t} token={t} />
            ))}
          </div>
        </div>
      </section>

      {/* ───────────────── Typography ───────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Foundations</p>
            <h2 className="section-title">Typography</h2>
            <p className="section-lede">
              Geist for interface text, Geist Mono for labels, code and
              identifiers. Headlines tighten; mono labels widen.
            </p>
          </div>

          <div className="display" style={{ fontSize: 'var(--text-4xl)' }}>
            Display / {`--text-4xl`}
          </div>
          <h2 className="section-title">Section title / {`--text-2xl`}</h2>
          <h3 className="feature-title">Feature title / {`--text-md`}</h3>
          <p className="lede" style={{ marginBottom: 'var(--space-4)' }}>
            Lede — a longer supporting sentence sets the tone at
            {` --text-lg`} with relaxed leading.
          </p>
          <p className="muted" style={{ marginBottom: 'var(--space-4)' }}>
            Body text sits at {`--text-base`} in the secondary text colour for
            comfortable long-form reading.
          </p>
          <p className="eyebrow">eyebrow / mono / uppercase / tracked</p>
        </div>
      </section>

      {/* ───────────────── Space, radius, elevation ───────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Foundations</p>
            <h2 className="section-title">Space, radius, elevation</h2>
          </div>

          <Row label="Spacing scale (4px base)">
            {[1, 2, 3, 4, 6, 8, 12, 16, 24].map((n) => (
              <div key={n} style={{ textAlign: 'center' }}>
                <div
                  style={{
                    width: `var(--space-${n})`,
                    height: '32px',
                    background: 'var(--accent-soft)',
                    border: '1px solid var(--line-accent)',
                    borderRadius: 'var(--radius-xs)',
                  }}
                />
                <div className="mono text-2xs subtle mt-2">{n}</div>
              </div>
            ))}
          </Row>

          <Row label="Radius">
            {['xs', 'sm', '', 'md', 'lg', 'xl', '2xl'].map((n) => (
              <div
                key={n || 'base'}
                style={{
                  width: '64px',
                  height: '48px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: n ? `var(--radius-${n})` : 'var(--radius)',
                  border: '1px solid var(--line-strong)',
                  background: 'var(--surface-2)',
                  fontSize: 'var(--text-2xs)',
                }}
                className="mono subtle"
              >
                {n || 'base'}
              </div>
            ))}
          </Row>

          <Row label="Elevation">
            {['sm', 'md', 'lg'].map((n) => (
              <div
                key={n}
                style={{
                  width: '120px',
                  height: '72px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 'var(--radius-lg)',
                  background: 'var(--surface)',
                  boxShadow: `var(--shadow-${n})`,
                }}
                className="mono text-2xs subtle"
              >
                shadow-{n}
              </div>
            ))}
          </Row>
        </div>
      </section>

      {/* ───────────────── Components ───────────────── */}
      <section className="section-tight">
        <div className="container">
          <div className="section-header">
            <p className="eyebrow">Primitives</p>
            <h2 className="section-title">Components</h2>
          </div>

          <Row label="Buttons">
            <button className="btn btn-primary">Primary</button>
            <button className="btn btn-secondary">Secondary</button>
            <button className="btn btn-ghost">Ghost</button>
            <button className="btn btn-outline">Outline</button>
            <button className="btn btn-success">Success</button>
            <button className="btn btn-danger">Danger</button>
            <button className="btn btn-primary" disabled>
              Disabled
            </button>
          </Row>

          <Row label="Button sizes & icon buttons">
            <button className="btn btn-primary btn-lg">Large</button>
            <button className="btn btn-primary">Medium</button>
            <button className="btn btn-primary btn-sm">Small</button>
            <button className="btn-icon">Icon</button>
            <button className="btn-icon btn-icon-danger">Danger</button>
          </Row>

          <Row label="Badges">
            <span className="badge badge-accent">accent</span>
            <span className="badge badge-info">info</span>
            <span className="badge badge-success">success</span>
            <span className="badge badge-warning">warning</span>
            <span className="badge badge-error">error</span>
            <span className="badge badge-muted">muted</span>
          </Row>

          <Row label="Kind tags">
            <span className="tag tag--cli">cli</span>
            <span className="tag tag--cloud">cloud</span>
            <span className="tag tag--mcp">mcp</span>
            <span className="tag tag--script">script</span>
            <span className="tag tag--git">git</span>
            <span className="tag tag--none">none</span>
            <span className="tag tag--accent">hitl</span>
          </Row>

          <Row label="Status dots">
            <span className="row items-center gap-2">
              <span className="status-dot status-dot-success" />
              <span className="text-sm muted">success</span>
            </span>
            <span className="row items-center gap-2">
              <span className="status-dot status-dot-warning" />
              <span className="text-sm muted">warning</span>
            </span>
            <span className="row items-center gap-2">
              <span className="status-dot status-dot-danger" />
              <span className="text-sm muted">danger</span>
            </span>
            <span className="row items-center gap-2">
              <span className="status-dot status-dot-info" />
              <span className="text-sm muted">info</span>
            </span>
            <span className="row items-center gap-2">
              <span className="status-dot status-dot-idle" />
              <span className="text-sm muted">idle</span>
            </span>
          </Row>

          <Row label="Tabs">
            <div className="tabs">
              <button className="tab tab-active">Overview</button>
              <button className="tab">Runs</button>
              <button className="tab">Config</button>
            </div>
          </Row>

          <Row label="Param chips & kbd">
            <span className="param-chip">repos: 2 items</span>
            <span className="param-chip">artifacts: [&quot;**/*.md&quot;]</span>
            <span className="param-chip param-chip-more">+3 more</span>
            <span className="kbd">Cmd</span>
            <span className="kbd">K</span>
          </Row>

          <div style={{ marginBottom: 'var(--space-8)' }}>
            <div className="eyebrow mb-4">Alerts</div>
            <div className="alert alert-info">Informational callout.</div>
            <div className="alert alert-success">Operation completed.</div>
            <div className="alert alert-warning">Heads up — degraded mode.</div>
            <div className="alert alert-error">Something failed.</div>
          </div>

          <div style={{ marginBottom: 'var(--space-8)' }}>
            <div className="eyebrow mb-4">Form controls</div>
            <div className="card" style={{ maxWidth: '480px' }}>
              <div className="form-field">
                <label className="form-label">
                  Agent name <span className="form-required">*</span>
                </label>
                <p className="form-description">
                  Validated against the executor&apos;s input schema.
                </p>
                <input className="form-input" placeholder="code-agent" />
              </div>
              <div className="form-field">
                <label className="form-label">Runtime</label>
                <select className="form-input form-select" defaultValue="">
                  <option value="">Select runtime...</option>
                  <option value="claude-cli">claude-cli</option>
                </select>
              </div>
              <label className="form-toggle-wrapper">
                <input type="checkbox" className="form-toggle" defaultChecked />
                <span className="form-toggle-label">
                  Requires approval before continuing
                </span>
              </label>
            </div>
          </div>

          <div style={{ marginBottom: 'var(--space-8)' }}>
            <div className="eyebrow mb-4">Panel & table</div>
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">Execution history</span>
                <span className="badge badge-muted">2 runs</span>
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Thread</th>
                    <th>Status</th>
                    <th>Step</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="cell-mono">7f3a91c2</td>
                    <td>
                      <span className="badge badge-success">completed</span>
                    </td>
                    <td>commit</td>
                  </tr>
                  <tr>
                    <td className="cell-mono">b104e77d</td>
                    <td>
                      <span className="badge badge-warning">paused</span>
                    </td>
                    <td>review-gate</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ marginBottom: 'var(--space-8)' }}>
            <div className="eyebrow mb-4">Code</div>
            <CodeBlock
              filename="events.stream"
              lang="text"
              code={`event: agent
data: {"node": "code-agent", "status": "completed", "artifacts": ["ai_review/report.md"]}

event: review
data: {"node": "review-gate", "requires_approval": true}`}
            />
          </div>

          <div>
            <div className="eyebrow mb-4">Empty state & loading</div>
            <div className="empty-state">
              <h3>Nothing here yet</h3>
              <p>Create something to populate this view.</p>
            </div>
            <div className="page-loading" style={{ minHeight: 'auto' }}>
              <div className="spinner" />
              <p className="muted">Loading...</p>
            </div>
          </div>
        </div>
      </section>
    </main>
  )
}
