'use client';

/**
 * MCP 商店 — Skills 商店同理念：
 * - 精选目录 + Official MCP Registry 多源
 * - Claude Code / Hermes / OpenClaw / Codex 共享 MCP 协议，目录可互通安装
 * - brand-purple 视觉、搜索筛选、一键安装
 */

import React, { memo, useCallback, useEffect, useMemo, useState } from 'react';
import type { MCPServerFormData, MCPServerStatus } from '@/types';
import {
  listMCPStore,
  installMCPFromStore,
  type UnifiedMCPStoreItem,
  type MCPStoreSourceInfo,
} from '@/lib/api';
import {
  useMCPServers,
  useMCPStatus,
  useCreateMCPServerMutation,
  useToggleMCPServerMutation,
  useDeleteMCPServerMutation,
  useReloadMCPServersMutation,
} from '@/lib/api-hooks';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { EmptyState } from '@/components/desktop/EmptyState';
import { Skeleton } from '@/components/desktop/Skeleton';
import { useT } from '@/stores/localeStore';

const BTN_PRIMARY =
  'rounded-lg bg-brand-purple px-3 py-1.5 text-xs font-semibold text-white shadow-sm shadow-brand-purple/20 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';
const BTN_PRIMARY_LG =
  'rounded-lg bg-brand-purple px-4 py-2 text-sm font-semibold text-white shadow-sm shadow-brand-purple/20 transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50';
const BTN_SECONDARY =
  'rounded-lg border border-border-subtle bg-elevated-bg px-3 py-1.5 text-xs font-medium text-foreground-muted transition-colors hover:border-brand-purple/40 hover:text-foreground';
const CHIP_ACTIVE =
  'border-brand-purple/40 bg-brand-purple text-white shadow-sm shadow-brand-purple/15';
const CHIP_IDLE =
  'border-border-subtle bg-elevated-bg text-foreground-muted hover:border-brand-purple/35 hover:text-foreground';

const SOURCE_META: Record<string, { labelKey: string; color: string; tipKey: string }> = {
  all: { labelKey: 'mcpStore.allSources', color: '', tipKey: 'mcpStore.allTip' },
  curated: {
    labelKey: 'mcpStore.curated',
    color: 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/25',
    tipKey: 'mcpStore.curatedTip',
  },
  official: {
    labelKey: 'Official Registry',
    color: 'bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/25',
    tipKey: 'mcpStore.officialTip',
  },
};

const RISK_LABEL_KEY: Record<string, string> = {
  safe: 'mcpStore.risk.safe',
  low: 'mcpStore.risk.low',
  medium: 'mcpStore.risk.medium',
  high: 'mcpStore.risk.high',
  dangerous: 'mcpStore.risk.dangerous',
};

function riskClass(risk: string): string {
  switch (risk) {
    case 'safe':
      return 'text-emerald-600 dark:text-emerald-400';
    case 'low':
      return 'text-cyan-600 dark:text-cyan-400';
    case 'medium':
      return 'text-amber-600 dark:text-amber-400';
    case 'high':
      return 'text-orange-600 dark:text-orange-400';
    case 'dangerous':
      return 'text-rose-600 dark:text-rose-400';
    default:
      return 'text-foreground-muted';
  }
}

