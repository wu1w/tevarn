'use client';

import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { CtxItem, ContextFlow, ContextStats, ContextOptimizeResult } from '@/types';
import {
  getCtxItems,
  getContextFlows,
  createCtxItem,
  deleteCtxItem,
  togglePin,
  getContextStats,
  optimizeContext,
} from '@/lib/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { useToastStore } from '@/stores/toastStore';
import { t, useT } from '@/stores/localeStore';

/* ─── 中文标签映射 ─── */
const SCOPE_LABELS: Record<string, { label: string; icon: string }> = {
  system: { label: t('contextDash.scope.system'), icon: '⚙️' },
  user: { label: t('evolution.source.user'), icon: '👤' },
  project: { label: t('memory.type.project'), icon: '📁' },
  session: { label: t('contextDash.scope.session'), icon: '💬' },
  knowledge: { label: t('contextDash.scope.knowledge'), icon: '📚' },
};

const KIND_LABELS: Record<string, { label: string; icon: string }> = {
  instruction: { label: t('contextDash.kind.instruction'), icon: '📋' },
  memory: { label: t('contextDash.kind.memory'), icon: '🧠' },
  doc: { label: t('contextDash.kind.doc'), icon: '📄' },
  message: { label: t('contextDash.kind.message'), icon: '💬' },
  rag: { label: t('contextDash.kind.rag'), icon: '🔍' },
  'tool-def': { label: t('contextDash.kind.toolDef'), icon: '🔧' },
};

/* ─── Token 预算条 ─── */
function TokenBudgetBar({ used, total }: { used: number; total: number }) {
  const pct = Math.min((used / total) * 100, 100);
  const color =
    pct > 90 ? 'bg-error-text' : pct > 70 ? 'bg-amber-500' : 'bg-brand-cyan';
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full bg-elevated-bg overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-foreground-dim whitespace-nowrap">
        {used.toLocaleString()} / {total.toLocaleString()}
      </span>
    </div>
  );
}

/* ─── 统计卡片 ─── */
function StatCard({ icon, label, value, sub }: { icon: string; label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-lg border border-border-default bg-card-bg p-3 flex items-center gap-3">
      <span className="text-xl">{icon}</span>
      <div className="min-w-0">
        <div className="text-lg font-bold text-foreground">{value}</div>
        <div className="text-xs text-foreground-dim">{label}</div>
        {sub && <div className="text-[10px] text-foreground-muted">{sub}</div>}
      </div>
    </div>
  );
}

/* ─── 上下文项卡片 ─── */
function ContextItemCard({
  item,
  onPin,
  onDelete,
}: {
  item: CtxItem;
  onPin: (id: string, pinned: boolean) => void;
  onDelete: (id: string) => void;
}) {
  const scope = SCOPE_LABELS[item.scope] || { label: item.scope, icon: '📦' };
  const kind = KIND_LABELS[item.kind] || { label: item.kind, icon: '📄' };
  const valuePreview = item.value.length > 80 ? item.value.slice(0, 80) + '…' : item.value;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border-default bg-card-bg p-3 hover:bg-elevated-bg/50 transition-colors group">
      <span className="text-base mt-0.5">{kind.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-foreground">{item.key}</span>
          <span className="rounded bg-card-bg-hover px-1.5 py-0.5 text-[10px] text-foreground-dim">
            {scope.icon} {scope.label}
          </span>
          {item.pinned && <span className="text-[10px] text-amber-500">{t('contextDash.filterPinned')}</span>}
        </div>
        <div className="mt-0.5 text-xs text-foreground-dim leading-relaxed line-clamp-2">{valuePreview}</div>
        <div className="mt-1 flex items-center gap-2 text-[10px] text-foreground-muted">
          <span>{item.tokens} tok</span>
          <span>·</span>
          <span>{new Date(item.updated_at).toLocaleString()}</span>
        </div>
      </div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => onPin(item.id, item.pinned)}
          className={`rounded px-1.5 py-1 text-xs ${item.pinned ? 'bg-amber-500/10 text-amber-500' : 'bg-card-bg-hover text-foreground-dim hover:bg-elevated-bg'}`}
          title={item.pinned ? t('contextDash.unpin') : t('contextDash.pin')}
        >
          {item.pinned ? '📌' : '📍'}
        </button>
        <button
          onClick={() => onDelete(item.id)}
          className="rounded bg-error-bg px-1.5 py-1 text-xs text-error-text hover:bg-error-bg"
          title={t('memory.delete')}
        >
          🗑️
        </button>
      </div>
    </div>
  );
}

