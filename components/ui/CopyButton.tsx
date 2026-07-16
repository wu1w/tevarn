import React, { useState } from 'react';

interface CopyButtonProps {
  text: string;
  size?: 'sm' | 'md';
  className?: string;
}

export function CopyButton({ text, size = 'md', className = '' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      console.error('Copy failed:', e);
    }
  };

  const sizeClasses = size === 'sm'
    ? 'h-6 w-6 p-1'
    : 'h-8 w-8 p-1.5';

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`rounded-md border border-border-subtle bg-card-bg text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground ${sizeClasses} ${className}`}
      title="复制"
    >
      {copied ? (
        <svg className="h-full w-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <path d="M20 6L9 17l-5-5" />
        </svg>
      ) : (
        <svg className="h-full w-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
  );
}
