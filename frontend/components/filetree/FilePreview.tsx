'use client';

import React, { useEffect, useState } from 'react';
import { FileContent } from '@/types';
import { readFile } from '@/lib/api';
import { useT } from '@/stores/localeStore';

interface FilePreviewProps {
  path: string;
  onClose?: () => void;
}

export function FilePreview({ path, onClose }: FilePreviewProps) {
  const t = useT();
  const [file, setFile] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    readFile(path)
      .then(setFile)
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || t('modelPicker.loadError'));
      })
      .finally(() => setLoading(false));
  }, [path]);

  return (
    <div className="flex flex-col h-full rounded-xl border border-border-subtle bg-card-bg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <svg className="h-4 w-4 flex-shrink-0 text-foreground-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
          </svg>
          <span className="text-sm font-medium text-foreground truncate">{path}</span>
        </div>
        <div className="flex items-center gap-2">
          {file && (
            <span className="text-[10px] text-foreground-dim font-mono">
              {file.language} · {file.size > 1024 ? `${(file.size / 1024).toFixed(1)}k` : `${file.size}B`}
            </span>
          )}
          {onClose && (
            <button
              onClick={onClose}
              className="rounded p-1 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground transition-colors"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-500/30 border-t-violet-500" />
              <span className="text-xs text-foreground-dim">{t('contextDash.loading')}</span>
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <svg className="mx-auto h-8 w-8 text-error-text/60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <p className="mt-2 text-xs text-error-text">{error}</p>
            </div>
          </div>
        ) : file ? (
          <pre className="p-4 text-xs leading-relaxed text-foreground-muted font-mono overflow-x-auto whitespace-pre-wrap break-all">
            {file.content}
          </pre>
        ) : null}
      </div>
    </div>
  );
}
