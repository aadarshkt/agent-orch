'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/', label: 'Dashboard', icon: '◆' },
  { href: '/agents', label: 'Agents', icon: '⬡' },
  { href: '/workflows/new', label: 'New Workflow', icon: '⊞' },
];

export default function Navbar() {
  const pathname = usePathname();

  return (
    <nav className="navbar">
      <div className="navbar-brand">
        <span className="navbar-logo">◎</span>
        <span className="navbar-title">Agent Orchestrator</span>
      </div>
      <div className="navbar-links">
        {navItems.map((item) => {
          const isActive =
            item.href === '/'
              ? pathname === '/'
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`navbar-link ${isActive ? 'navbar-link-active' : ''}`}
            >
              <span className="navbar-link-icon">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
