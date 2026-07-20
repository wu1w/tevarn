'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  attachPackage,
  detachPackage,
  getSystemLayers,
  listPackages,
  type SystemLayer,
  type SystemLayersReport,
  type TaktonPackageItem,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';

function LayerCard({ layer }: { layer: SystemLayer }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const pctColor =
    layer.tokens_est > 2000 ? 'text-amber-500' : layer.tokens_est > 0 ? 'text-brand-cyan' : 'text-foreground-muted';
  return (
    <div className="rounded-lg border border-border-default bg-card-bg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-elevated-bg/50"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">{layer.label}</span>
            {layer.mutable ? (
              <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-[10px] text-brand-purple">{t('context._e13')}</span>
            ) : (
              <span className="rounded bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">{t('contextDash.pin')}</span>
            )}
          </div>
          <div className="truncate text-[10px] text-foreground-dim">{layer.source}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-xs font-medium ${pctColor}`}>{layer.tokens_est} tok</div>
          <div className="text-[10px] text-foreground-muted">{layer.chars} 字</div>
        </div>
      </button>
      {open && (
        <div className="border-t border-border-subtle px-3 py-2">
          {layer.items && layer.items.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1">
              {layer.items.slice(0, 12).map((it, i) => (
                <span
                  key={i}
                  className="rounded-md border border-border-subtle bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted"
                >
                  {String((it as any).name || (it as any).key || (it as any).kind || JSON.stringify(it).slice(0, 40))}
                </span>
              ))}
            </div>
          )}
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-elevated-bg/60 p-2 font-mono text-[11px] text-foreground-dim">
            {layer.content?.trim() ? layer.content : t('context._e86')}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function SystemLayersPanel({ sessionId }: { sessionId?: string | null }) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [report, setReport] = useState<SystemLayersReport | null>(null);
  const [packages, setPackages] = useState<TaktonPackageItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyName, setBusyName] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [layers, pkgRes] = await Promise.all([
        getSystemLayers(sessionId ? { session_id: sessionId } : undefined),
        listPackages(sessionId || undefined),
      ]);
      setReport(layers);
      setPackages(Array.isArray(pkgRes?.packages) ? pkgRes.packages : []);
    } catch (e) {
      console.error(e);
      addToast(t('context._e87'), 'error');
    } finally {
      setLoading(false);
    }
  }, [sessionId, addToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const togglePkg = async (pkg: TaktonPackageItem) => {
    if (!sessionId) {
      addToast(t('context._e88'), 'info');
      return;
    }
    setBusyName(pkg.name);
    try {
      if (pkg.attached) {
        await detachPackage(sessionId, pkg.name);
        addToast(`已卸载 ${pkg.name}`, 'success');
      } else {
        await attachPackage(sessionId, pkg.name);
        addToast(`已挂载 ${pkg.name}`, 'success');
      }
      await load();
    } catch (e) {
      addToast(e instanceof Error ? e.message : t('modelPicker.opFailed'), 'error');
    } finally {
      setBusyName(null);
    }
  };

  const totals = report?.totals;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border-default bg-card-bg p-4">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-foreground">System 分层（可观测）</h2>
            <p className="mt-0.5 text-[11px] text-foreground-muted">
              对齐 Pi：Stable 核心保持短小；人格 / 包 / 动态注入分开展示
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="shrink-0 rounded-md border border-border-default px-2.5 py-1 text-[11px] text-foreground-muted hover:bg-elevated-bg disabled:opacity-50"
          >
            {loading ? t('settings.refreshing') : t('modelPicker.refresh')}
          </button>
        </div>
        {totals && (
          <div className="mb-3 flex flex-wrap gap-3 text-[11px] text-foreground-dim">
            <span>合计约 {totals.tokens_est} tok / {totals.chars} 字</span>
            <span>合并预览 {totals.merged_tokens_est} tok</span>
          </div>
        )}
        <div className="space-y-2">
          {(report?.layers || []).map((layer) => (
            <LayerCard key={layer.id} layer={layer} />
          ))}
          {!report && !loading && (
            <div className="py-6 text-center text-xs text-foreground-muted">{t('context._e14')}</div>
          )}
        </div>
        {report?.legend && (
          <div className="mt-3 grid gap-1 border-t border-border-subtle pt-3 text-[10px] text-foreground-muted sm:grid-cols-2">
            {report.legend.map((l) => (
              <div key={l.id}>
                <span className="font-medium text-foreground-dim">{l.id}</span> · {l.desc}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border-default bg-card-bg p-4">
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-foreground">Takton Packages</h2>
          <p className="mt-0.5 text-[11px] text-foreground-muted">
            统一 skill / 子代理 / 工作流投影；挂载后只注入 Context 层，不污染核心
          </p>
        </div>
        <div className="max-h-72 space-y-1.5 overflow-y-auto">
          {packages.length === 0 && (
            <div className="py-6 text-center text-xs text-foreground-muted">
              暂无包。可在 workspace/packages 下放 takton.package.json
            </div>
          )}
          {packages.map((pkg) => (
            <div
              key={pkg.name}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${
                pkg.attached
                  ? 'border-brand-purple/30 bg-brand-purple/[0.04]'
                  : 'border-border-subtle bg-elevated-bg/30'
              }`}
            >
              <span className="text-base">{pkg.icon || '📦'}</span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-foreground">{pkg.name}</div>
                <div className="truncate text-[10px] text-foreground-muted">
                  {pkg.source}
                  {pkg.virtual ? ' · virtual' : ''}
                  {pkg.description ? ` · ${pkg.description}` : ''}
                </div>
              </div>
              <button
                type="button"
                disabled={!sessionId || busyName === pkg.name}
                onClick={() => void togglePkg(pkg)}
                className={`shrink-0 rounded-md px-2.5 py-1 text-[11px] font-medium disabled:opacity-50 ${
                  pkg.attached
                    ? 'bg-elevated-bg text-foreground-muted hover:bg-card-bg-hover'
                    : 'bg-brand-purple text-white hover:opacity-90'
                }`}
              >
                {busyName === pkg.name ? '…' : pkg.attached ? t('mcpStore.uninstall') : t('context._e89')}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
