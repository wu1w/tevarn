'use client';

import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  SkillSource,
  SkillStoreSource,
  UnifiedSkill,
  ActivePromptSkill,
  getStoreSources,
  listStoreSkills,
  installStoreSkill,
  uninstallStoreSkill,
  listInstalledStoreSkills,
  listActivePromptSkills,
  refreshStoreCache,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { Skeleton } from '@/components/desktop/Skeleton';
import { EmptyState } from '@/components/desktop/EmptyState';
import { useT } from '@/stores/localeStore';

type SourceFilter = 'all' | SkillSource;
type ViewFilter = 'browse' | 'installed' | 'active';

const PAGE_SIZE = 48;

const SOURCE_META: Record<
  string,
  { label: string; short: string; color: string; ring: string; tipKey: string }
> = {
  takton: {
    label: 'Takton',
    short: 'Tk',
    color: 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/25',
    ring: 'ring-sky-500/30',
    tipKey: 'store.src.takton',
  },
  clawhub: {
    label: 'ClawHub',
    short: 'OC',
    color: 'bg-violet-500/15 text-violet-600 dark:text-violet-400 border-violet-500/25',
    ring: 'ring-violet-500/30',
    tipKey: 'store.src.clawhub',
  },
  'awesome-claude': {
    label: 'Claude Code',
    short: 'CC',
    color: 'bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/25',
    ring: 'ring-orange-500/30',
    tipKey: 'awesome-claude-skills · SKILL.md',
  },
  'awesome-hermes': {
    label: 'Hermes',
    short: 'Hm',
    color: 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/25',
    ring: 'ring-rose-500/30',
    tipKey: 'awesome-hermes-skills · SKILL.md',
  },
  custom: {
    label: 'Custom',
    short: 'Cu',
    color: 'bg-zinc-500/15 text-zinc-600 dark:text-zinc-400 border-zinc-500/25',
    ring: 'ring-zinc-500/30',
    tipKey: 'store.src.custom',
  },
};

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
const TAB_ACTIVE = 'bg-brand-purple text-white shadow-sm shadow-brand-purple/15';
const TAB_IDLE =
  'text-foreground-muted hover:bg-card-bg-hover hover:text-foreground';

function skillKey(skill: Pick<UnifiedSkill, 'source' | 'id' | 'name'>) {
  return `${skill.source}/${skill.id || skill.name}`;
}

