'use client';

import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { WikiEntity, WikiRelation } from '@/types';
import { getWikiGraph, createWikiEntity, updateWikiEntity, deleteWikiEntity, createWikiRelation, deleteWikiRelation, importWiki, previewWikiImport } from '@/lib/api';
import { useT, t as tFn } from '@/stores/localeStore';

/** 由导出的 t 反推出的合法 key 类型，用于动态拼接 key 的精确断言 */
type LocaleKey = Parameters<typeof tFn>[0];

/** 从 axios/任意错误中安全提取可读消息（unknown 收窄，避免 any） */
const errMsg = (e: unknown): string | undefined => {
  if (e && typeof e === 'object' && 'response' in e) {
    const detail = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return undefined;
};

/** 导入预览返回的草稿实体/关系（尚未入库，字段为部分） */
interface PreviewEntity {
  name: string;
  entity_type: string;
  description?: string;
  aliases?: string[];
}
interface PreviewRelation {
  source?: string;
  target?: string;
  relation_type?: string;
}

const ENTITY_TYPES = [
  'person', 'organization', 'project', 'tech', 'concept',
  'docs', 'event', 'location', 'problem', 'solution',
];

const RELATION_TYPES = [
  'depends_on', 'part_of', 'uses', 'solves', 'related_to',
  'alternative_to', 'belongs_to', 'participates_in', 'authored_by', 'presents',
];

const TYPE_ICON_COLORS: Record<string, string> = {
  person: '#22c55e', organization: '#ef4444', project: '#f97316', tech: '#06b6d4',
  concept: '#3b82f6', docs: '#a855f7', event: '#eab308', location: '#14b8a6',
  problem: '#f43f5e', solution: '#10b981',
};

/** 各实体类型的 emoji 图标（列表行内标识用） */
const TYPE_EMOJI: Record<string, string> = {
  person: '👤', organization: '🏢', project: '📁', tech: '⚙️', concept: '💡',
  docs: '📄', event: '📅', location: '📍', problem: '⚠️', solution: '✅',
};

const TYPE_COLORS: Record<string, string> = {
  person: 'bg-green-100 text-green-800 border-green-200',
  organization: 'bg-red-100 text-red-800 border-red-200',
  project: 'bg-orange-100 text-orange-800 border-orange-200',
  tech: 'bg-cyan-100 text-cyan-800 border-cyan-200',
  concept: 'bg-blue-100 text-blue-800 border-blue-200',
  docs: 'bg-purple-100 text-purple-800 border-purple-200',
  event: 'bg-yellow-100 text-yellow-800 border-yellow-200',
  location: 'bg-teal-100 text-teal-800 border-teal-200',
  problem: 'bg-rose-100 text-rose-800 border-rose-200',
  solution: 'bg-emerald-100 text-emerald-800 border-emerald-200',
};

export default function WikiExplorer() {
  const t = useT();
  const [entities, setEntities] = useState<WikiEntity[]>([]);
  const [relations, setRelations] = useState<WikiRelation[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [showEntityForm, setShowEntityForm] = useState(false);
  const [showRelationForm, setShowRelationForm] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [importSource, setImportSource] = useState<'text' | 'json' | 'context'>('text');
  const [importContent, setImportContent] = useState('');
  const [preview, setPreview] = useState<{ entities: PreviewEntity[]; relations: PreviewRelation[] } | null>(null);
  const [importing, setImporting] = useState(false);
  const [editingEntity, setEditingEntity] = useState<WikiEntity | null>(null);
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState('concept');
  const [formDesc, setFormDesc] = useState('');
  const [formAliases, setFormAliases] = useState('');
  const [relSource, setRelSource] = useState('');
  const [relTarget, setRelTarget] = useState('');
  const [relType, setRelType] = useState('related_to');
  const [relWeight, setRelWeight] = useState(1);

  // 语义化标签访问器（动态 key，断言为精确 key 类型）
  const typeLabel = useCallback((type: string) => t(`wiki.type.${type}` as LocaleKey) || type, [t]);
  const relLabel = useCallback((rel: string) => t(`wiki.rel.${rel}` as LocaleKey) || rel, [t]);

  const addToast = useCallback((msg: string, type: 'success' | 'error') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getWikiGraph();
      setEntities(data.entities || []);
      setRelations(data.relations || []);
    } catch {
      addToast(t('wiki.msg.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast, t]);

  // 首屏挂载加载数据（loadData 内含 setLoading/setEntities，属数据获取的标准 effect 用法）
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadData(); }, [loadData]);

  const entityMap = useMemo(() => {
    const map = new Map<string, WikiEntity>();
    entities.forEach((e) => map.set(e.id, e));
    return map;
  }, [entities]);

  /** 每个实体的关系数（列表显示用） */
  const degreeMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of relations) {
      map.set(r.source_id, (map.get(r.source_id) || 0) + 1);
      map.set(r.target_id, (map.get(r.target_id) || 0) + 1);
    }
    return map;
  }, [relations]);

  const filteredEntities = useMemo(() => {
    const q = search.toLowerCase();
    return entities.filter((e) => {
      const matchesSearch = !q
        || e.name.toLowerCase().includes(q)
        || (e.description || '').toLowerCase().includes(q)
        || (e.aliases || []).some((a) => a.toLowerCase().includes(q));
      const matchesType = filterTypes.size === 0 || filterTypes.has(e.entity_type);
      return matchesSearch && matchesType;
    });
  }, [entities, search, filterTypes]);

  /** 列表视图：按类型分组（保持 ENTITY_TYPES 顺序，未分类排最后） */
  const groupedEntities = useMemo(() => {
    const groups = new Map<string, WikiEntity[]>();
    for (const e of filteredEntities) {
      if (!groups.has(e.entity_type)) groups.set(e.entity_type, []);
      groups.get(e.entity_type)!.push(e);
    }
    const ordered: Array<{ type: string; items: WikiEntity[] }> = [];
    for (const type of ENTITY_TYPES) {
      if (groups.has(type)) ordered.push({ type, items: groups.get(type)! });
    }
    for (const [type, items] of groups) {
      if (!ENTITY_TYPES.includes(type)) ordered.push({ type, items });
    }
    // 每组内按名称排序
    ordered.forEach((g) => g.items.sort((a, b) => a.name.localeCompare(b.name, 'zh')));
    return ordered;
  }, [filteredEntities]);

  const selectedEntity = selectedId ? entityMap.get(selectedId) || null : null;
  const entityRelations = useMemo(() => {
    if (!selectedEntity) return { outgoing: [] as WikiRelation[], incoming: [] as WikiRelation[] };
    return {
      outgoing: relations.filter((r) => r.source_id === selectedEntity.id),
      incoming: relations.filter((r) => r.target_id === selectedEntity.id),
    };
  }, [relations, selectedEntity]);

  const resetForm = () => {
    setFormName('');
    setFormType('concept');
    setFormDesc('');
    setFormAliases('');
  };

  const openCreateEntity = () => {
    setEditingEntity(null);
    resetForm();
    setShowEntityForm(true);
  };

  const openEditEntity = (entity: WikiEntity) => {
    setEditingEntity(entity);
    setFormName(entity.name);
    setFormType(entity.entity_type);
    setFormDesc(entity.description || '');
    setFormAliases((entity.aliases || []).join(', '));
    setShowEntityForm(true);
  };

  const handleSaveEntity = async () => {
    if (!formName.trim()) return;
    try {
      if (editingEntity) {
        await updateWikiEntity(editingEntity.id, {
          name: formName.trim(),
          entity_type: formType,
          description: formDesc.trim(),
          aliases: formAliases.split(',').map((s) => s.trim()).filter(Boolean),
        });
        addToast(t('wiki.msg.entityUpdated'), 'success');
      } else {
        await createWikiEntity({
          name: formName.trim(),
          entity_type: formType,
          description: formDesc.trim(),
          aliases: formAliases.split(',').map((s) => s.trim()).filter(Boolean),
        });
        addToast(t('wiki.msg.entityCreated'), 'success');
      }
      setShowEntityForm(false);
      resetForm();
      loadData();
    } catch (e: unknown) {
      addToast(errMsg(e) || t('wiki.msg.saveFailed'), 'error');
    }
  };

  const handleDeleteEntity = async (id: string) => {
    const entity = entityMap.get(id);
    const name = entity?.name || id.slice(0, 8);
    if (!window.confirm(t('wiki.msg.confirmDeleteEntity').replace('{name}', name))) return;
    try {
      await deleteWikiEntity(id);
      addToast(t('wiki.msg.entityDeleted'), 'success');
      if (selectedId === id) setSelectedId(null);
      loadData();
    } catch {
      addToast(t('wiki.msg.deleteFailed'), 'error');
    }
  };

  const handleCreateRelation = async () => {
    if (!relSource || !relTarget) return;
    try {
      await createWikiRelation({ source_id: relSource, target_id: relTarget, relation_type: relType, weight: relWeight });
      addToast(t('wiki.msg.relationCreated'), 'success');
      setShowRelationForm(false);
      setRelSource('');
      setRelTarget('');
      setRelWeight(1);
      loadData();
    } catch (e: unknown) {
      addToast(errMsg(e) || t('wiki.msg.createRelFailed'), 'error');
    }
  };

  const handleDeleteRelation = async (id: string) => {
    try {
      await deleteWikiRelation(id);
      addToast(t('wiki.msg.relationDeleted'), 'success');
      loadData();
    } catch {
      addToast(t('wiki.msg.deleteRelFailed'), 'error');
    }
  };

  const handlePreview = async () => {
    if (!importContent.trim()) {
      addToast(t('wiki.msg.emptyImport'), 'error');
      return;
    }
    try {
      setImporting(true);
      const p = await previewWikiImport({ source: importSource, content: importContent });
      setPreview(p);
    } catch (e: unknown) {
      addToast(errMsg(e) || t('wiki.msg.previewFailed'), 'error');
    } finally {
      setImporting(false);
    }
  };

  const handleImport = async () => {
    if (!importContent.trim()) {
      addToast(t('wiki.msg.emptyImport'), 'error');
      return;
    }
    setImporting(true);
    try {
      const result = await importWiki({ source: importSource, content: importContent, options: { dry_run: false, update_existing: true } });
      addToast(
        t('wiki.msg.importDone')
          .replace('{c}', String(result.entities_created))
          .replace('{u}', String(result.entities_updated))
          .replace('{r}', String(result.relations_created)),
        'success'
      );
      setShowImportDialog(false);
      setImportContent('');
      setPreview(null);
      loadData();
    } catch (e: unknown) {
      addToast(errMsg(e) || t('wiki.msg.importFailed'), 'error');
    } finally {
      setImporting(false);
    }
  };

  const toggleTypeFilter = (type: string) => {
    const next = new Set(filterTypes);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    setFilterTypes(next);
  };

  const renderEntityDetail = () => {
    if (!selectedEntity) return null;
    const { outgoing, incoming } = entityRelations;
    return (
      <div className="rounded-xl border border-border-default bg-card-bg p-5 shadow-sm">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-foreground">{selectedEntity.name}</h3>
            <span className={`mt-1 inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${TYPE_COLORS[selectedEntity.entity_type] || 'bg-gray-100 text-gray-800'}`}>
              {typeLabel(selectedEntity.entity_type)}
            </span>
          </div>
          <div className="flex gap-1">
            <button
              onClick={() => openEditEntity(selectedEntity)}
              className="rounded-lg p-1.5 text-foreground-dim hover:bg-gray-100 transition-colors"
              title={t('wiki.detail.editEntity')}
            >
              📝
            </button>
            <button
              onClick={() => handleDeleteEntity(selectedEntity.id)}
              className="rounded-lg p-1.5 text-foreground-dim hover:bg-red-50 hover:text-red-500 transition-colors"
              title={t('wiki.detail.deleteEntity')}
            >
              🗑️
            </button>
          </div>
        </div>
        {selectedEntity.description && (
          <p className="mb-4 text-sm text-foreground-dim leading-relaxed">{selectedEntity.description}</p>
        )}
        {selectedEntity.aliases && selectedEntity.aliases.length > 0 && (
          <div className="mb-4">
            <span className="text-xs font-medium text-foreground-dim">{t('wiki.detail.aliases')}：</span>
            <div className="mt-1 flex flex-wrap gap-1">
              {selectedEntity.aliases.map((a: string, i: number) => (
                <span key={i} className="rounded-md bg-gray-100 px-2 py-0.5 text-xs text-foreground-dim">{a}</span>
              ))}
            </div>
          </div>
        )}
        {outgoing.length > 0 && (
          <div className="mb-3">
            <span className="text-xs font-medium text-foreground-dim">{t('wiki.detail.outgoing')}：</span>
            <div className="mt-1 space-y-1">
              {outgoing.map((r) => {
                const target = entityMap.get(r.target_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{relLabel(r.relation_type)}</span>
                    <span className="text-foreground">→</span>
                    <span className="font-medium text-foreground">{target?.name || r.target_id.slice(0, 8)}</span>
                    <button onClick={() => handleDeleteRelation(r.id)} className="ml-auto text-foreground-dim hover:text-red-500">✕</button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {incoming.length > 0 && (
          <div>
            <span className="text-xs font-medium text-foreground-dim">{t('wiki.detail.incoming')}：</span>
            <div className="mt-1 space-y-1">
              {incoming.map((r) => {
                const source = entityMap.get(r.source_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="font-medium text-foreground">{source?.name || r.source_id.slice(0, 8)}</span>
                    <span className="text-foreground">→</span>
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{relLabel(r.relation_type)}</span>
                    <button onClick={() => handleDeleteRelation(r.id)} className="ml-auto text-foreground-dim hover:text-red-500">✕</button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {outgoing.length === 0 && incoming.length === 0 && (
          <p className="text-xs text-foreground-dim italic">{t('wiki.detail.noRelations')}</p>
        )}
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      {toast && (
        <div className={`fixed right-4 top-4 z-50 rounded-lg px-4 py-2.5 text-sm font-medium shadow-lg ${
          toast.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
        }`}>
          {toast.msg}
        </div>
      )}

      {/* 顶部工具栏 */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border-default bg-page-bg px-5 py-3">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <svg className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('wiki.toolbar.searchPlaceholder')}
            className="w-full rounded-lg border border-border-default bg-card-bg py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-foreground-dim/50 focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple/30"
          />
        </div>

        <div className="flex flex-wrap gap-1.5">
          {ENTITY_TYPES.map((type) => (
            <button
              key={type}
              onClick={() => toggleTypeFilter(type)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                filterTypes.has(type)
                  ? TYPE_COLORS[type] + ' ring-1 ring-offset-1'
                  : 'border-border-default text-foreground-dim hover:border-border-focus hover:text-foreground'
              }`}
            >
              {typeLabel(type)}
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          <button onClick={openCreateEntity} className="rounded-lg bg-brand-purple px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-purple/90 transition-colors">+ {t('wiki.toolbar.newEntity')}</button>
          <button onClick={() => setShowRelationForm(true)} className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors">+ {t('wiki.toolbar.newRelation')}</button>
          <button onClick={() => { setImportContent(''); setPreview(null); setShowImportDialog(true); }} className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors">📥 {t('wiki.toolbar.import')}</button>
        </div>
      </div>

      {/* 主区域 */}
      <div className="flex flex-1 gap-4 overflow-hidden p-5">
        <div className="flex-1 overflow-hidden rounded-xl border border-border-default bg-card-bg">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-foreground-dim">{t('profile.loading')}</div>
          ) : (
            /* ── 实体列表（按类型分组）── */
            <div className="h-full overflow-y-auto">
              {filteredEntities.length === 0 ? (
                <div className="flex h-40 items-center justify-center text-sm text-foreground-dim">
                  {entities.length === 0 ? t('wiki.list.empty') : t('wiki.list.noMatch')}
                </div>
              ) : (
                <>
                  <div className="sticky top-0 z-10 border-b border-border-subtle bg-card-bg px-4 py-2 text-xs text-foreground-dim">
                    {t('wiki.list.count').replace('{n}', String(filteredEntities.length))}
                  </div>
                  {groupedEntities.map((group) => (
                    <div key={group.type}>
                      <div className="flex items-center gap-2 bg-elevated-bg/40 px-4 py-2 border-b border-border-subtle">
                        <span>{TYPE_EMOJI[group.type] || '🏷️'}</span>
                        <span className="text-xs font-semibold text-foreground">{typeLabel(group.type)}</span>
                        <span className="text-[10px] text-foreground-dim">{group.items.length}</span>
                      </div>
                      <div className="divide-y divide-border-subtle">
                        {group.items.map((e) => (
                          <div
                            key={e.id}
                            role="button"
                            tabIndex={0}
                            onClick={() => setSelectedId(e.id)}
                            onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') setSelectedId(e.id); }}
                            className={`group flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-gray-50 ${selectedId === e.id ? 'bg-brand-purple/5' : ''}`}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-foreground truncate">{e.name}</span>
                                {(degreeMap.get(e.id) || 0) > 0 && (
                                  <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-foreground-dim">
                                    {degreeMap.get(e.id)} {t('wiki.detail.relationCount')}
                                  </span>
                                )}
                              </div>
                              {e.description && <p className="mt-0.5 truncate text-xs text-foreground-dim">{e.description}</p>}
                              {e.aliases && e.aliases.length > 0 && (
                                <p className="mt-0.5 truncate text-[11px] text-foreground-dim/70">
                                  {t('wiki.detail.aliases')}: {e.aliases.join(', ')}
                                </p>
                              )}
                            </div>
                            {/* 行内快捷操作（hover 显示，避免误触） */}
                            <div className="flex shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                              <button
                                onClick={(ev) => { ev.stopPropagation(); openEditEntity(e); }}
                                className="rounded-lg p-1.5 text-foreground-dim hover:bg-gray-200 transition-colors"
                                title={t('wiki.detail.editEntity')}
                              >
                                📝
                              </button>
                              <button
                                onClick={(ev) => { ev.stopPropagation(); handleDeleteEntity(e.id); }}
                                className="rounded-lg p-1.5 text-foreground-dim hover:bg-red-50 hover:text-red-500 transition-colors"
                                title={t('wiki.detail.deleteEntity')}
                              >
                                🗑️
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>

        {/* 右侧详情面板 */}
        <div className="w-80 shrink-0 overflow-y-auto">
          {selectedEntity ? renderEntityDetail() : (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border-default p-8 text-center">
              <div>
                <svg className="mx-auto mb-3 h-10 w-10 text-foreground-dim/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
                <p className="text-sm text-foreground-dim">{t('wiki.detail.empty')}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 实体表单 */}
      {showEntityForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowEntityForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">{editingEntity ? t('wiki.form.editTitle') : t('wiki.form.createTitle')}</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.form.name')}</label>
                <input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder={t('wiki.form.namePlaceholder')} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.form.type')}</label>
                <select value={formType} onChange={(e) => setFormType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {ENTITY_TYPES.map((type) => <option key={type} value={type}>{typeLabel(type)}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.form.desc')}</label>
                <textarea value={formDesc} onChange={(e) => setFormDesc(e.target.value)} placeholder={t('wiki.form.descPlaceholder')} rows={3} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.form.aliases')}</label>
                <input value={formAliases} onChange={(e) => setFormAliases(e.target.value)} placeholder={t('wiki.form.aliasesPlaceholder')} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowEntityForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">{t('wiki.form.cancel')}</button>
                <button onClick={handleSaveEntity} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('wiki.form.save')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 关系表单 */}
      {showRelationForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowRelationForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">{t('wiki.relForm.title')}</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.relForm.source')}</label>
                <select value={relSource} onChange={(e) => setRelSource(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">{t('wiki.relForm.selectEntity')}</option>
                  {entities.map((e) => <option key={e.id} value={e.id}>{e.name} ({typeLabel(e.entity_type)})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.relForm.target')}</label>
                <select value={relTarget} onChange={(e) => setRelTarget(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">{t('wiki.relForm.selectEntity')}</option>
                  {entities.filter((e) => e.id !== relSource).map((e) => <option key={e.id} value={e.id}>{e.name} ({typeLabel(e.entity_type)})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.relForm.type')}</label>
                <select value={relType} onChange={(e) => setRelType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {RELATION_TYPES.map((rel) => <option key={rel} value={rel}>{relLabel(rel)}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki.relForm.weight')}</label>
                <input type="number" value={relWeight} min={0} max={1} step={0.1} onChange={(e) => setRelWeight(Number(e.target.value))} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowRelationForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">{t('wiki.form.cancel')}</button>
                <button onClick={handleCreateRelation} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('wiki.relForm.create')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 导入对话框 */}
      {showImportDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowImportDialog(false)}>
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">📥 {t('wiki.import.title')}</h3>
            <div className="space-y-4">
              <div className="flex gap-2">
                {(['text', 'json', 'context'] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setImportSource(s)}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
                      importSource === s ? 'bg-brand-purple text-white border-brand-purple' : 'border-border-default text-foreground-dim hover:bg-gray-50'
                    }`}
                  >
                    {s === 'text' ? t('wiki.import.sourceText') : s === 'json' ? 'JSON' : t('wiki.import.sourceContext')}
                  </button>
                ))}
              </div>
              <textarea
                value={importContent}
                onChange={(e) => setImportContent(e.target.value)}
                placeholder={
                  importSource === 'context'
                    ? t('wiki.import.contextHint')
                    : importSource === 'json'
                    ? t('wiki.import.placeholderJson')
                    : t('wiki.import.placeholderText')
                }
                rows={6}
                disabled={importSource === 'context'}
                className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none disabled:bg-gray-50"
              />
              <div className="flex gap-2">
                <button onClick={handlePreview} disabled={importing} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50 disabled:opacity-50">{importing ? t('wiki.import.previewing') : '🔍 ' + t('wiki.import.preview')}</button>
                <button onClick={handleImport} disabled={importing} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90 disabled:opacity-50">{importing ? t('wiki.import.importing') : t('wiki.import.confirm')}</button>
              </div>

              {preview && (
                <div className="rounded-lg border border-border-default bg-elevated-bg/40 p-4">
                  <div className="text-sm font-semibold text-foreground mb-2">{t('wiki.import.previewResult')}</div>
                  <div className="text-xs text-foreground-dim mb-2">
                    {t('wiki.import.previewSummary').replace('{e}', String(preview.entities.length)).replace('{r}', String(preview.relations.length))}
                  </div>
                  <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                    {preview.entities.map((e, i) => (
                      <div key={i} className="rounded bg-card-bg px-2 py-1 text-xs border border-border-default">
                        <span className="font-medium">{e.name}</span>
                        <span className="ml-2 text-foreground-dim">{typeLabel(e.entity_type)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
