'use client';

/**
 * 专业模式：绑定项目根目录（文件树 / 终端 cwd）
 * 触发：forceProjectOpen、顶栏「选择项目」、切到 pro 且无 root
 */

import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { FolderOpen, X } from 'lucide-react';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';

export function OpenProjectModal() {
  const t = useT();
  const forceProjectOpen = useWorkspaceStore((s) => s.forceProjectOpen);
  const setForceProjectOpen = useWorkspaceStore((s) => s.setForceProjectOpen);
  const bindRoot = useWorkspaceStore((s) => s.bindRoot);
  const setUiMode = useWorkspaceStore((s) => s.setUiMode);
  const root = useWorkspaceStore((s) => s.root);
  const addToast = useToastStore((s) => s.addToast);

  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (forceProjectOpen) {
      setPath(root || '');
      setError(null);
      setBusy(false);
    }
  }, [forceProjectOpen, root]);

  const close = () => {
    if (busy) return;
    setForceProjectOpen(false);
  };

  const pickFolder = async () => {
    setError(null);
    try {
      const api = window.electronAPI as
        | { selectDirectory?: () => Promise<string | null> }
        | undefined;
      if (api?.selectDirectory) {
        const picked = await api.selectDirectory();
        if (picked) setPath(picked);
        return;
      }
      addToast(t('workspace._e154'), 'info');
    } catch (e: unknown) {
      const msg =
        (e as { message?: string })?.message || t('workspace._e155');
      setError(String(msg));
      addToast(t('workspace._e155'), 'error');
    }
  };

  const confirm = async () => {
    const next = path.trim();
    if (!next) {
      setError(t('workspace._e156'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await bindRoot(next);
      setForceProjectOpen(false);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } }; message?: string })?.response
          ?.data?.detail ||
        (e as { message?: string })?.message ||
        t('workspace._e157');
      setError(String(detail));
      addToast(t('workspace._e157'), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <AnimatePresence>
      {forceProjectOpen ? (
        <div className="fixed inset-0 z-[95] flex items-center justify-center bg-black/55 backdrop-blur-sm">
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="open-project-title"
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ duration: 0.15 }}
            className="w-[min(480px,92vw)] rounded-2xl border border-border-subtle bg-elevated-bg shadow-2xl shadow-black/40"
          >
            <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-5 py-4">
              <div className="min-w-0">
                <h3
                  id="open-project-title"
                  className="text-[15px] font-semibold text-foreground"
                >
                  {t('chat.selectProject') === 'chat.selectProject'
                    ? '选择项目'
                    : t('chat.selectProject')}
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-foreground-dim">
                  {t('workspace._e158')}
                </p>
                <p className="mt-0.5 text-[11px] text-foreground-dim/80">
                  {t('workspace._e159')}
                </p>
              </div>
              <button
                type="button"
                onClick={close}
                disabled={busy}
                className="rounded-lg p-1.5 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground disabled:opacity-40"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3 px-5 py-4">
              <label className="block text-[11px] font-medium text-foreground-muted">
                {t('workspace._e156')}
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void confirm();
                  }}
                  placeholder={t('workspace._e31')}
                  disabled={busy}
                  className="min-w-0 flex-1 rounded-xl border border-border-subtle bg-card-bg px-3 py-2.5 font-mono text-[12px] text-foreground outline-none ring-brand-purple/30 placeholder:text-foreground-dim focus:border-brand-purple/50 focus:ring-2"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => void pickFolder()}
                  disabled={busy}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-border-subtle bg-card-bg px-3 py-2.5 text-xs font-medium text-foreground-muted hover:border-brand-purple/40 hover:text-foreground disabled:opacity-50"
                  title={t('workspace._e32')}
                >
                  <FolderOpen className="h-3.5 w-3.5" />
                  …
                </button>
              </div>
              {error ? (
                <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {error}
                </div>
              ) : null}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle px-5 py-4">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setUiMode('simple');
                  setForceProjectOpen(false);
                }}
                className="rounded-xl px-3 py-2 text-xs font-medium text-foreground-dim hover:text-foreground disabled:opacity-40"
              >
                {t('workspace._e160')}
              </button>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={close}
                  disabled={busy}
                  className="rounded-xl border border-border-default bg-card-bg px-3.5 py-2 text-sm font-medium text-foreground-muted hover:bg-card-bg-hover disabled:opacity-40"
                >
                  {t('common.cancel') === 'common.cancel' ? '取消' : t('common.cancel')}
                </button>
                <button
                  type="button"
                  onClick={() => void confirm()}
                  disabled={busy || !path.trim()}
                  className="rounded-xl bg-brand-purple px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  {busy ? t('workspace._e161') : t('workspace._e162')}
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>
  );
}
