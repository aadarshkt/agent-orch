import Link from 'next/link'
import { API_BASE } from '@/lib/api'

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <>
      <header className="mnav">
        <div className="container mnav-inner">
          <Link href="/" className="mnav-brand">
            Agent Orchestrator
          </Link>
          <nav className="mnav-links">
            <a className="mnav-link hide-mobile" href={`${API_BASE}/docs`}>
              API
            </a>
            <Link className="mnav-link hide-mobile" href="/design">
              Design
            </Link>
            <Link className="btn btn-primary btn-sm" href="/dashboard">
              Open dashboard
            </Link>
          </nav>
        </div>
      </header>

      {children}

      <footer className="footer">
        <div className="container">
          <div className="footer-grid">
            <div>
              <div className="mnav-brand mb-4">Agent Orchestrator</div>
              <p className="footer-note">
                A local-first, config-driven engine for running heterogeneous
                agentic workflows from a single, shareable YAML file.
              </p>
            </div>
            <div className="footer-links">
              <div className="footer-col">
                <div className="footer-col-title">Product</div>
                <Link href="/dashboard">Dashboard</Link>
                <Link href="/agents">Agents</Link>
                <Link href="/workflows/new">Workflow builder</Link>
              </div>
              <div className="footer-col">
                <div className="footer-col-title">Reference</div>
                <a href={`${API_BASE}/docs`}>API documentation</a>
                <Link href="/design">Design system</Link>
              </div>
            </div>
          </div>
        </div>
      </footer>
    </>
  )
}
