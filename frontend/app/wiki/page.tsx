'use client';

import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { WikiEntity, WikiRelation } from '@/types';
import { getWikiGraph, createWikiEntity, updateWikiEntity, deleteWikiEntity, createWikiRelation, deleteWikiRelation, importWiki, previewWikiImport } from '@/lib/api';
import GraphCanvas from './components/GraphCanvas';
import { t, useT } from '@/stores/localeStore';


const ENTITY_TYPES = [
  'person', 'organization', 'project', 'tech', 'concept',
  'docs', 'event', 'location', 'problem', 'solution',
];

const ENTITY_LABELS: Record<string, string> = {
  person: t('memory.type.person'), organization: t('wiki._e12'), project: t('memory.type.project'), tech: t('wiki._e13'), concept: t('wiki._e14'),
  docs: t('contextDash.kind.doc'), event: t('wiki._e15'), location: t('wiki._e16'), problem: t('wiki._e17'), solution: t('wiki._e18'),
};

const TYPE_ICON_COLORS: Record<string, string> = {
  person: '#22c55e', organization: '#ef4444', project: '#f97316', tech: '#06b6d4',
  concept: '#3b82f6', docs: '#a855f7', event: '#eab308', location: '#14b8a6',
  problem: '#f43f5e', solution: '#10b981',
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

const RELATION_TYPES = [
  'depends_on', 'part_of', 'uses', 'solves', 'related_to',
  'alternative_to', 'belongs_to', 'participates_in', 'authored_by', 'presents',
];

const RELATION_LABELS: Record<string, string> = {
  depends_on: t('wiki._e19'), part_of: t('wiki._e20'), uses: t('wiki._e21'), solves: t('wiki._e22'), related_to: t('wiki._e23'),
  alternative_to: t('wiki._e24'), belongs_to: t('wiki._e25'), participates_in: t('wiki._e26'), authored_by: t('wiki._e27'), presents: t('wiki._e28'),
};

export default function WikiExplorer() {
  const t = useT();
  const [entities, setEntities] = useState<WikiEntity[]>([]);
  const [relations, setRelations] = useState<WikiRelation[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [showEntityForm, setShowEntityForm] = useState(false);
  const [showRelationForm, setShowRelationForm] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [importSource, setImportSource] = useState<'text' | 'json' | 'context'>('text');
  const [importContent, setImportContent] = useState('');
  const [preview, setPreview] = useState<{ entities: any[]; relations: any[] } | null>(null);
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
    } catch (e) {
      addToast(t('wiki._e29'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { loadData(); }, [loadData]);

  const entityMap = useMemo(() => {
    const map = new Map<string, WikiEntity>();
    entities.forEach((e) => map.set(e.id, e));
    return map;
  }, [entities]);

  const neighborMap = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const r of relations) {
      if (!map.has(r.source_id)) map.set(r.source_id, new Set());
      if (!map.has(r.target_id)) map.set(r.target_id, new Set());
      map.get(r.source_id)!.add(r.target_id);
      map.get(r.target_id)!.add(r.source_id);
    }
    return map;
  }, [relations]);

  const highlightedIds = useMemo(() => {
    if (!search && filterTypes.size === 0) return new Set<string>();
    const q = search.toLowerCase();
    return new Set(
      entities
        .filter((e) => {
          const matchesSearch = !q || e.name.toLowerCase().includes(q) || (e.description || '').toLowerCase().includes(q);
          const matchesType = filterTypes.size === 0 || filterTypes.has(e.entity_type);
          return matchesSearch && matchesType;
        })
        .map((e) => e.id)
    );
  }, [entities, search, filterTypes]);

  const visibleEntities = useMemo(() => {
    if (focusedId) {
      const set = new Set<string>([focusedId]);
      const neighbors = neighborMap.get(focusedId);
      if (neighbors) neighbors.forEach((id) => set.add(id));
      return entities.filter((e) => set.has(e.id));
    }
    return entities;
  }, [entities, focusedId, neighborMap]);

  const visibleRelations = useMemo(() => {
    const ids = new Set(visibleEntities.map((e) => e.id));
    return relations.filter((r) => ids.has(r.source_id) && ids.has(r.target_id));
  }, [visibleEntities, relations]);

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
        addToast(t('wiki._e30'), 'success');
      } else {
        await createWikiEntity({
          name: formName.trim(),
          entity_type: formType,
          description: formDesc.trim(),
          aliases: formAliases.split(',').map((s) => s.trim()).filter(Boolean),
        });
        addToast(t('wiki._e31'), 'success');
      }
      setShowEntityForm(false);
      resetForm();
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('channels.saveFailed'), 'error');
    }
  };

  const handleDeleteEntity = async (id: string) => {
    try {
      await deleteWikiEntity(id);
      addToast(t('wiki._e32'), 'success');
      if (selectedId === id) setSelectedId(null);
      if (focusedId === id) setFocusedId(null);
      loadData();
    } catch (e) {
      addToast(t('channels.deleteFailed'), 'error');
    }
  };

  const handleCreateRelation = async () => {
    if (!relSource || !relTarget) return;
    try {
      await createWikiRelation({ source_id: relSource, target_id: relTarget, relation_type: relType, weight: relWeight });
      addToast(t('wiki._e33'), 'success');
      setShowRelationForm(false);
      setRelSource('');
      setRelTarget('');
      setRelWeight(1);
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('wiki._e34'), 'error');
    }
  };

  const handleDeleteRelation = async (id: string) => {
    try {
      await deleteWikiRelation(id);
      addToast(t('wiki._e35'), 'success');
      loadData();
    } catch (e) {
      addToast(t('wiki._e36'), 'error');
    }
  };

  const handlePreview = async () => {
    if (!importContent.trim()) {
      addToast(t('wiki._e37'), 'error');
      return;
    }
    try {
      setImporting(true);
      const p = await previewWikiImport({ source: importSource, content: importContent });
      setPreview(p);
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('wiki._e38'), 'error');
    } finally {
      setImporting(false);
    }
  };

  const handleImport = async () => {
    if (!importContent.trim()) {
      addToast(t('wiki._e37'), 'error');
      return;
    }
    setImporting(true);
    try {
      const result = await importWiki({ source: importSource, content: importContent, options: { dry_run: false, update_existing: true } });
      addToast(`导入完成：创建 ${result.entities_created} 实体，更新 ${result.entities_updated}，创建 ${result.relations_created} 关系`, 'success');
      setShowImportDialog(false);
      setImportContent('');
      setPreview(null);
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('wiki._e39'), 'error');
    } finally {
      setImporting(false);
    }
  };

  const toggleTypeFilter = (t: string) => {
    const next = new Set(filterTypes);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    setFilterTypes(next);
  };

  const handleCanvasCreateRelation = async (sourceId: string, targetId: string) => {
    try {
      await createWikiRelation({ source_id: sourceId, target_id: targetId, relation_type: 'related_to', weight: 1 });
      addToast(t('wiki._e33'), 'success');
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('wiki._e34'), 'error');
    }
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
              {ENTITY_LABELS[selectedEntity.entity_type] || selectedEntity.entity_type}
            </span>
          </div>
          <div className="flex gap-1">
            <button
              onClick={() => openEditEntity(selectedEntity)}
              className="rounded-lg p-1.5 text-foreground-dim hover:bg-gray-100 transition-colors"
              title={t('memory.edit')}
            >
              📝
            </button>
            <button
              onClick={() => handleDeleteEntity(selectedEntity.id)}
              className="rounded-lg p-1.5 text-foreground-dim hover:bg-red-50 hover:text-red-500 transition-colors"
              title={t('memory.delete')}
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
            <span className="text-xs font-medium text-foreground-dim">别名：</span>
            <div className="mt-1 flex flex-wrap gap-1">
              {selectedEntity.aliases.map((a: string, i: number) => (
                <span key={i} className="rounded-md bg-gray-100 px-2 py-0.5 text-xs text-foreground-dim">{a}</span>
              ))}
            </div>
          </div>
        )}
        {outgoing.length > 0 && (
          <div className="mb-3">
            <span className="text-xs font-medium text-foreground-dim">出边关系：</span>
            <div className="mt-1 space-y-1">
              {outgoing.map((r) => {
                const target = entityMap.get(r.target_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{RELATION_LABELS[r.relation_type] || r.relation_type}</span>
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
            <span className="text-xs font-medium text-foreground-dim">入边关系：</span>
            <div className="mt-1 space-y-1">
              {incoming.map((r) => {
                const source = entityMap.get(r.source_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="font-medium text-foreground">{source?.name || r.source_id.slice(0, 8)}</span>
                    <span className="text-foreground">→</span>
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{RELATION_LABELS[r.relation_type] || r.relation_type}</span>
                    <button onClick={() => handleDeleteRelation(r.id)} className="ml-auto text-foreground-dim hover:text-red-500">✕</button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {outgoing.length === 0 && incoming.length === 0 && (
          <p className="text-xs text-foreground-dim italic">{t('wiki._e9')}</p>
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

      <div className="flex flex-wrap items-center gap-3 border-b border-border-default bg-page-bg px-5 py-3">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <svg className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('wiki._e10')}
            className="w-full rounded-lg border border-border-default bg-card-bg py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-foreground-dim/50 focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple/30"
          />
        </div>

        <div className="flex flex-wrap gap-1.5">
          {ENTITY_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => toggleTypeFilter(t)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                filterTypes.has(t)
                  ? TYPE_COLORS[t] + ' ring-1 ring-offset-1'
                  : 'border-border-default text-foreground-dim hover:border-border-focus hover:text-foreground'
              }`}
            >
              {ENTITY_LABELS[t]}
            </button>
          ))}
        </div>

        <div className="flex rounded-lg border border-border-default overflow-hidden">
          <button onClick={() => setViewMode('graph')} className={`px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'graph' ? 'bg-brand-purple text-white' : 'text-foreground-dim hover:bg-gray-50'}`}>{t('wiki._e11')}</button>
          <button onClick={() => setViewMode('list')} className={`px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'list' ? 'bg-brand-purple text-white' : 'text-foreground-dim hover:bg-gray-50'}`}>{t('wiki._e12')}</button>
        </div>

        <div className="flex gap-2">
          <button onClick={openCreateEntity} className="rounded-lg bg-brand-purple px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-purple/90 transition-colors">+ 新建实体</button>
          <button onClick={() => setShowRelationForm(true)} className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors">+ 新建关系</button>
          <button onClick={() => { setImportContent(''); setPreview(null); setShowImportDialog(true); }} className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors">📥 导入</button>
        </div>
      </div>

      <div className="flex flex-1 gap-4 overflow-hidden p-5">
        <div className="flex-1 overflow-hidden rounded-xl border border-border-default bg-card-bg">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-foreground-dim">{t('profile.loading')}</div>
          ) : viewMode === 'graph' ? (
            <GraphCanvas
              entities={visibleEntities}
              relations={visibleRelations}
              selectedId={selectedId}
              focusedId={focusedId}
              onSelect={setSelectedId}
              onFocus={setFocusedId}
              onCreateRelation={handleCanvasCreateRelation}
              onDeleteEntity={handleDeleteEntity}
            />
          ) : (
            <div className="h-full overflow-y-auto">
              <div className="divide-y divide-border-subtle">
                {entities.length === 0 ? (
                  <div className="flex h-40 items-center justify-center text-sm text-foreground-dim">暂无实体，点击"新建实体"开始</div>
                ) : (
                  entities.map((e) => (
                    <button
                      key={e.id}
                      onClick={() => setSelectedId(e.id)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-gray-50 ${selectedId === e.id ? 'bg-brand-purple/5' : ''}`}
                    >
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: TYPE_ICON_COLORS[e.entity_type] || '#9ca3af' }} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-foreground truncate">{e.name}</span>
                          <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-foreground-dim">{ENTITY_LABELS[e.entity_type] || e.entity_type}</span>
                        </div>
                        {e.description && <p className="mt-0.5 truncate text-xs text-foreground-dim">{e.description}</p>}
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        <div className="w-80 shrink-0 overflow-y-auto">
          {selectedEntity ? renderEntityDetail() : (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border-default p-8 text-center">
              <div>
                <svg className="mx-auto mb-3 h-10 w-10 text-foreground-dim/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
                <p className="text-sm text-foreground-dim">点击图谱或列表中的实体<br />{t('wiki._e13')}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {showEntityForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowEntityForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">{editingEntity ? t('memory.modal.edit') : t('wiki._e40')}</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki._e14')}</label>
                <input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder={t('memory.form.namePlaceholder')} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('memory.form.type')}</label>
                <select value={formType} onChange={(e) => setFormType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {ENTITY_TYPES.map((t) => <option key={t} value={t}>{ENTITY_LABELS[t]}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('memory.form.desc')}</label>
                <textarea value={formDesc} onChange={(e) => setFormDesc(e.target.value)} placeholder={t('wiki._e15')} rows={3} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">别名（逗号分隔）</label>
                <input value={formAliases} onChange={(e) => setFormAliases(e.target.value)} placeholder={t('wiki._e16')} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowEntityForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">{t('contextDash.cancel')}</button>
                <button onClick={handleSaveEntity} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('kb.save')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showRelationForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowRelationForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">{t('wiki._e17')}</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki._e18')}</label>
                <select value={relSource} onChange={(e) => setRelSource(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">{t('wiki._e19')}</option>
                  {entities.map((e) => <option key={e.id} value={e.id}>{e.name} ({ENTITY_LABELS[e.entity_type] || e.entity_type})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki._e20')}</label>
                <select value={relTarget} onChange={(e) => setRelTarget(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">{t('wiki._e19')}</option>
                  {entities.filter((e) => e.id !== relSource).map((e) => <option key={e.id} value={e.id}>{e.name} ({ENTITY_LABELS[e.entity_type] || e.entity_type})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki._e21')}</label>
                <select value={relType} onChange={(e) => setRelType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {RELATION_TYPES.map((t) => <option key={t} value={t}>{RELATION_LABELS[t] || t}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('wiki._e22')}</label>
                <input type="number" value={relWeight} min={0} max={1} step={0.1} onChange={(e) => setRelWeight(Number(e.target.value))} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowRelationForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">{t('contextDash.cancel')}</button>
                <button onClick={handleCreateRelation} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('channels.create')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showImportDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowImportDialog(false)}>
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl bg-card-bg p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">📥 导入 Wiki 图谱</h3>
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
                    {s === 'text' ? t('wiki._e41') : s === 'json' ? 'JSON' : t('wiki._e42')}
                  </button>
                ))}
              </div>
              <textarea
                value={importContent}
                onChange={(e) => setImportContent(e.target.value)}
                placeholder={importSource === 'context' ? t('wiki._e43') : t('wiki._e44')}
                rows={6}
                disabled={importSource === 'context'}
                className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none disabled:bg-gray-50"
              />
              <div className="flex gap-2">
                <button onClick={handlePreview} disabled={importing} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50 disabled:opacity-50">{importing ? t('wiki._e45') : t('wiki._e46')}</button>
                <button onClick={handleImport} disabled={importing} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90 disabled:opacity-50">{importing ? t('skills.community.importing') : t('wiki._e47')}</button>
              </div>

              {preview && (
                <div className="rounded-lg border border-border-default bg-elevated-bg/40 p-4">
                  <div className="text-sm font-semibold text-foreground mb-2">{t('wiki._e23')}</div>
                  <div className="text-xs text-foreground-dim mb-2">实体 {preview.entities.length} 个，关系 {preview.relations.length} 个</div>
                  <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                    {preview.entities.map((e, i) => (
                      <div key={i} className="rounded bg-card-bg px-2 py-1 text-xs border border-border-default">
                        <span className="font-medium">{e.name}</span>
                        <span className="ml-2 text-foreground-dim">{ENTITY_LABELS[e.entity_type] || e.entity_type}</span>
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
