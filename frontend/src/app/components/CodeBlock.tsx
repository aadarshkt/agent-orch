'use client';

import { useState } from 'react';

export default function CodeBlock({
  filename,
  lang,
  code,
}: {
  filename: string;
  lang?: string;
  code: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-filename">
          {filename}
          {lang && <span className="code-block-lang">{lang}</span>}
        </span>
        <button type="button" className="btn-icon" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="code-block-body">
        <code>{code}</code>
      </pre>
    </div>
  );
}
