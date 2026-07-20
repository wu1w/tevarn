'use client';

import React, { useCallback, useState } from 'react';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useT } from '@/stores/localeStore';

export function OpenProjectModal() {
  const t = useT();
  const { forceProjectOpen, setForceProjectOpen, bindRoot, uiMode, setUiMode } =
    useWorkspaceStore();
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pickFolder = useCallback(async () => {
    setErr(null);
    try {
      const api = (window as unknown as {
        electronAPI?: { selectDirectory?: () => Promise<string | null> };
      }).electronAPI;
      if (api?.selectDirectory) {
        const dir = await api.selectDirectory();
        if (dir) setPath(dir);
        return;
      }
      setErr(t('workspace._e154'));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t('workspace._e155'));
    }
  }, []);

  const confirm = useCallback(async () => {
    const p = path.trim();
    if (!p) {
      setErr(t('workspace._e156'));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await bindRoot(p);
      setForceProjectOpen(false);
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as Error)?.message ||
        t('workspace._e157');
      setErr(String(msg));
    } finally {
      setBusy(false);
    }
  }, [path, bindRoot, setForceProjectOpen]);

  if (!forceProjectOpen) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-border-default bg-card-bg p-5 shadow-2xl">
        <h2 className="text-sm font-semibold text-foreground">{t('chat.selectProjectTitle')}</h2>
        <p className="mt-1.5 text-[12px] leading-relaxed text-foreground-muted">
          {uiMode === 'pro'
            ? t('workspace._e158')
            : t('workspace._e159')}
        </p>

        <div className="mt-4 flex gap-2">
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder={t('workspace._e31')}
            className="min-w-0 flex-1 rounded-xl border border-border-subtle bg-page-bg px-3 py-2 text-xs text-foreground outline-none focus:border-brand-purple/50"
          />
          <button
            type="button"
            onClick={pickFolder}
            className="shrink-0 rounded-xl border border-border-subtle bg-card-bg px-3 py-2 text-xs font-medium text-foreground-muted hover:bg-card-bg-hover"
          >
            浏览…
          </button>
        </div>

        {err && (
          <p className="mt-2 text-[11px] text-red-500 dark:text-red-400">{err}</p>
        )}

        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => {
              setForceProjectOpen(false);
              if (uiMode === 'pro') setUiMode('simple');
            }}
            className="text-[11px] text-foreground-dim hover:text-foreground"
          >
            {uiMode === 'pro' ? t('workspace._e160') : t('contextDash.cancel')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={confirm}
            className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            {busy ? t('workspace._e161') : t('workspace._e162')}
          </button>
        </div>
      </div>
    </div>
  );
}
