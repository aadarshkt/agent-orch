'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/agents', label: 'Agents' },
  { href: '/workflows/new', label: 'New Workflow' },
];

export default function Navbar() {
  const pathname = usePathname();

  return (
    <nav className="navbar">
      <div className="navbar-left">
        <Link href="/" className="btn btn-ghost btn-sm">
          Back to home
        </Link>
        <Link href="/dashboard" className="navbar-brand">
          <span className="navbar-title">Agent Orchestrator</span>
        </Link>
      </div>
      <div className="navbar-links">
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`navbar-link ${isActive ? 'navbar-link-active' : ''}`}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