function formatPop(n: number): string {
  if (!n) return '';
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

function joinArgs(args: string[] | null | undefined): string {
  if (!args?.length) return '';
  return args.map((a) => (/\s|"/.test(a) ? JSON.stringify(a) : a)).join(' ');
}

function toForm(item: UnifiedMCPStoreItem): MCPServerFormData {
  return {
    name: item.name,
    description: item.summary || item.display_name,
    transport: item.transport,
    command: item.command || '',
    args: joinArgs(item.args),
    url: item.url || '',
    env: item.env_hint || '',
    enabled: true,
    timeout: 30,
    risk_level: item.risk_level || 'medium',
    allowed_paths: '',
  };
}

const StoreCard = memo(function StoreCard({
  item,
  installed,
  busy,
  onInstall,
  onOpen,
  onUninstall,
}: {
  item: UnifiedMCPStoreItem;
  installed: boolean;
  busy: boolean;
  onInstall: (i: UnifiedMCPStoreItem) => void;
  onOpen: (i: UnifiedMCPStoreItem) => void;
  onUninstall?: (i: UnifiedMCPStoreItem) => void;
}) {
  const t = useT();
  const meta = SOURCE_META[item.source] || SOURCE_META.curated;
  return (
    <article
      className={`group relative flex flex-col rounded-xl border border-border-subtle/80 bg-gradient-to-b from-elevated-bg/80 to-elevated-bg/40 p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-brand-purple/35 hover:shadow-md ${
        installed ? 'ring-1 ring-brand-purple/30' : ''
      }`}
    >
      {installed && (
        <span className="absolute right-3 top-3 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
          {t('mcpStore.installed')}
        </span>
      )}
      <div className="mb-2 flex items-start gap-2 pr-14">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border-subtle bg-elevated-bg text-lg">
          {item.icon || '🔌'}
        </div>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onOpen(item)}
            className="block w-full truncate text-left text-sm font-semibold text-foreground hover:text-brand-purple"
          >
            {item.display_name}
          </button>
          <div className="mt-0.5 truncate text-[10px] text-foreground-muted">
            {item.category}
            {item.version ? ` · v${item.version}` : ''}
          </div>
        </div>
      </div>
      <p className="mb-3 line-clamp-2 min-h-[2.5rem] flex-1 text-xs leading-relaxed text-foreground-muted">
        {item.summary || item.description}
      </p>
      <div className="mb-2 flex flex-wrap gap-1">
        <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${meta.color}`}>
          {meta.labelKey.startsWith('mcpStore.') || meta.labelKey.startsWith('store.')
            ? t(meta.labelKey as never)
            : meta.labelKey}
        </span>
        <span className="rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
          {item.transport}
        </span>
        <span
          className={`rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] font-medium ${riskClass(item.risk_level)}`}
        >
          {t('mcpStore.riskLabel').replace('{level}', RISK_LABEL_KEY[item.risk_level] ? t(RISK_LABEL_KEY[item.risk_level] as never) : item.risk_level)}
        </span>
        {item.popularity > 0 && (
          <span className="rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
            ⬇ {formatPop(item.popularity)}
          </span>
        )}
      </div>
      {item.tags?.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {item.tags.slice(0, 4).map((tag) => (
            <span
              key={tag}
              className="rounded-md bg-brand-purple/10 px-1.5 py-0.5 text-[10px] text-brand-purple"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      <div className="mt-auto flex gap-2">
        {installed ? (
          <>
            <button
              type="button"
              onClick={() => onOpen(item)}
              title={t('mcpStore.configureTitle')}
              className={`flex-1 ${BTN_PRIMARY}`}
            >
              {t('mcpStore.configure')}
            </button>
            <button
              type="button"
              disabled={busy || !onUninstall}
              onClick={() => onUninstall?.(item)}
              className="rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-500/20 disabled:opacity-50"
            >
              {busy ? t('mcpStore.busy') : t('mcpStore.uninstall')}
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy || !item.installable}
            title={!item.installable ? item.note || t('mcpStore.notInstallable') : t('mcpStore.installLocal')}
            onClick={() => onInstall(item)}
            className={`flex-1 ${BTN_PRIMARY}`}
          >
            {busy ? t('mcpStore.installing') : item.installable ? t('mcpStore.oneClick') : t('mcpStore.noDirect')}
          </button>
        )}
        <button type="button" onClick={() => onOpen(item)} className={BTN_SECONDARY}>
          {t('mcpStore.details')}
        </button>
      </div>
    </article>
  );
});

export type MCPPageTab = 'store' | 'installed' | 'custom';

export default function MCPStorePanel({
  activeTab,
  onRequestCustom,
  onFillCustom,
}: {
  activeTab: MCPPageTab;
  onRequestCustom?: () => void;
  onFillCustom?: (form: MCPServerFormData, existingId?: string | null) => void;
}) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const { confirm, ConfirmDialogComponent } = useConfirm();

  const { data: servers = [], isLoading: loadingServers, refetch } = useMCPServers();
  const { data: statusList = [] } = useMCPStatus();
  const createMutation = useCreateMCPServerMutation();
  const toggleMutation = useToggleMCPServerMutation();
  const deleteMutation = useDeleteMCPServerMutation();
  const reloadMutation = useReloadMCPServersMutation();

  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<UnifiedMCPStoreItem[]>([]);
  const [sources, setSources] = useState<MCPStoreSourceInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selected, setSelected] = useState<UnifiedMCPStoreItem | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const loadStore = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listMCPStore({
        source: sourceFilter === 'all' ? undefined : sourceFilter,
        search: search || undefined,
        limit: 80,
        offset: 0,
      });
      // 去重：同一 source+id 只保留第一条，避免 React key 冲突
      const seen = new Set<string>();
      const deduped = (data.items || []).filter((it) => {
        const k = `${it.source}/${it.id}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      setItems(deduped);
      setTotal(deduped.length);
      setSources(data.sources || []);
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : t('mcpStore.loadFail'));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [sourceFilter, search]);

  // 商店 tab 或筛选变化时拉目录（用 flag 避免在 effect 里直接 setState 串扰）
  useEffect(() => {
    if (activeTab !== 'store') return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const data = await listMCPStore({
          source: sourceFilter === 'all' ? undefined : sourceFilter,
          search: search || undefined,
          limit: 80,
          offset: 0,
        });
        if (cancelled) return;
        setItems(data.items || []);
        setTotal(data.total || 0);
        setSources(data.sources || []);
      } catch (e: unknown) {
        if (cancelled) return;
        setLoadError(e instanceof Error ? e.message : t('mcpStore.loadFail'));
        setItems([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTab, sourceFilter, search]);

  const statusMap = useMemo(() => {
    const m: Record<string, MCPServerStatus> = {};
    for (const s of statusList) m[s.name] = s;
    return m;
  }, [statusList]);

  const installedNames = useMemo(() => new Set(servers.map((s) => s.name)), [servers]);

  const handleInstall = useCallback(
    async (item: UnifiedMCPStoreItem) => {
      if (installedNames.has(item.name) || installedNames.has(item.id)) {
        addToast(t('mcpStore.installedNamed').replace('{name}', item.display_name), 'info');
        return;
      }
      setBusyId(item.id);
      try {
        const res = await installMCPFromStore(item.source, item.id);
        if (res.success) {
          addToast(res.message || t('mcpStore.installedOk').replace('{name}', item.display_name), 'success');
          await refetch();
        } else {
          addToast(res.message || t('mcpStore.installFail'), 'error');
        }
      } catch (e: unknown) {
        // 回退：直接 create
        try {
          await createMutation.mutateAsync(toForm(item));
          addToast(t('mcpStore.installedOk').replace('{name}', item.display_name), 'success');
          await refetch();
        } catch (e2: unknown) {
          addToast(e2 instanceof Error ? e2.message : e instanceof Error ? e.message : t('mcpStore.installFail'), 'error');
        }
      } finally {
        setBusyId(null);
      }
    },
    [installedNames, addToast, refetch, createMutation]
  );

  const handleUninstall = useCallback(
    async (item: UnifiedMCPStoreItem) => {
      const server = servers.find((s) => s.name === item.name || s.name === item.id);
      if (!server) {
        addToast(t('mcpStore.notFoundInstalled'), 'error');
        return;
      }
      const ok = await confirm(t('mcpStore.confirmUninstall').replace('{name}', item.display_name), t('mcpStore.uninstallTitle'), 'danger');
      if (!ok) return;
      setBusyId(item.id);
      try {
        await deleteMutation.mutateAsync(server.id);
        addToast(t('mcpStore.uninstalledOk').replace('{name}', item.display_name), 'success');
        await refetch();
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : t('mcpStore.uninstallFail'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [servers, confirm, deleteMutation, addToast, refetch]
  );

  const handleReload = async () => {
    try {
      await reloadMutation.mutateAsync();
      addToast(t('mcpStore.reloaded'), 'success');
      await refetch();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('mcpStore.reloadFail'), 'error');
    }
  };

  if (activeTab === 'store') {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="mb-3 rounded-xl border border-border-subtle/70 bg-gradient-to-r from-brand-purple/5 via-transparent to-brand-cyan/5 px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-foreground">{t('mcpStore.title')}</div>
              <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-foreground-muted">
                {t('mcpStore.subtitle')}
              </p>
            </div>
            <div className="flex gap-3 text-center text-[11px]">
              <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
                <div className="font-semibold text-foreground">{total || items.length}</div>
                <div className="text-foreground-muted">{t('store.browse')}</div>
              </div>
              <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
                <div className="font-semibold text-emerald-600 dark:text-emerald-400">{servers.length}</div>
                <div className="text-foreground-muted">{t('mcpStore.installed')}</div>
              </div>
              <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
                <div className="font-semibold text-brand-purple">
                  {statusList.filter((s) => s.connected).length}
                </div>
                <div className="text-foreground-muted">{t('mcpStore.enabled')}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="sticky top-0 z-10 mb-3 space-y-2 rounded-xl border border-border-subtle/60 bg-background/90 p-2 backdrop-blur-md">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={t('mcpStore.searchPh')}
                className="w-full rounded-lg border border-border-subtle bg-elevated-bg py-1.5 pl-3 pr-8 text-sm text-foreground outline-none placeholder:text-foreground-muted/70 focus:border-brand-purple/50 focus:ring-1 focus:ring-brand-purple/20"
              />
            </div>
            <button type="button" onClick={() => void loadStore()} className={BTN_SECONDARY}>
              {loading ? t('common.loading') : t('mcpStore.refreshCatalog')}
            </button>
            <button
              type="button"
              onClick={() => void handleReload()}
              disabled={reloadMutation.isPending}
              className={BTN_SECONDARY}
            >
              {reloadMutation.isPending ? t('mcpStore.reloading') : t('mcpStore.reloadMcp')}
            </button>
            {onRequestCustom && (
              <button type="button" onClick={onRequestCustom} className={BTN_PRIMARY}>
                {t('mcpStore.customNew')}
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(['all', 'curated', 'official'] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSourceFilter(s)}
                className={`rounded-full border px-3 py-1 text-[11px] font-medium transition-colors ${
                  sourceFilter === s ? CHIP_ACTIVE : CHIP_IDLE
                }`}
                title={SOURCE_META[s].tipKey.startsWith('mcpStore.') ? t(SOURCE_META[s].tipKey as never) : SOURCE_META[s].tipKey}
              >
                {SOURCE_META[s].labelKey.startsWith('mcpStore.') || SOURCE_META[s].labelKey.startsWith('store.') ? t(SOURCE_META[s].labelKey as never) : SOURCE_META[s].labelKey}
                {sources.find((x) => x.id === s)?.count
                  ? ` (${sources.find((x) => x.id === s)!.count})`
                  : ''}
              </button>
            ))}
          </div>
          {sources.some((s) => s.error) && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
              {sources
                .filter((s) => s.error)
                .map((s) => `${s.name}: ${s.error}`)
                .join(' · ')}
            </div>
          )}
        </div>

        <div className="mb-2 text-[11px] text-foreground-muted">
          {t('store.browse') /* count */} {total}{search ? ` · "${search}"` : ''}
          {loadError ? ` · ${loadError}` : ''}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pb-4">
          {loading && items.length === 0 ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <Skeleton key={i} className="h-44 rounded-xl" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <EmptyState title={t('mcpStore.noMatch')} description={t('mcpStore.noMatchHint')} />
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {items.map((item) => (
                <StoreCard
                  key={`${item.source}/${item.id}`}
                  item={item}
                  installed={installedNames.has(item.name) || installedNames.has(item.id)}
                  busy={busyId === item.id}
                  onInstall={handleInstall}
                  onOpen={setSelected}
                  onUninstall={handleUninstall}
                />
              ))}
            </div>
          )}
        </div>

        {selected && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-[2px]"
            onClick={() => setSelected(null)}
            role="dialog"
            aria-modal="true"
          >
            <div
              className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border-subtle bg-elevated-bg p-6 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-4 flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{selected.icon}</span>
                  <div>
                    <h2 className="text-lg font-bold">{selected.display_name}</h2>
                    <p className="text-xs text-foreground-muted">
                      {(() => {
                        const m = SOURCE_META[selected.source] || SOURCE_META.curated;
                        const lab = m.labelKey.startsWith('mcpStore.') || m.labelKey.startsWith('store.') ? t(m.labelKey as never) : m.labelKey;
                        return `${lab} · ${selected.category}`;
                      })()}
                    </p>
                  </div>
                </div>
                <button type="button" onClick={() => setSelected(null)} className="text-foreground-muted">
                  ✕
                </button>
              </div>
              <p className="mb-3 text-sm leading-relaxed text-foreground">
                {selected.description || selected.summary}
              </p>
              {selected.compatibility?.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1">
                  {selected.compatibility.map((c) => (
                    <span
                      key={c}
                      className="rounded-md bg-brand-purple/10 px-2 py-0.5 text-[10px] text-brand-purple"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              <div className="mb-4 space-y-2 text-xs text-foreground-muted">
                <div>
                  Transport: <code className="text-foreground">{selected.transport}</code>
                </div>
                {selected.command && (
                  <div>
                    Command:{' '}
                    <code className="break-all text-foreground">
                      {selected.command} {joinArgs(selected.args)}
                    </code>
                  </div>
                )}
                {selected.url && (
                  <div>
                    URL: <code className="break-all text-foreground">{selected.url}</code>
                  </div>
                )}
                {selected.env_hint && (
                  <div>
                    Env: <code className="text-foreground">{selected.env_hint}</code>
                  </div>
                )}
                {selected.note && <div className="text-amber-600 dark:text-amber-400">{selected.note}</div>}
                <div className={riskClass(selected.risk_level)}>
                  {t('mcpStore.riskLabel').replace('{level}', RISK_LABEL_KEY[selected.risk_level] ? t(RISK_LABEL_KEY[selected.risk_level] as never) : selected.risk_level)}
                </div>
              </div>
              <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-4">
                {installedNames.has(selected.name) ? (
                  <button
                    type="button"
                    className="flex-1 rounded-lg bg-red-500/10 px-4 py-2 text-sm font-medium text-red-600"
                    onClick={() => {
                      void handleUninstall(selected);
                      setSelected(null);
                    }}
                  >
                    {t('mcpStore.uninstall')}
                  </button>
                ) : (
                  <button
                    type="button"
                    className={`flex-1 ${BTN_PRIMARY_LG}`}
                    disabled={!selected.installable}
                    onClick={() => {
                      void handleInstall(selected);
                      setSelected(null);
                    }}
                  >
                    {t('mcpStore.oneClick')}
                  </button>
                )}
                {onFillCustom && (
                  <button
                    type="button"
                    className={`${BTN_SECONDARY} px-4 py-2 text-sm`}
                    onClick={() => {
                      const existing = servers.find(
                        (s) => s.name === selected.name || s.name === selected.id,
                      );
                      onFillCustom(toForm(selected), existing?.id ?? null);
                      setSelected(null);
                    }}
                  >
                    {t('mcpStore.fillForm')}
                  </button>
                )}
                {selected.source_url && (
                  <a
                    href={selected.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`${BTN_SECONDARY} px-4 py-2 text-sm`}
                  >
                    Source
                  </a>
                )}
              </div>
            </div>
          </div>
        )}
        {ConfirmDialogComponent}
      </div>
    );
  }

  if (activeTab === 'installed') {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-foreground-muted">
            Configured {servers.length} · connected {statusList.filter((s) => s.connected).length}
          </p>
          <button type="button" onClick={() => void handleReload()} className={BTN_SECONDARY}>
            {reloadMutation.isPending ? t('mcpStore.reloading') : t('mcpStore.reloadAll')}
          </button>
        </div>
        {loadingServers ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28 rounded-xl" />
            ))}
          </div>
        ) : servers.length === 0 ? (
          <EmptyState title={t('mcpStore.noneInstalled')} description={t('mcpStore.noneInstalledHint')} />
        ) : (
          <div className="space-y-2">
            {servers.map((server) => {
              const st = statusMap[server.name];
              return (
                <div
                  key={server.id}
                  className="flex flex-wrap items-center gap-3 rounded-xl border border-border-subtle bg-elevated-bg/50 p-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-foreground">{server.name}</span>
                      <span className="rounded-md border border-border-subtle px-1.5 py-0.5 text-[10px] text-foreground-muted">
                        {server.transport}
                      </span>
                      <span className={`text-[10px] font-medium ${riskClass(server.risk_level)}`}>
                        {RISK_LABEL_KEY[server.risk_level] || server.risk_level}
                      </span>
                      <span
                        className={`text-[10px] ${server.enabled ? 'text-emerald-600' : 'text-foreground-dim'}`}
                      >
                        {server.enabled ? t('mcpStore.enabled') : t('mcpStore.disabled')}
                      </span>
                      {st && (
                        <span
                          className={`text-[10px] ${st.connected ? 'text-emerald-600' : 'text-rose-500'}`}
                        >
                          {st.connected ? `● Connected · ${st.tool_count} tools` : t('mcpStore.disconnected')}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-foreground-muted">
                      {server.description || t('store.noDesc')}
                    </p>
                    <code className="mt-1 block truncate text-[10px] text-foreground-dim">
                      {server.transport === 'stdio'
                        ? `${server.command || ''} ${joinArgs(server.args)}`
                        : server.url || ''}
                    </code>
                  </div>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      className={BTN_SECONDARY}
                      onClick={() =>
                        toggleMutation
                          .mutateAsync({ id: server.id, enabled: !server.enabled })
                          .then(
                            () =>
                              addToast(
                                `${server.enabled ? t('mcpStore.disabled') : t('mcpStore.enabled')} ${server.name}`,
                                'success'
                              ),
                            (e) => addToast(e?.message || t('mcpStore.failed'), 'error')
                          )
                      }
                    >
                      {server.enabled ? t('mcpStore.disable') : t('mcpStore.enable')}
                    </button>
                    <button
                      type="button"
                      className="rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-500/20"
                      onClick={async () => {
                        const ok = await confirm(`${t('mcpStore.delete')} ${server.name}?`, t('mcpStore.deleteTitle'), 'danger');
                        if (!ok) return;
                        try {
                          await deleteMutation.mutateAsync(server.id);
                          addToast(t('mcpStore.deleted'), 'success');
                          await refetch();
                        } catch (e: unknown) {
                          addToast(e instanceof Error ? e.message : t('mcpStore.deleteFail'), 'error');
                        }
                      }}
                    >
                      {t('mcpStore.delete')}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {ConfirmDialogComponent}
      </div>
    );
  }

  return null;
}
