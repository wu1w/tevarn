'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  EvolutionAsset,
  bulkDeleteEvolution,
  deleteEvolutionAsset,
  enableEvolution,
  getEvolutionAssets,
  getEvolutionStats,
  runEvolutionTask,
  setEvolutionAssetEnabled,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { Skeleton } from '@/components/desktop/Skeleton';
import { EmptyState } from '@/components/desktop/EmptyState';

const BADGE =
  'rounded border border-border-subtle bg-elevated-bg/80 px-1.5 py-0.5 text-[10px] font-medium text-foreground-muted';

export default function EvolutionPage() {
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<any>(null);
  const [assets, setAssets] = useState<EvolutionAsset[]>([]);
  const [unusedOnly, setUnusedOnly] = useState(false);
  const [source, setSource] = useState<string>('');
  const [status, setStatus] = useState<string>('');
  const [sort, setSort] = useState('updated_at');
  const [selected, setSelected] = useState<EvolutionAsset | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, list] = await Promise.all([
        getEvolutionStats(),
        getEvolutionAssets({
          unused_only: unusedOnly || undefined,
          source: source || undefined,
          status: status || undefined,
          sort,
        }),
      ]);
      setStats(s);
      setAssets(Array.isArray(list) ? list : []);
    } catch (e: any) {
      addToast(e?.response?.data?.detail || e?.message || '加载失败', 'error');
    } finally {
      setLoading(false);
    }
  }, [unusedOnly, source, status, sort, addToast]);

  useEffect(() => {
    load();
  }, [load]);

  const enabled = !!stats?.enabled;

  const onToggleEngine = async () => {
    setBusy(true);
    try {
      await enableEvolution({
        enabled: !enabled,
        auto_apply_skills: true,
        mode: 'on_failure',
      });
      addToast(!enabled ? '自主进化已开启（过门自动生效）' : '自主进化已关闭', 'success');
      await load();
    } catch (e: any) {
      addToast(e?.message || '切换失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (a: EvolutionAsset) => {
    if (a.source === 'seed') {
      addToast('预置资产不可删除', 'error');
      return;
    }
    const ok = await confirm(
      `确定删除「${a.name}」？删除后 Agent 不再使用该项。`,
      '删除进化资产'
    );
    if (!ok) return;
    try {
      await deleteEvolutionAsset(a.id);
      addToast('已删除', 'success');
      if (selected?.id === a.id) setSelected(null);
      await load();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || '删除失败', 'error');
    }
  };

  const onBulkUnused = async () => {
    const ok = await confirm(
      `将删除所有「自主归纳且使用次数为 0」的资产（含草稿）。确定？`,
      '批量清理未使用'
    );
    if (!ok) return;
    try {
      const r = await bulkDeleteEvolution({ filter: 'unused_auto' });
      addToast(`已删除 ${r.deleted ?? 0} 项`, 'success');
      await load();
    } catch (e: any) {
      addToast(e?.message || '批量删除失败', 'error');
    }
  };

  const top = useMemo(() => stats?.top_used || [], [stats]);

  return (
    <div className="flex h-full min-h-0 flex-col p-6">
      {ConfirmDialogComponent}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">自主进化</h1>
          <p className="mt-0.5 text-xs text-foreground-dim">
            查看 Agent 归纳了什么、用了多少次；无用的可删。开启后过安全门将自动生效（auto-apply）。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onToggleEngine}
            className={`rounded-xl px-4 py-2 text-sm font-medium text-white ${
              enabled
                ? 'bg-emerald-600 hover:bg-emerald-500'
                : 'bg-gradient-to-r from-brand-purple to-brand-cyan'
            }`}
          >
            {enabled ? '进化：已开启' : '开启进化'}
          </button>
          <button
            type="button"
            onClick={() => void runEvolutionTask('smoke-health').then(() => addToast('已跑 smoke-health', 'success')).catch((e) => addToast(String(e), 'error'))}
            className="rounded-xl border border-border-default px-3 py-2 text-sm text-foreground-muted hover:border-brand-cyan/40"
          >
            跑验收任务
          </button>
          <button
            type="button"
            onClick={() => void onBulkUnused()}
            className="rounded-xl border border-error-text/30 px-3 py-2 text-sm text-error-text hover:bg-error-bg"
          >
            清理未使用
          </button>
        </div>
      </div>

      {/* stats */}
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: '自主归纳', value: stats?.auto_count ?? '—' },
          { label: '生效中', value: stats?.active_count ?? '—' },
          { label: '待审草稿', value: stats?.draft_count ?? '—' },
          { label: '从未使用(auto)', value: stats?.unused_auto_count ?? '—' },
        ].map((c) => (
          <div
            key={c.label}
            className="rounded-xl border border-border-subtle bg-card-bg/50 px-4 py-3"
          >
            <div className="text-[11px] text-foreground-dim">{c.label}</div>
            <div className="mt-1 text-2xl font-semibold text-foreground">{c.value}</div>
          </div>
        ))}
      </div>

      {top.length > 0 && (
        <div className="mb-4 rounded-xl border border-border-subtle bg-card-bg/30 px-4 py-3">
          <div className="mb-2 text-xs font-medium text-foreground-muted">使用 Top</div>
          <div className="flex flex-wrap gap-2">
            {top.map((t: any) => (
              <span key={`${t.kind}-${t.name}`} className={BADGE}>
                {t.name} · {t.use_count}次
              </span>
            ))}
          </div>
        </div>
      )}

      {/* filters */}
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1.5 text-foreground-muted">
          <input
            type="checkbox"
            checked={unusedOnly}
            onChange={(e) => setUnusedOnly(e.target.checked)}
          />
          仅未使用
        </label>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="rounded-lg border border-border-subtle bg-input-bg px-2 py-1 text-foreground"
        >
          <option value="">来源：全部</option>
          <option value="auto">自主</option>
          <option value="seed">预置</option>
          <option value="user">用户</option>
        </select>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-lg border border-border-subtle bg-input-bg px-2 py-1 text-foreground"
        >
          <option value="">状态：全部</option>
          <option value="active">生效</option>
          <option value="draft">草稿</option>
          <option value="disabled">停用</option>
          <option value="rejected">已拒绝</option>
        </select>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          className="rounded-lg border border-border-subtle bg-input-bg px-2 py-1 text-foreground"
        >
          <option value="updated_at">最近更新</option>
          <option value="use_count">使用次数</option>
          <option value="created_at">最近创建</option>
          <option value="name">名称</option>
        </select>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-lg border border-border-subtle px-2 py-1 text-foreground-muted hover:text-foreground"
        >
          刷新
        </button>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_360px]">
        <div className="min-h-0 overflow-y-auto rounded-xl border border-border-subtle">
          {loading ? (
            <div className="p-4">
              <Skeleton className="h-10 w-full" />
            </div>
          ) : assets.length === 0 ? (
            <EmptyState
              title="暂无进化资产"
              description="开启进化并使用 Agent 后，归纳结果会出现在这里。"
            />
          ) : (
            <ul className="divide-y divide-border-subtle">
              {assets.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(a)}
                    className={`flex w-full items-start justify-between gap-3 px-4 py-3 text-left hover:bg-card-bg-hover ${
                      selected?.id === a.id ? 'bg-brand-purple/10' : ''
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-foreground">
                          {a.name}
                        </span>
                        <span className={BADGE}>{a.kind}</span>
                        <span className={BADGE}>{a.source}</span>
                        <span className={BADGE}>{a.status}</span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[11px] text-foreground-dim">
                        {a.summary || '（无摘要）'}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="font-mono text-sm text-brand-cyan">{a.use_count}</div>
                      <div className="text-[10px] text-foreground-dim">次使用</div>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto rounded-xl border border-border-subtle bg-card-bg/20 p-4">
          {!selected ? (
            <div className="py-16 text-center text-sm text-foreground-dim">选择左侧资产查看详情</div>
          ) : (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold text-foreground">{selected.name}</h2>
              <div className="flex flex-wrap gap-1.5">
                <span className={BADGE}>{selected.kind}</span>
                <span className={BADGE}>{selected.source}</span>
                <span className={BADGE}>{selected.status}</span>
                <span className={BADGE}>Gen{selected.gen}</span>
                <span className={BADGE}>用了 {selected.use_count} 次</span>
                {selected.last_score != null && (
                  <span className={BADGE}>分 {Number(selected.last_score).toFixed(2)}</span>
                )}
              </div>
              <p className="text-xs text-foreground-muted">{selected.summary}</p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-input-bg p-3 text-[11px] text-foreground">
                {selected.content || '（无正文）'}
              </pre>
              <div className="flex flex-wrap gap-2">
                {selected.status !== 'active' && selected.source !== 'seed' && (
                  <button
                    type="button"
                    onClick={async () => {
                      await setEvolutionAssetEnabled(selected.id, true);
                      addToast('已启用', 'success');
                      await load();
                    }}
                    className="rounded-lg bg-brand-purple/80 px-3 py-1.5 text-xs text-white"
                  >
                    启用
                  </button>
                )}
                {selected.status === 'active' && selected.source !== 'seed' && (
                  <button
                    type="button"
                    onClick={async () => {
                      await setEvolutionAssetEnabled(selected.id, false);
                      addToast('已停用', 'success');
                      await load();
                    }}
                    className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-foreground-muted"
                  >
                    停用
                  </button>
                )}
                {selected.source !== 'seed' && (
                  <button
                    type="button"
                    onClick={() => void onDelete(selected)}
                    className="rounded-lg border border-error-text/30 px-3 py-1.5 text-xs text-error-text"
                  >
                    删除
                  </button>
                )}
              </div>
              <div className="text-[10px] text-foreground-dim">
                创建 {selected.created_at}
                {selected.last_used_at ? ` · 最近使用 ${selected.last_used_at}` : ''}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