function formatCount(n: number): string {
  if (!n || n <= 0) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

/** 卡片 — memo 减少搜索/翻页时的重渲染 */
const SkillCard = memo(function SkillCard({
  skill,
  installed,
  busy,
  onOpen,
  onInstall,
  onUninstall,
}: {
  skill: UnifiedSkill;
  installed: boolean;
  busy: boolean;
  onOpen: (s: UnifiedSkill) => void;
  onInstall: (s: UnifiedSkill) => void;
  onUninstall: (s: UnifiedSkill) => void;
}) {
  const t = useT();
  const meta = SOURCE_META[skill.source] || SOURCE_META.custom;
  // Claude/Hermes 有 skill_md_url 可直装；ClawHub 走后端「元数据→SKILL.md」转换安装
  const canInstall =
    skill.source !== 'takton' &&
    (skill.source === 'clawhub' ||
      !!skill.skill_md_url ||
      skill.source === 'awesome-claude' ||
      skill.source === 'awesome-hermes');
  const isClawhub = skill.source === 'clawhub';
  const isTakton = skill.source === 'takton';

  return (
    <article
      className={`group relative flex flex-col rounded-xl border border-border-subtle/80 bg-gradient-to-b from-elevated-bg/80 to-elevated-bg/40 p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/35 hover:shadow-md ${
        installed ? `ring-1 ${meta.ring}` : ''
      }`}
    >
      {installed && (
        <span className="absolute right-3 top-3 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
          {t('store.installed')}
        </span>
      )}

      <div className="mb-2 flex items-start gap-2 pr-12">
        <div
          className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border text-[11px] font-bold ${meta.color}`}
          title={meta.tipKey.startsWith('store.') ? t(meta.tipKey as never) : meta.tipKey}
        >
          {meta.short}
        </div>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onOpen(skill)}
            className="block w-full truncate text-left text-sm font-semibold text-foreground transition-colors hover:text-accent"
            title={skill.display_name}
          >
            {skill.display_name}
          </button>
          <div className="mt-0.5 truncate text-[10px] text-foreground-muted">
            {skill.source}/{skill.id}
          </div>
        </div>
      </div>

      <p className="mb-3 line-clamp-2 min-h-[2.5rem] flex-1 text-xs leading-relaxed text-foreground-muted">
        {skill.summary || skill.description || t('store.noDesc')}
      </p>

      <div className="mb-2 flex flex-wrap items-center gap-1">
        <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${meta.color}`}>
          {meta.label}
        </span>
        {skill.version ? (
          <span className="rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
            v{skill.version}
          </span>
        ) : null}
        {skill.stats?.downloads > 0 ? (
          <span className="rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
            ⬇ {formatCount(skill.stats.downloads)}
          </span>
        ) : null}
        {skill.stats?.stars > 0 ? (
          <span className="rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
            ★ {formatCount(skill.stats.stars)}
          </span>
        ) : null}
        {skill.author ? (
          <span className="max-w-[7rem] truncate rounded-md bg-elevated-bg px-1.5 py-0.5 text-[10px] text-foreground-muted">
            @{skill.author}
          </span>
        ) : null}
      </div>

      {(skill.topics?.length > 0 || skill.compatibility?.length > 0) && (
        <div className="mb-3 flex flex-wrap gap-1">
          {skill.compatibility?.slice(0, 3).map((c) => (
            <span
              key={`c-${c}`}
              className="rounded-md bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-600 dark:text-blue-400"
            >
              {c}
            </span>
          ))}
          {skill.topics?.slice(0, 3).map((t) => (
            <span key={t} className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
              {t}
            </span>
          ))}
        </div>
      )}

      <div className="mt-auto flex gap-2">
        {installed ? (
          <button
            type="button"
            onClick={() => onUninstall(skill)}
            disabled={busy}
            className="flex-1 rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-500/20 disabled:opacity-50"
          >
            {busy ? t('store.busy') : t('store.uninstall')}
          </button>
        ) : isTakton ? (
          <button
            type="button"
            disabled
            title={t('store.taktonTip')}
            className="flex-1 cursor-not-allowed rounded-lg border border-border-subtle bg-elevated-bg px-3 py-1.5 text-xs font-medium text-foreground-muted opacity-80"
          >
            {t('store.useCommunity')}
          </button>
        ) : canInstall ? (
          <button
            type="button"
            onClick={() => onInstall(skill)}
            disabled={busy}
            title={
              isClawhub
                ? t('store.clawhubTip')
                : t('store.downloadTip')
            }
            className={`flex-1 ${BTN_PRIMARY}`}
          >
            {busy ? t('store.installing') : isClawhub ? t('store.convertInstall') : t('store.oneClick')}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onInstall(skill)}
            disabled={busy}
            className={`flex-1 ${BTN_PRIMARY}`}
          >
            {busy ? t('store.installing') : t('store.install')}
          </button>
        )}
        <button type="button" onClick={() => onOpen(skill)} className={BTN_SECONDARY}>
          {t('store.details')}
        </button>
      </div>
    </article>
  );
});

