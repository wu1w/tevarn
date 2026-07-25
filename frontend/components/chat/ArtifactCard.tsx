'use client';

import React, { useState } from 'react';
import type { ChatArtifact } from '@/lib/artifacts';
import { artifactPreviewable } from '@/lib/artifacts';
import { useT } from '@/stores/localeStore';

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const auth = localStorage.getItem('takton-auth');
    return auth ? (JSON.parse(auth)?.state?.token ?? null) : null;
  } catch {
    return null;
  }
}

function apiBase(): string {
  if (typeof window !== 'undefined' && (window as unknown as { electron?: unknown }).electron) {
    return 'http://127.0.0.1:8000/api';
  }
  return '/api';
}

/** 与 FileDownloadLink 一致：相对 workspace 根 */
function toRelPath(href: string): string {
  let p = href.trim();
  p = p.replace(/^sandbox:\/*/i, '');
  p = p.replace(/^\/+/, '');
  p = p.replace(/^(\.\/)?workspace\//i, '');
  p = p.replace(/^\.\//, '');
  // uploads 走静态，download API 可能不在 sandbox
  return p;
}

async function downloadArtifact(path: string): Promise<void> {
  const rel = toRelPath(path);
  // /uploads/ 静态资源
  if (rel.startsWith('uploads/') || path.includes('/uploads/')) {
    const name = rel.split('/').pop() || 'download';
    const url = path.startsWith('http') ? path : `/${rel.replace(/^\/+/, '')}`;
    const a = document.createElement('a');
    a.href = url.startsWith('/') ? url : `/${url}`;
    a.download = name;
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }
  const token = getToken();
  const res = await fetch(`${apiBase()}/files/download?path=${encodeURIComponent(rel)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const filename = rel.split('/').pop() || 'download';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function KindIcon({ kind }: { kind?: ChatArtifact['kind'] }) {
  const common = 'h-5 w-5 flex-shrink-0';
  if (kind === 'image') {
    return (
      <svg className={`${common} text-brand-cyan`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3.75 21h16.5A2.25 2.25 0 0022.5 18.75V5.25A2.25 2.25 0 0020.25 3H3.75A2.25 2.25 0 001.5 5.25v13.5A2.25 2.25 0 003.75 21z" />
      </svg>
    );
  }
  if (kind === 'table') {
    return (
      <svg className={`${common} text-emerald-500`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 01-1.125-1.125M3.375 19.5h7.5c.621 0 1.125-.504 1.125-1.125m-9.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 0H9.75m11.25 0h.008v.008h-.008v-.008zm0 0H15m-6.75 0H9.75m0 0H5.625m0 0h-.008v.008h.008V15z" />
      </svg>
    );
  }
  return (
    <svg className={`${common} text-brand-purple`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  );
}

export interface ArtifactCardProps {
  artifacts: ChatArtifact[];
  onPreview?: (art: ChatArtifact) => void;
}

export function ArtifactCard({ artifacts, onPreview }: ArtifactCardProps) {
  const t = useT();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!artifacts.length) return null;

  const handleDownload = async (art: ChatArtifact) => {
    if (busy) return;
    setBusy(art.path);
    setErr(null);
    try {
      await downloadArtifact(art.path);
    } catch {
      setErr(art.path);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-3 space-y-2" data-testid="chat-artifacts">
      {artifacts.length > 1 && (
        <div className="flex items-center gap-2 rounded-xl border border-border-subtle bg-elevated-bg/50 px-3 py-2">
          <svg className="h-4 w-4 text-foreground-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 9.75h16.5m-16.5 4.5h16.5m-16.5 4.5h16.5M3.75 5.25h16.5" />
          </svg>
          <span className="text-xs font-medium text-foreground-muted">
            {t('chat.artifactsAll').replace('{n}', String(artifacts.length))}
          </span>
          {onPreview && (
            <button
              type="button"
              onClick={() => onPreview(artifacts[0])}
              className="ml-auto rounded-lg border border-border-default px-2 py-0.5 text-[11px] text-foreground-muted hover:bg-card-bg-hover hover:text-foreground"
            >
              {t('chat.artifactPreview')}
            </button>
          )}
        </div>
      )}
      {artifacts.map((art) => {
        const canPreview = artifactPreviewable(art.kind) && !!onPreview;
        return (
          <div
            key={art.path}
            className="flex items-center gap-3 rounded-xl border border-border-subtle bg-card-bg/80 px-3 py-2.5 shadow-sm transition-colors hover:border-brand-purple/25"
          >
            <KindIcon kind={art.kind} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground" title={art.path}>
                {art.name}
              </div>
              <div className="truncate text-[11px] text-foreground-dim">
                {err === art.path ? t('chat.artifactDownloadFail') : t('chat.artifactHint')}
              </div>
            </div>
            <div className="flex flex-shrink-0 items-center gap-1.5">
              {canPreview && (
                <button
                  type="button"
                  onClick={() => onPreview?.(art)}
                  className="rounded-lg border border-brand-purple/25 bg-brand-purple/10 px-2.5 py-1 text-[11px] font-medium text-brand-purple hover:bg-brand-purple/15"
                >
                  {t('chat.artifactPreview')}
                </button>
              )}
              <button
                type="button"
                onClick={() => void handleDownload(art)}
                disabled={busy === art.path}
                className="rounded-lg border border-border-default px-2.5 py-1 text-[11px] font-medium text-foreground-muted hover:bg-card-bg-hover hover:text-foreground disabled:opacity-50"
              >
                {busy === art.path ? '…' : t('chat.artifactDownload')}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
