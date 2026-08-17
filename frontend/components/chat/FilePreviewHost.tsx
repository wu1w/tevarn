'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatArtifact } from '@/lib/artifacts';
import { isInternalRuntimePath, isScratchOrProcessFile } from '@/lib/artifacts';
import { getPersistedAuthToken, readFile } from '@/lib/api';
import {
  loadDocxHtml,
  loadPptxSlides,
  loadXlsxTables,
  parseCsvText,
  sanitizeHtmlForPreview,
  type SheetTable,
} from '@/lib/filePreviewLoaders';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';
import { ColResizer } from '@/components/ui/ColResizer';
import { useColResize } from '@/hooks/useColResize';
import { maxRightPanelWidth } from '@/lib/colResize';

function getToken(): string | null {
  return getPersistedAuthToken();
}

function apiBase(): string {
  // Electron 与浏览器同源 /api（主进程反代）；勿硬编码 :8000
  return '/api';
}

export function toRelPath(href: string): string {
  let p = href.trim().replace(/\\/g, '/');
  p = p.replace(/^sandbox:\/*/i, '');
  p = p.replace(/^file:\/\//i, '');
  // Windows abs under …/workspace/… → relative to that workspace root
  const wsIdx = p.toLowerCase().lastIndexOf('/workspace/');
  if (wsIdx >= 0 && /^[a-zA-Z]:\//.test(p)) {
    p = p.slice(wsIdx + '/workspace/'.length);
  }
  // UNC or posix abs with /workspace/
  const wsIdx2 = p.toLowerCase().indexOf('/workspace/');
  if (wsIdx2 >= 0 && p.startsWith('/')) {
    p = p.slice(wsIdx2 + '/workspace/'.length);
  }
  p = p.replace(/^\/+/, '');
  p = p.replace(/^(\.\/)?workspace\//i, '');
  p = p.replace(/^\.\//, '');
  return p;
}

async function fetchBlob(path: string): Promise<Blob> {
  const rel = toRelPath(path);
  if (rel.startsWith('uploads/') || path.includes('/uploads/')) {
    const url = path.startsWith('http') || path.startsWith('/') ? path : `/${rel}`;
    const res = await fetch(url.startsWith('/') || url.startsWith('http') ? url : `/${url}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }
  const token = getToken();
  const res = await fetch(`${apiBase()}/files/download?path=${encodeURIComponent(rel)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.blob();
}

async function resolveAbsPath(rel: string): Promise<{ abs_path: string; exists: boolean } | null> {
  const token = getToken();
  const res = await fetch(`${apiBase()}/files/resolve?path=${encodeURIComponent(rel)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) return null;
  return res.json();
}

async function openViaBackend(rel: string): Promise<boolean> {
  const token = getToken();
  const res = await fetch(`${apiBase()}/files/open?path=${encodeURIComponent(rel)}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return res.ok;
}

type PreviewState =
  | { type: 'empty' }
  | { type: 'image'; url: string }
  | { type: 'pdf'; url: string }
  | { type: 'html'; html: string }
  | { type: 'markdown'; md: string }
  | { type: 'text'; text: string }
  | { type: 'table'; sheets: SheetTable[]; active: number }
  | { type: 'pptx'; slides: string[] }
  | { type: 'docx'; html: string };

function resolveKind(artifact: ChatArtifact): NonNullable<ChatArtifact['kind']> {
  if (artifact.kind && artifact.kind !== 'other') return artifact.kind;
  const ext = artifact.name.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, ChatArtifact['kind']> = {
    png: 'image',
    jpg: 'image',
    jpeg: 'image',
    gif: 'image',
    webp: 'image',
    bmp: 'image',
    svg: 'image',
    pdf: 'pdf',
    html: 'html',
    htm: 'html',
    md: 'markdown',
    markdown: 'markdown',
    txt: 'text',
    log: 'text',
    json: 'text',
    yaml: 'text',
    yml: 'text',
    csv: 'table',
    tsv: 'table',
    xlsx: 'table',
    xls: 'table',
    docx: 'docx',
    doc: 'docx',
    pptx: 'pptx',
    ppt: 'pptx',
  };
  return map[ext] || 'other';
}

export interface FilePreviewHostProps {
  artifact: ChatArtifact | null;
  onClose: () => void;
  /** 嵌在 ChatInspector 内：不要自己再开一列 */
  embedded?: boolean;
}

export function FilePreviewHost({ artifact, onClose, embedded = false }: FilePreviewHostProps) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState>({ type: 'empty' });
  const [objectUrls, setObjectUrls] = useState<string[]>([]);
  const panelRef = React.useRef<HTMLElement | null>(null);
  const fpResize = useColResize({
    storageKey: 'tk-fp-w',
    defaultWidth: 480,
    min: 280,
    max: () => maxRightPanelWidth(panelRef.current),
    edge: 'left',
  });

  const kind = artifact ? resolveKind(artifact) : 'other';
  const title = artifact?.name || '';

  useEffect(() => {
    let cancelled = false;
    const urls: string[] = [];

    async function load() {
      if (!artifact) {
        setPreview({ type: 'empty' });
        setError(null);
        return;
      }
      if (isInternalRuntimePath(artifact.path) || isScratchOrProcessFile(artifact.path)) {
        setPreview({ type: 'empty' });
        setError(t('chat.artifactDownloadFail'));
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      setPreview({ type: 'empty' });

      try {
        const k = resolveKind(artifact);
        const rel = toRelPath(artifact.path);

        if (k === 'image') {
          const blob = await fetchBlob(artifact.path);
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          urls.push(url);
          setPreview({ type: 'image', url });
        } else if (k === 'pdf') {
          const blob = await fetchBlob(artifact.path);
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          urls.push(url);
          setPreview({ type: 'pdf', url });
        } else if (k === 'html') {
          let raw = '';
          try {
            const f = await readFile(rel);
            raw = f.content;
          } catch {
            raw = await (await fetchBlob(artifact.path)).text();
          }
          if (cancelled) return;
          setPreview({ type: 'html', html: sanitizeHtmlForPreview(raw) });
        } else if (k === 'markdown') {
          let raw = '';
          try {
            const f = await readFile(rel);
            raw = f.content;
          } catch {
            raw = await (await fetchBlob(artifact.path)).text();
          }
          if (cancelled) return;
          setPreview({ type: 'markdown', md: raw });
        } else if (k === 'text') {
          let raw = '';
          try {
            const f = await readFile(rel);
            raw = f.content;
          } catch {
            raw = await (await fetchBlob(artifact.path)).text();
          }
          if (cancelled) return;
          setPreview({
            type: 'text',
            text: raw.length > 250_000 ? `${raw.slice(0, 250_000)}\n\n…[truncated]` : raw,
          });
        } else if (k === 'table') {
          const ext = artifact.name.split('.').pop()?.toLowerCase();
          const blob = await fetchBlob(artifact.path);
          if (cancelled) return;
          if (ext === 'csv') {
            const raw = await blob.text();
            setPreview({
              type: 'table',
              sheets: [{ name: artifact.name, rows: parseCsvText(raw) }],
              active: 0,
            });
          } else if (ext === 'tsv') {
            const raw = await blob.text();
            const rows = raw
              .split(/\r?\n/)
              .filter(Boolean)
              .slice(0, 120)
              .map((l) => l.split('\t'));
            setPreview({ type: 'table', sheets: [{ name: artifact.name, rows }], active: 0 });
          } else {
            const buf = await blob.arrayBuffer();
            const sheets = await loadXlsxTables(buf);
            if (cancelled) return;
            setPreview({ type: 'table', sheets, active: 0 });
          }
        } else if (k === 'docx') {
          const blob = await fetchBlob(artifact.path);
          const buf = await blob.arrayBuffer();
          const html = await loadDocxHtml(buf);
          if (cancelled) return;
          setPreview({ type: 'docx', html: sanitizeHtmlForPreview(html) });
        } else if (k === 'pptx') {
          const blob = await fetchBlob(artifact.path);
          const buf = await blob.arrayBuffer();
          const slides = await loadPptxSlides(buf);
          if (cancelled) return;
          setPreview({ type: 'pptx', slides });
        } else {
          try {
            const f = await readFile(rel);
            if (cancelled) return;
            setPreview({ type: 'text', text: f.content });
          } catch {
            setError(t('chat.previewUnsupported'));
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) {
          setLoading(false);
          setObjectUrls(urls);
        } else {
          urls.forEach((u) => URL.revokeObjectURL(u));
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
      urls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [artifact, t]);

  useEffect(() => {
    return () => {
      objectUrls.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [objectUrls]);

  const handleDownload = useCallback(async () => {
    if (!artifact) return;
    if (isInternalRuntimePath(artifact.path) || isScratchOrProcessFile(artifact.path)) {
      addToast(t('chat.artifactDownloadFail'), 'error');
      return;
    }
    try {
      const blob = await fetchBlob(artifact.path);
      const a = document.createElement('a');
      const url = URL.createObjectURL(blob);
      a.href = url;
      a.download = artifact.name || 'download';
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      addToast(t('chat.artifactDownloadFail'), 'error');
    }
  }, [artifact, addToast, t]);

  const handleOpenSystem = useCallback(async () => {
    if (!artifact) return;
    const rel = toRelPath(artifact.path);
    try {
      const electronAPI = (
        window as unknown as {
          electronAPI?: { openPath?: (p: string) => Promise<string> };
        }
      ).electronAPI;
      if (electronAPI?.openPath) {
        const meta = await resolveAbsPath(rel);
        if (meta?.abs_path && meta.exists) {
          const err = await electronAPI.openPath(meta.abs_path);
          if (!err) {
            addToast(t('chat.openedInSystem'), 'success');
            return;
          }
        }
      }
      const ok = await openViaBackend(rel);
      if (ok) addToast(t('chat.openedInSystem'), 'success');
      else addToast(t('chat.openSystemFail'), 'error');
    } catch (e) {
      addToast(t('chat.openSystemFail'), 'error');
      console.error(e);
    }
  }, [artifact, addToast, t]);

  const activeSheet = useMemo(() => {
    if (preview.type !== 'table') return null;
    return preview.sheets[preview.active] || preview.sheets[0] || null;
  }, [preview]);

  if (!artifact) {
    if (embedded) {
      return (
        <div
          className="flex h-full items-center justify-center px-6 text-center text-xs text-foreground-dim"
          data-testid="file-preview-host"
        >
          {t('chat.previewEmpty')}
        </div>
      );
    }
    return null;
  }

  const Root = embedded ? 'div' : 'aside';

  return (
    <Root
      ref={panelRef as React.Ref<HTMLDivElement & HTMLElement>}
      className={
        embedded
          ? 'flex h-full min-h-0 flex-col bg-card-bg'
          : 'relative flex h-full shrink-0 flex-col border-l border-border-subtle bg-card-bg shadow-xl'
      }
      style={embedded ? undefined : { width: fpResize.width, minWidth: 280, flex: '0 1 auto' }}
      data-testid="file-preview-host"
    >
      {embedded ? null : (
        <ColResizer
          className="tk-edge-resizer"
          label={t('layout.resizePreview' as never)}
          onStart={fpResize.onStart}
          onDrag={fpResize.onDrag}
          onEnd={fpResize.onEnd}
          onDoubleClick={fpResize.onReset}
        />
      )}
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-foreground">{title}</div>
          <div className="truncate font-mono text-[10px] text-foreground-dim" title={artifact.path}>
            {artifact.path}
            <span className="ml-2 rounded bg-elevated-bg px-1 text-[9px] uppercase tracking-wide text-foreground-muted">
              {kind}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void handleDownload()}
          className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-foreground-muted hover:bg-card-bg-hover hover:text-foreground"
        >
          {t('chat.artifactDownload')}
        </button>
        <button
          type="button"
          onClick={() => void handleOpenSystem()}
          className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-foreground-muted hover:bg-card-bg-hover hover:text-foreground"
          title={t('chat.openInSystem')}
        >
          {t('chat.openInSystem')}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
          aria-label={t('common.close')}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {loading && (
          <div className="flex h-40 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-purple/30 border-t-brand-purple" />
          </div>
        )}
        {!loading && error && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-3 text-xs text-amber-800 dark:text-amber-100/90">
            {error}
          </div>
        )}

        {!loading && !error && preview.type === 'image' && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={preview.url}
            alt={title}
            className="mx-auto max-h-full max-w-full rounded-lg object-contain"
          />
        )}

        {!loading && !error && preview.type === 'pdf' && (
          <iframe
            title={title}
            src={preview.url}
            className="h-full min-h-[70vh] w-full rounded-lg border border-border-subtle bg-white"
          />
        )}

        {!loading && !error && preview.type === 'html' && (
          <iframe
            title={title}
            sandbox=""
            srcDoc={preview.html}
            className="h-full min-h-[70vh] w-full rounded-lg border border-border-subtle bg-white"
          />
        )}

        {!loading && !error && preview.type === 'docx' && (
          <div
            className="prose prose-sm dark:prose-invert max-w-none rounded-xl border border-border-subtle bg-elevated-bg/30 p-4 text-foreground"
            onClick={(event) => {
              const target = event.target as Element | null;
              const anchor = target?.closest('a');
              if (!anchor) return;
              event.preventDefault();
              const href = anchor.getAttribute('href') || '';
              if (!/^https?:\/\//i.test(href)) return;
              if (window.electronAPI?.openExternal) {
                void window.electronAPI.openExternal(href);
              } else {
                window.open(href, '_blank', 'noopener,noreferrer');
              }
            }}
            dangerouslySetInnerHTML={{ __html: preview.html }}
          />
        )}

        {!loading && !error && preview.type === 'markdown' && (
          <div className="prose prose-sm dark:prose-invert max-w-none rounded-xl border border-border-subtle bg-elevated-bg/30 p-4 text-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.md}</ReactMarkdown>
          </div>
        )}

        {!loading && !error && preview.type === 'text' && (
          <pre className="whitespace-pre-wrap break-words rounded-xl border border-border-subtle bg-elevated-bg/40 p-3 font-mono text-[11px] leading-relaxed text-foreground-muted">
            {preview.text}
          </pre>
        )}

        {!loading && !error && preview.type === 'table' && (
          <div className="flex h-full flex-col gap-2">
            {preview.sheets.length > 1 && (
              <div className="flex flex-wrap gap-1">
                {preview.sheets.map((s, i) => (
                  <button
                    key={s.name + i}
                    type="button"
                    onClick={() =>
                      setPreview((prev) => (prev.type === 'table' ? { ...prev, active: i } : prev))
                    }
                    className={`rounded-lg px-2 py-1 text-[11px] ${
                      preview.active === i
                        ? 'bg-brand-purple/15 text-brand-purple'
                        : 'text-foreground-muted hover:bg-card-bg-hover'
                    }`}
                  >
                    {s.name}
                  </button>
                ))}
              </div>
            )}
            {activeSheet && (
              <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-border-subtle">
                <table className="w-full border-collapse text-left text-[11px]">
                  <tbody>
                    {activeSheet.rows.map((row, i) => (
                      <tr key={i} className={i === 0 ? 'bg-elevated-bg/80 font-semibold' : ''}>
                        {row.map((cell, j) => (
                          <td
                            key={j}
                            className="max-w-[14rem] truncate border-b border-border-subtle/60 px-2 py-1 text-foreground-muted"
                            title={cell}
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {!loading && !error && preview.type === 'pptx' && (
          <div className="space-y-3">
            {preview.slides.map((slide, i) => (
              <div key={i} className="rounded-xl border border-border-subtle bg-elevated-bg/40 p-3">
                <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
                  {t('chat.previewSlide').replace('{n}', String(i + 1))}
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground-muted">
                  {slide}
                </p>
              </div>
            ))}
            <p className="text-[10px] text-foreground-dim">{t('chat.previewPptxHint')}</p>
          </div>
        )}
      </div>
    </Root>
  );
}