export default function SkillStorePanel() {
  const t = useT();
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();

  const [sources, setSources] = useState<SkillStoreSource[]>([]);
  const [skills, setSkills] = useState<UnifiedSkill[]>([]);
  const [total, setTotal] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyKeys, setBusyKeys] = useState<Set<string>>(new Set());
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [installedMeta, setInstalledMeta] = useState<
    Array<{ source: string; name: string; path: string; size: number }>
  >([]);
  const [activeSkills, setActiveSkills] = useState<ActivePromptSkill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<UnifiedSkill | null>(null);

  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [viewFilter, setViewFilter] = useState<ViewFilter>('browse');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const searchBoxRef = useRef<HTMLInputElement>(null);

  // 源列表（只拉一次）
  useEffect(() => {
    getStoreSources()
      .then(setSources)
      .catch((e) => addToast(t('store.loadSourcesFail').replace('{msg}', String(e?.message || e)), 'error'));
  }, [addToast]);

  const loadInstalled = useCallback(async () => {
    try {
      const [list, active] = await Promise.all([
        listInstalledStoreSkills(),
        listActivePromptSkills().catch(() => [] as ActivePromptSkill[]),
      ]);
      setInstalledMeta(list);
      setInstalled(new Set(list.map((s) => `${s.source}/${s.name}`)));
      setActiveSkills(active || []);
    } catch {
      // 静默
    }
  }, []);

  useEffect(() => {
    loadInstalled();
  }, [loadInstalled]);

  // 防抖搜索
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 320);
    return () => clearTimeout(t);
  }, [searchInput]);

  // 筛选变化时回到第一页
  useEffect(() => {
    setOffset(0);
  }, [sourceFilter, search, viewFilter]);

  const load = useCallback(
    async (opts?: { append?: boolean; nextOffset?: number }) => {
      const append = !!opts?.append;
      const useOffset = opts?.nextOffset ?? 0;

      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      if (append) setLoadingMore(true);
      else setLoading(true);

      try {
        const params: Parameters<typeof listStoreSkills>[0] = {
          limit: PAGE_SIZE,
          offset: useOffset,
        };
        if (sourceFilter !== 'all') params.source = sourceFilter;
        if (search) params.search = search;

        const data = await listStoreSkills(params);
        if (ac.signal.aborted) return;

        const items = data.items || [];
        setSkills((prev) => (append ? [...prev, ...items] : items));
        setTotal(data.total || 0);
        setErrors(data.errors || {});
        setOffset(useOffset + items.length);
      } catch (e: any) {
        if (ac.signal.aborted || e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') return;
        addToast(t('store.loadFail').replace('{msg}', String(e?.message || e)), 'error');
        if (!append) {
          setSkills([]);
          setErrors({});
          setTotal(0);
        }
      } finally {
        if (!ac.signal.aborted) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [sourceFilter, search, addToast]
  );

  useEffect(() => {
    if (viewFilter === 'browse') {
      load({ append: false, nextOffset: 0 });
    }
    return () => abortRef.current?.abort();
  }, [load, viewFilter]);

  // Esc 关闭详情
  useEffect(() => {
    if (!selectedSkill) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedSkill(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedSkill]);

  const setBusy = (key: string, on: boolean) => {
    setBusyKeys((prev) => {
      const n = new Set(prev);
      if (on) n.add(key);
      else n.delete(key);
      return n;
    });
  };

  const handleInstall = async (skill: UnifiedSkill) => {
    const key = skillKey(skill);
    setBusy(key, true);
    try {
      const result = await installStoreSkill(skill.source, skill.id);
      if (result.success) {
        const tip =
          skill.source === 'clawhub'
            ? t('store.convertedOk').replace('{name}', skill.display_name)
          : t('store.installedOk').replace('{name}', skill.display_name);
        addToast(tip, 'success');
        await loadInstalled();
      } else {
        addToast(t('store.installFail').replace('{msg}', String(result.error || t('store.unknownError'))), 'error');
      }
    } catch (e: any) {
      addToast(t('store.installFail').replace('{msg}', String(e?.response?.data?.detail || e?.message || e)), 'error');
    } finally {
      setBusy(key, false);
    }
  };

  const handleUninstall = async (skill: UnifiedSkill | { source: string; name: string; display_name?: string }) => {
    const display = 'display_name' in skill && skill.display_name ? skill.display_name : skill.name;
    const ok = await confirm(
      t('store.confirmUninstall').replace('{name}', display),
      t('store.uninstallTitle'),
      'danger'
    );
    if (!ok) return;
    const id = 'id' in skill ? (skill as UnifiedSkill).id : skill.name;
    const key = `${skill.source}/${id}`;
    setBusy(key, true);
    try {
      const result = await uninstallStoreSkill(skill.source as SkillSource, id);
      if (result.success) {
        addToast(t('store.uninstalledOk').replace('{name}', display), 'success');
        await loadInstalled();
      } else {
        addToast(t('store.uninstallFail').replace('{msg}', String(result.error || t('store.unknownError'))), 'error');
      }
    } catch (e: any) {
      addToast(t('store.uninstallFail').replace('{msg}', String(e?.response?.data?.detail || e?.message || e)), 'error');
    } finally {
      setBusy(key, false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshStoreCache(sourceFilter === 'all' ? undefined : sourceFilter);
      addToast(t('store.refreshed'), 'success');
      await Promise.all([load({ append: false, nextOffset: 0 }), loadInstalled()]);
    } catch (e: any) {
      addToast(t('store.refreshFail').replace('{msg}', String(e?.message || e)), 'error');
    } finally {
      setRefreshing(false);
    }
  };

  const handleCopyCmd = async (cmd: string) => {
    const ok = await copyText(cmd);
    addToast(ok ? t('store.copiedCmd') : t('store.copyFail'), ok ? 'success' : 'error');
  };

  const isInstalled = useCallback(
    (skill: UnifiedSkill) =>
      installed.has(`${skill.source}/${skill.id}`) || installed.has(`${skill.source}/${skill.name}`),
    [installed]
  );

  const errorEntries = useMemo(() => Object.entries(errors), [errors]);
  const hasMore = viewFilter === 'browse' && skills.length < total;

  const filteredBrowse = skills; // 服务端已筛
  const installedView = useMemo(() => {
    // 用 active 优先（有 description），否则 installedMeta
    const byKey = new Map<string, ActivePromptSkill | (typeof installedMeta)[0]>();
    for (const m of installedMeta) byKey.set(`${m.source}/${m.name}`, m);
    for (const a of activeSkills) byKey.set(`${a.source}/${a.name}`, a);
    let list = Array.from(byKey.values());
    if (sourceFilter !== 'all') list = list.filter((s) => s.source === sourceFilter);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter((s) => {
        const name = ('display_name' in s ? s.display_name : s.name) || s.name;
        const desc = 'description' in s ? s.description || '' : '';
        return (
          name.toLowerCase().includes(q) ||
          s.name.toLowerCase().includes(q) ||
          s.source.toLowerCase().includes(q) ||
          desc.toLowerCase().includes(q)
        );
      });
    }
    return list;
  }, [installedMeta, activeSkills, sourceFilter, search]);

  const activeView = useMemo(() => {
    let list = activeSkills;
    if (sourceFilter !== 'all') list = list.filter((s) => s.source === sourceFilter);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (s) =>
          s.display_name.toLowerCase().includes(q) ||
          s.name.toLowerCase().includes(q) ||
          (s.description || '').toLowerCase().includes(q)
      );
    }
    return list;
  }, [activeSkills, sourceFilter, search]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶栏：说明 + 统计 */}
      <div className="mb-3 rounded-xl border border-border-subtle/70 bg-gradient-to-r from-accent/5 via-transparent to-violet-500/5 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-foreground">{t('store.title')}</div>
            <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-foreground-muted">
              {t('store.subtitle')}
            </p>
          </div>
          <div className="flex gap-3 text-center text-[11px]">
            <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
              <div className="font-semibold text-foreground">{total || '—'}</div>
              <div className="text-foreground-muted">{t('store.browse')}</div>
            </div>
            <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
              <div className="font-semibold text-emerald-600 dark:text-emerald-400">
                {installedMeta.length}
              </div>
              <div className="text-foreground-muted">{t('store.installed')}</div>
            </div>
            <div className="rounded-lg bg-elevated-bg/80 px-3 py-1.5">
              <div className="font-semibold text-accent">{activeSkills.length}</div>
              <div className="text-foreground-muted">{t('store.currentlyInjected')}</div>
            </div>
          </div>
        </div>
      </div>

      {/* 工具条 sticky */}
      <div className="sticky top-0 z-10 mb-3 space-y-2 rounded-xl border border-border-subtle/60 bg-background/90 p-2 backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-2">
          {/* 视图切换 — 避免 bg-accent/text-white 在浅色主题下变成死白 */}
          <div className="flex rounded-lg border border-border-subtle bg-elevated-bg/80 p-0.5">
            {(
              [
                { id: 'browse' as const, label: t('store.browse') },
                { id: 'installed' as const, label: `${t('store.tab.installed')} (${installedMeta.length})` },
                { id: 'active' as const, label: `${t('store.currentlyInjected')} (${activeSkills.length})` },
              ] as const
            ).map((v) => (
              <button
                key={v.id}
                type="button"
                onClick={() => setViewFilter(v.id)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  viewFilter === v.id ? TAB_ACTIVE : TAB_IDLE
                }`}
              >
                {v.label}
              </button>
            ))}
          </div>

          <div className="relative min-w-[200px] flex-1">
            <input
              ref={searchBoxRef}
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={t('store.searchPh')}
            className="w-full rounded-lg border border-border-subtle bg-elevated-bg py-1.5 pl-3 pr-8 text-sm text-foreground outline-none transition-colors placeholder:text-foreground-muted/70 focus:border-brand-purple/50 focus:ring-1 focus:ring-brand-purple/20"
            />
            {searchInput && (
              <button
                type="button"
                onClick={() => setSearchInput('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-foreground-muted hover:text-foreground"
                aria-label={t('store.clearSearch')}
              >
                ✕
              </button>
            )}
          </div>

          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-lg border border-border-subtle bg-elevated-bg px-3 py-1.5 text-xs text-foreground-muted transition-colors hover:border-brand-purple/40 hover:text-foreground disabled:opacity-50"
            title={t('store.refresh')}
          >
            {refreshing ? t('common.loading') : t('store.refresh')}
          </button>
        </div>

        {/* 源筛选 chips — 选中态用柔和描边，避免死黑块 */}
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setSourceFilter('all')}
            className={`rounded-full border px-3 py-1 text-[11px] font-medium transition-colors ${
              sourceFilter === 'all' ? CHIP_ACTIVE : CHIP_IDLE
            }`}
          >
                      {t('store.allSources')}
                    </button>
          {sources.map((s) => {
            const meta = SOURCE_META[s.id] || SOURCE_META.custom;
            const active = sourceFilter === s.id;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setSourceFilter(s.id)}
                title={meta.tipKey.startsWith('store.') ? t(meta.tipKey as never) : meta.tipKey}
                className={`rounded-full border px-3 py-1 text-[11px] font-medium transition-colors ${
                  active
                    ? `${meta.color} ring-1 ${meta.ring}`
                    : CHIP_IDLE
                }`}
              >
                {s.display_name || meta.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 源错误降级 */}
      {errorEntries.length > 0 && viewFilter === 'browse' && (
        <div className="mb-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          {errorEntries.map(([src, err]) => (
            <div key={src}>
              Source <b>{src}</b> unavailable: {err}
            </div>
          ))}
        </div>
      )}

      {/* 内容区 */}
      <div className="min-h-0 flex-1 overflow-y-auto pb-4">
        {viewFilter === 'browse' && (
          <>
            <div className="mb-2 flex items-center justify-between text-[11px] text-foreground-muted">
              <span>
                Total {total}
                {search ? ` · "${search}"` : ''}
                {sourceFilter !== 'all' ? ` · ${sourceFilter}` : ''}
              </span>
              {loading && <span className="animate-pulse">{t('common.loading')}</span>}
            </div>

            {loading && skills.length === 0 ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-44 rounded-xl" />
                ))}
              </div>
            ) : filteredBrowse.length === 0 ? (
              <EmptyState
                title={t('store.noneFound')}
                description={search ? t('store.noneFoundHint') : t('store.sourceEmpty')}
              />
            ) : (
              <>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {filteredBrowse.map((skill) => {
                    const key = skillKey(skill);
                    return (
                      <SkillCard
                        key={key}
                        skill={skill}
                        installed={isInstalled(skill)}
                        busy={busyKeys.has(key)}
                        onOpen={setSelectedSkill}
                        onInstall={handleInstall}
                        onUninstall={handleUninstall}
                      />
                    );
                  })}
                </div>
                {hasMore && (
                  <div className="mt-4 flex justify-center">
                    <button
                      type="button"
                      disabled={loadingMore}
                      onClick={() => load({ append: true, nextOffset: offset })}
                      className="rounded-lg border border-border-subtle bg-elevated-bg px-5 py-2 text-xs font-medium text-foreground-muted transition-colors hover:border-accent/40 hover:text-foreground disabled:opacity-50"
                    >
                      {loadingMore ? t('common.loading') : `{t('store.loadMore').replace('{shown}', String(skills.length)).replace('{total}', String(total))}`}
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {viewFilter === 'installed' && (
          <>
            {installedView.length === 0 ? (
              <EmptyState
                title={t('store.noneInstalled')}
                description={t('store.noneInstalledHint')}
              />
            ) : (
              <div className="space-y-2">
                {installedView.map((s) => {
                  const meta = SOURCE_META[s.source] || SOURCE_META.custom;
                  const title = 'display_name' in s && s.display_name ? s.display_name : s.name;
                  const desc = 'description' in s ? s.description : '';
                  const key = `${s.source}/${s.name}`;
                  return (
                    <div
                      key={key}
                      className="flex flex-wrap items-center gap-3 rounded-xl border border-border-subtle bg-elevated-bg/50 p-3"
                    >
                      <span className={`rounded-md border px-2 py-0.5 text-[10px] font-medium ${meta.color}`}>
                        {meta.label}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-foreground">{title}</div>
                        <div className="truncate text-[11px] text-foreground-muted">
                          {s.path || `${s.source}/${s.name}`}
                          {'size' in s && s.size ? ` · ${(s.size / 1024).toFixed(1)} KB` : ''}
                        </div>
                        {desc ? (
                          <p className="mt-1 line-clamp-1 text-xs text-foreground-muted">{desc}</p>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          handleUninstall({
                            source: s.source,
                            name: s.name,
                            display_name: title,
                          })
                        }
                        disabled={busyKeys.has(key)}
                        className="rounded-lg bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-500 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {t('store.uninstall')}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {viewFilter === 'active' && (
          <>
            <p className="mb-3 text-xs text-foreground-muted">
                          These skills inject a summary + path into context. Use file_read on the path for full steps.
                        </p>
            {activeView.length === 0 ? (
              <EmptyState
                title={t('store.noneInstalled')}
                description={t('store.noneInstalledHint')}
              />
            ) : (
              <div className="space-y-2">
                {activeView.map((s) => {
                  const meta = SOURCE_META[s.source] || SOURCE_META.custom;
                  return (
                    <div
                      key={`${s.source}/${s.name}`}
                      className="rounded-xl border border-accent/20 bg-accent/5 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-md border px-2 py-0.5 text-[10px] font-medium ${meta.color}`}>
                          {meta.label}
                        </span>
                        <span className="text-sm font-semibold text-foreground">{s.display_name}</span>
                        <span className="text-[11px] text-foreground-muted">
                          {s.source}/{s.name}
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-foreground-muted">
                        {s.description || t('store.noDesc')}
                      </p>
                      <code className="mt-2 block truncate rounded-md bg-elevated-bg px-2 py-1 text-[10px] text-foreground-muted">
                        {s.path}
                      </code>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>

      {/* 详情 Modal */}
      {selectedSkill && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-[2px]"
          onClick={() => setSelectedSkill(null)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border-subtle bg-background p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-bold text-foreground">{selectedSkill.display_name}</h2>
                <p className="mt-1 text-xs text-foreground-muted">
                  {selectedSkill.source} / {selectedSkill.id}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedSkill(null)}
                className="rounded-lg px-2 py-1 text-foreground-muted hover:bg-elevated-bg hover:text-foreground"
                aria-label={t('store.close')}
              >
                ✕
              </button>
            </div>

            <div className="space-y-4">
              {selectedSkill.summary && (
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                                      Summary
                                    </div>
                  <p className="mt-1 text-sm leading-relaxed text-foreground">{selectedSkill.summary}</p>
                </div>
              )}
              {selectedSkill.description &&
                selectedSkill.description !== selectedSkill.summary && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">
                                          Description
                                        </div>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {selectedSkill.description}
                    </p>
                  </div>
                )}

              <div className="grid grid-cols-2 gap-3 text-sm">
                {selectedSkill.author && (
                  <div>
                    <div className="text-xs text-foreground-muted">Author</div>
                    <div>@{selectedSkill.author}</div>
                  </div>
                )}
                {selectedSkill.version && (
                  <div>
                    <div className="text-xs text-foreground-muted">Version</div>
                    <div>{selectedSkill.version}</div>
                  </div>
                )}
                {selectedSkill.license && (
                  <div>
                    <div className="text-xs text-foreground-muted">License</div>
                    <div>{selectedSkill.license}</div>
                  </div>
                )}
                {selectedSkill.source_repo && (
                  <div>
                    <div className="text-xs text-foreground-muted">Repo</div>
                    <div className="truncate">{selectedSkill.source_repo}</div>
                  </div>
                )}
              </div>

              {selectedSkill.compatibility?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-foreground-muted">{t('store.compat')}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {selectedSkill.compatibility.map((c) => (
                      <span
                        key={c}
                        className="rounded-md bg-blue-500/10 px-2 py-0.5 text-xs text-blue-600 dark:text-blue-400"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {selectedSkill.topics?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-foreground-muted">Topics</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {selectedSkill.topics.map((t) => (
                      <span key={t} className="rounded-md bg-accent/10 px-2 py-0.5 text-xs text-accent">
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {selectedSkill.install_command && (
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <div className="text-xs font-semibold text-foreground-muted">
                      Native CLI (optional, advanced)
                    </div>
                    <button
                      type="button"
                      onClick={() => handleCopyCmd(selectedSkill.install_command)}
                      className="text-[11px] text-violet-600 hover:underline dark:text-violet-400"
                    >
                                          Copy
                                        </button>
                  </div>
                  <code className="block rounded-lg bg-elevated-bg p-2 text-xs text-foreground">
                    {selectedSkill.install_command}
                  </code>
                  {selectedSkill.source === 'clawhub' && (
                    <p className="mt-1 text-[11px] text-foreground-muted">
                      Beginners: use Convert & install below — no CLI needed.
                    </p>
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-4">
                {isInstalled(selectedSkill) ? (
                  <button
                    type="button"
                    onClick={() => {
                      handleUninstall(selectedSkill);
                      setSelectedSkill(null);
                    }}
                    className="flex-1 rounded-lg bg-red-500/10 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-500/20"
                  >
                    {t('store.uninstall')}
                  </button>
                ) : selectedSkill.source === 'takton' ? (
                  <button
                    type="button"
                    disabled
                    className="flex-1 cursor-not-allowed rounded-lg border border-border-subtle bg-elevated-bg px-4 py-2 text-sm text-foreground-muted opacity-80"
                  >
                    {t('store.useCommunity')}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      handleInstall(selectedSkill);
                      setSelectedSkill(null);
                    }}
                    className="flex-1 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500"
                  >
                    {selectedSkill.source === 'clawhub' ? t('store.convertInstall') : t('store.installInject')}
                  </button>
                )}
                {selectedSkill.source_url && (
                  <a
                    href={selectedSkill.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-border-subtle bg-elevated-bg px-4 py-2 text-sm text-foreground-muted hover:border-violet-400/40"
                  >
                    Source ↗
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {ConfirmDialogComponent}
    </div>
  );
}