/* ─── 新建上下文弹窗（自然语言表单） ─── */
function ContextCreateModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { currentSession } = useSessionStore();
  const addToast = useToastStore((s) => s.addToast);
  const [desc, setDesc] = useState('');
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!desc.trim() || !content.trim()) {
      addToast(t('contextDash.fillRequired'), 'info');
      return;
    }
    setSubmitting(true);
    try {
      // 智能识别 scope/kind：关键词匹配
      const lower = desc.toLowerCase();
      let scope = 'session';
      let kind = 'memory';
      if (lower.includes(t('contextDash.scope.system')) || lower.includes(t('contextDash.kind.instruction'))) { scope = 'system'; kind = 'instruction'; }
      else if (lower.includes(t('memory.type.project')) || lower.includes(t('contextDash.kind.doc'))) { scope = 'project'; kind = 'doc'; }
      else if (lower.includes(t('context._e85')) || lower.includes('rag')) { scope = 'knowledge'; kind = 'rag'; }
      else if (lower.includes(t('memory.type.tool')) || lower.includes('function')) { scope = 'session'; kind = 'tool-def'; }
      else if (lower.includes(t('evolution.source.user')) || lower.includes(t('memory.type.preference'))) { scope = 'user'; kind = 'memory'; }

      await createCtxItem({
        session_id: currentSession?.id,
        scope,
        kind,
        key: desc.trim(),
        value: content.trim(),
        tokens: Math.ceil(content.length / 2), // 粗略估算
      });
      addToast(t('contextDash.created'), 'success');
      setDesc('');
      setContent('');
      onClose();
      onCreated();
    } catch (err) {
      addToast(err instanceof Error ? err.message : t('channels.createFailed'), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-card-bg p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-base font-semibold text-foreground">{t('contextDash.addTitle')}</h3>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-foreground-muted">{t('contextDash.descLabel')}</label>
            <input
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
              placeholder={t('contextDash.descPlaceholder')}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-foreground-muted">{t('contextDash.contentLabel')}</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={5}
              className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
              placeholder={t('contextDash.contentPlaceholder')}
            />
          </div>
          <div className="text-[10px] text-foreground-muted">
            {t('contextDash.autoDetectHint')}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-border-default px-4 py-2 text-sm text-foreground-muted hover:bg-elevated-bg">
            {t('contextDash.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50"
          >
            {submitting ? t('contextDash.creating') : t('channels.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── 主组件 ─── */
export default function ContextDashboard() {
  const t = useT();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const addToast = useToastStore((s) => s.addToast);
  const { currentSession } = useSessionStore();
  const [items, setItems] = useState<CtxItem[]>([]);
  const [flows, setFlows] = useState<ContextFlow[]>([]);
  const [stats, setStats] = useState<ContextStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeResult, setOptimizeResult] = useState<ContextOptimizeResult | null>(null);
  const [filterScope, setFilterScope] = useState<string | null>(null);
  const [filterPinned, setFilterPinned] = useState(false);
  const [search, setSearch] = useState('');

  const load = useCallback(() => {
    if (!currentSession) return;
    setLoading(true);
    Promise.all([
      getCtxItems(currentSession.id).then((d) => setItems(Array.isArray(d) ? (d as CtxItem[]) : [])),
      getContextFlows(currentSession.id).then((d) => setFlows(Array.isArray(d) ? (d as ContextFlow[]) : [])),
      getContextStats(currentSession.id).then((d) => setStats(d as ContextStats)),
    ])
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [currentSession]);

  useEffect(() => { load(); }, [load]);

  const filteredItems = useMemo(() => {
    let result = items;
    if (filterScope) result = result.filter((i) => i.scope === filterScope);
    if (filterPinned) result = result.filter((i) => i.pinned);
    if (search) {
      const q = search.toLowerCase();
      result = result.filter((i) => i.key.toLowerCase().includes(q) || i.value.toLowerCase().includes(q));
    }
    return result;
  }, [items, filterScope, filterPinned, search]);

  const handlePin = async (id: string, pinned: boolean) => {
    try {
      await togglePin(id, !pinned);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm(t('contextDash.confirmDelete'));
    if (!ok) return;
    try {
      await deleteCtxItem(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  const handleOptimize = async () => {
    if (!currentSession) return;
    setOptimizing(true);
    try {
      const result = await optimizeContext(currentSession.id);
      setOptimizeResult(result);
      addToast(t('contextDash.optDone').replace('{n}', String(result.saved_tokens)), 'success');
      load();
    } catch (err) {
      addToast(t('contextDash.optimizeFailed'), 'error');
    } finally {
      setOptimizing(false);
    }
  };

  if (!currentSession) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-border-default bg-card-bg py-12 text-center text-foreground-muted">
          {t('contextDash.pickSession')}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t('contextDash.title')}</h1>
          <p className="text-xs text-foreground-dim mt-1">{t('contextDash.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleOptimize}
            disabled={optimizing}
            className="rounded-md bg-success-text px-4 py-2 text-sm font-medium text-white hover:bg-success-text/80 disabled:opacity-50"
          >
            {optimizing ? t('contextDash.optimizing') : t('contextDash.optimize')}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80"
          >
            {t('contextDash.add')}
          </button>
        </div>
      </div>

      {/* 优化结果提示 */}
      {optimizeResult && (
        <div className="rounded-lg border border-success-text/20 bg-success-bg p-3 text-sm text-success-text flex items-center gap-2">
          <span>✅</span>
          <span>{t('contextDash.optSummary').replace('{saved}', String(optimizeResult.saved_tokens)).replace('{pruned}', String(optimizeResult.pruned_count)).replace('{summarized}', String(optimizeResult.summarized_count))}</span>
          <button onClick={() => setOptimizeResult(null)} className="ml-auto text-success-text/60 hover:text-success-text">✕</button>
        </div>
      )}

      {/* 统计面板 */}
      {stats && (
        <div className="space-y-3">
          <div className="grid grid-cols-4 gap-3">
            <StatCard icon="📊" label={t('contextDash.stat.totalTokens')} value={stats.total_tokens.toLocaleString()} sub={`/${stats.context_window.toLocaleString()}`} />
            <StatCard icon="📌" label={t('contextDash.pinned')} value={stats.pinned_tokens.toLocaleString()} sub={t('contextDash.nItems').replace('{n}', String(stats.item_count))} />
            <StatCard icon="💬" label={t('contextDash.stat.session')} value={stats.session_tokens.toLocaleString()} />
            <StatCard icon="🔍" label={t('contextDash.stat.rag')} value={stats.rag_tokens.toLocaleString()} />
          </div>
          <TokenBudgetBar used={stats.total_tokens} total={stats.context_window} />
        </div>
      )}

      {/* 筛选栏 */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('contextDash.searchPlaceholder')}
            className="w-full rounded-md border border-border-default pl-8 pr-3 py-1.5 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
          />
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-foreground-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => setFilterScope(null)}
            className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${!filterScope ? 'bg-brand-purple text-white' : 'bg-card-bg-hover text-foreground-dim hover:bg-elevated-bg'}`}
          >
            {t('contextDash.all')}
          </button>
          {Object.entries(SCOPE_LABELS).map(([key, val]) => (
            <button
              key={key}
              onClick={() => setFilterScope(filterScope === key ? null : key)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${filterScope === key ? 'bg-brand-purple text-white' : 'bg-card-bg-hover text-foreground-dim hover:bg-elevated-bg'}`}
            >
              {val.icon} {val.label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setFilterPinned(!filterPinned)}
          className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${filterPinned ? 'bg-amber-500 text-white' : 'bg-card-bg-hover text-foreground-dim hover:bg-elevated-bg'}`}
        >
          {t('contextDash.pinnedItems')}
        </button>
      </div>

      {/* 上下文项列表 */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-foreground-dim">
          {t('contextDash.items').replace('{filtered}', String(filteredItems.length)).replace('{total}', String(items.length))}
        </h2>
        {loading ? (
          <div className="py-8 text-center text-foreground-muted">{t('profile.loading')}</div>
        ) : filteredItems.length === 0 ? (
          <div className="rounded-lg border border-border-default bg-card-bg py-8 text-center text-foreground-muted">
            {items.length === 0 ? t('contextDash.empty') : t('contextDash.noMatch')}
          </div>
        ) : (
          <div className="space-y-2">
            {filteredItems.map((item) => (
              <ContextItemCard key={item.id} item={item} onPin={handlePin} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>

      {/* 上下文流 */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-foreground-dim">
          {t('contextDash.accessLog').replace('{n}', String(flows.length))}
        </h2>
        {flows.length === 0 ? (
          <div className="rounded-lg border border-border-default bg-card-bg py-8 text-center text-foreground-muted">{t('contextDash.noFlows')}</div>
        ) : (
          <div className="space-y-1.5">
            {flows.map((flow) => (
              <div key={flow.id} className="flex items-center justify-between rounded-lg border border-border-default bg-card-bg px-4 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium text-foreground">{flow.agent}</span>
                  <span className="text-foreground-muted">·</span>
                  <span className="rounded bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-400">
                    {SCOPE_LABELS[flow.scope]?.icon} {SCOPE_LABELS[flow.scope]?.label || flow.scope}
                  </span>
                  <span className="text-xs text-foreground-dim">{t('contextDash.nItems').replace('{n}', String(flow.keys?.length || 0))}</span>
                </div>
                <div className="text-xs text-foreground-dim">{flow.tokens} tok</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 新建弹窗 */}
      <ContextCreateModal open={showCreate} onClose={() => setShowCreate(false)} onCreated={load} />

      {ConfirmDialogComponent}
    </div>
  );
}