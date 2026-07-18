'use client';

import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { WikiEntity, WikiRelation } from '@/types';
import { useT } from '@/stores/localeStore';
import {
  getWikiEntities,
  createWikiEntity,
  updateWikiEntity,
  deleteWikiEntity,
  getWikiRelations,
  createWikiRelation,
  importWiki,
} from '@/lib/api';

const ENTITY_TYPES = ['concept', 'person', 'project', 'tech'] as const;
const RELATION_TYPES = ['uses', 'depends_on', 'related_to', 'part_of'] as const;
const TYPE_COLORS: Record<string, string> = {
  concept: 'bg-blue-100 text-blue-800 border-blue-200',
  person: 'bg-green-100 text-green-800 border-green-200',
  project: 'bg-purple-100 text-purple-800 border-purple-200',
  tech: 'bg-amber-100 text-amber-800 border-amber-200',
};

export default function WikiExplorer() {
  const t = useT();
  const [entities, setEntities] = useState<WikiEntity[]>([]);
  const [relations, setRelations] = useState<WikiRelation[]>([]);
  const [search, setSearch] = useState('');
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [selectedEntity, setSelectedEntity] = useState<WikiEntity | null>(null);
  const [showEntityForm, setShowEntityForm] = useState(false);
  const [showRelationForm, setShowRelationForm] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [importSource, setImportSource] = useState<'text' | 'json' | 'context'>('text');
  const [importContent, setImportContent] = useState('');
  const [importResult, setImportResult] = useState<{ entities_created: number; entities_updated: number; relations_created: number } | null>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [graphHighlight, setGraphHighlight] = useState<Set<string>>(new Set());

  // Entity form state
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState<string>('concept');
  const [formDesc, setFormDesc] = useState('');
  const [formAliases, setFormAliases] = useState('');

  // Relation form state
  const [relSource, setRelSource] = useState('');
  const [relTarget, setRelTarget] = useState('');
  const [relType, setRelType] = useState<string>('related_to');
  const [relWeight, setRelWeight] = useState(1);

  const addToast = useCallback((msg: string, type: 'success' | 'error') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [ents, rels] = await Promise.all([
        getWikiEntities(),
        getWikiRelations(),
      ]);
      setEntities(ents);
      setRelations(rels);
    } catch (e) {
      addToast('加载Wiki数据失败', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { loadData(); }, [loadData]);

  // Filter entities
  const filteredEntities = useMemo(() => {
    let result = entities;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(e =>
        e.name.toLowerCase().includes(q) ||
        e.description?.toLowerCase().includes(q) ||
        e.aliases?.some((a: string) => a.toLowerCase().includes(q))
      );
    }
    if (filterTypes.size > 0) {
      result = result.filter(e => filterTypes.has(e.entity_type));
    }
    return result;
  }, [entities, search, filterTypes]);

  // Compute highlighted set for graph
  const highlightedIds = useMemo(() => {
    if (!search && filterTypes.size === 0) return new Set<string>();
    return new Set(filteredEntities.map(e => e.id));
  }, [filteredEntities, search, filterTypes]);

  // Get relations for selected entity
  const entityRelations = useMemo(() => {
    if (!selectedEntity) return { outgoing: [] as WikiRelation[], incoming: [] as WikiRelation[] };
    return {
      outgoing: relations.filter(r => r.source_id === selectedEntity.id),
      incoming: relations.filter(r => r.target_id === selectedEntity.id),
    };
  }, [relations, selectedEntity]);

  const entityMap = useMemo(() => {
    const map = new Map<string, WikiEntity>();
    entities.forEach(e => map.set(e.id, e));
    return map;
  }, [entities]);

  const handleCreateEntity = async () => {
    if (!formName.trim()) return;
    try {
      await createWikiEntity({
        name: formName.trim(),
        entity_type: formType,
        description: formDesc.trim(),
        aliases: formAliases.split(',').map(s => s.trim()).filter(Boolean),
      });
      addToast('实体创建成功', 'success');
      setShowEntityForm(false);
      resetForm();
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('channels.createFailed'), 'error');
    }
  };

  const handleDeleteEntity = async (id: string) => {
    try {
      await deleteWikiEntity(id);
      addToast('实体已删除', 'success');
      if (selectedEntity?.id === id) setSelectedEntity(null);
      loadData();
    } catch (e) {
      addToast(t('channels.deleteFailed'), 'error');
    }
  };

  const handleCreateRelation = async () => {
    if (!relSource || !relTarget) return;
    try {
      await createWikiRelation({
        source_id: relSource,
        target_id: relTarget,
        relation_type: relType,
        weight: relWeight,
      });
      addToast('关系创建成功', 'success');
      setShowRelationForm(false);
      setRelSource('');
      setRelTarget('');
      setRelWeight(1);
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || '创建关系失败', 'error');
    }
  };

  const handleImport = async () => {
    if (importSource !== 'context' && !importContent.trim()) {
      addToast('请输入导入内容', 'error');
      return;
    }
    setImportLoading(true);
    try {
      const result = await importWiki({
        source: importSource,
        content: importSource === 'context' ? undefined : importContent,
        options: { dry_run: false, update_existing: true },
      });
      setImportResult(result);
      addToast(`导入完成：创建 ${result.entities_created} 实体，更新 ${result.entities_updated}，创建 ${result.relations_created} 关系`, 'success');
      loadData();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || '导入失败', 'error');
    } finally {
      setImportLoading(false);
    }
  };

  const resetForm = () => {
    setFormName('');
    setFormType('concept');
    setFormDesc('');
    setFormAliases('');
  };

  const toggleTypeFilter = (t: string) => {
    const next = new Set(filterTypes);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    setFilterTypes(next);
  };

  // Render entity detail panel
  const renderEntityDetail = () => {
    if (!selectedEntity) return null;
    const { outgoing, incoming } = entityRelations;

    return (
      <div className="rounded-xl border border-border-default bg-card-bg p-5 shadow-sm">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-foreground">{selectedEntity.name}</h3>
            <span className={`mt-1 inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${TYPE_COLORS[selectedEntity.entity_type] || 'bg-gray-100 text-gray-800'}`}>
              {selectedEntity.entity_type}
            </span>
          </div>
          <button
            onClick={() => handleDeleteEntity(selectedEntity.id)}
            className="rounded-lg p-1.5 text-foreground-dim hover:bg-red-50 hover:text-red-500 transition-colors"
            title="删除实体"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
          </button>
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

        {/* Outgoing relations */}
        {outgoing.length > 0 && (
          <div className="mb-3">
            <span className="text-xs font-medium text-foreground-dim">出边关系：</span>
            <div className="mt-1 space-y-1">
              {outgoing.map(r => {
                const target = entityMap.get(r.target_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{r.relation_type}</span>
                    <span className="text-foreground">→</span>
                    <span className="font-medium text-foreground">{target?.name || r.target_id.slice(0, 8)}</span>
                    {r.weight !== 1 && <span className="text-foreground-dim">(w:{r.weight})</span>}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Incoming relations */}
        {incoming.length > 0 && (
          <div>
            <span className="text-xs font-medium text-foreground-dim">入边关系：</span>
            <div className="mt-1 space-y-1">
              {incoming.map(r => {
                const source = entityMap.get(r.source_id);
                return (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="font-medium text-foreground">{source?.name || r.source_id.slice(0, 8)}</span>
                    <span className="text-foreground">→</span>
                    <span className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-brand-purple font-medium">{r.relation_type}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {outgoing.length === 0 && incoming.length === 0 && (
          <p className="text-xs text-foreground-dim italic">暂无关联关系</p>
        )}
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      {/* Toast */}
      {toast && (
        <div className={`fixed right-4 top-4 z-50 rounded-lg px-4 py-2.5 text-sm font-medium shadow-lg ${
          toast.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
        }`}>
          {toast.msg}
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border-default bg-page-bg px-5 py-3">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <svg className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索实体名称、描述、别名..."
            className="w-full rounded-lg border border-border-default bg-card-bg py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-foreground-dim/50 focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple/30"
          />
        </div>

        {/* Type filter chips */}
        <div className="flex flex-wrap gap-1.5">
          {ENTITY_TYPES.map(t => (
            <button
              key={t}
              onClick={() => toggleTypeFilter(t)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                filterTypes.has(t)
                  ? TYPE_COLORS[t] + ' ring-1 ring-offset-1'
                  : 'border-border-default text-foreground-dim hover:border-border-focus hover:text-foreground'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* View mode toggle */}
        <div className="flex rounded-lg border border-border-default overflow-hidden">
          <button
            onClick={() => setViewMode('graph')}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              viewMode === 'graph' ? 'bg-brand-purple text-white' : 'text-foreground-dim hover:bg-gray-50'
            }`}
          >
            图谱
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              viewMode === 'list' ? 'bg-brand-purple text-white' : 'text-foreground-dim hover:bg-gray-50'
            }`}
          >
            列表
          </button>
        </div>

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={() => { resetForm(); setShowEntityForm(true); }}
            className="rounded-lg bg-brand-purple px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-purple/90 transition-colors"
          >
            + 新建实体
          </button>
          <button
            onClick={() => setShowRelationForm(true)}
            className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors"
          >
            + 新建关系
          </button>
          <button
            onClick={() => { setImportContent(''); setImportResult(null); setShowImportDialog(true); }}
            className="rounded-lg border border-border-default px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-gray-50 transition-colors"
          >
            📥 导入
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 gap-4 overflow-hidden p-5">
        {/* Left: graph or list */}
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex h-full items-center justify-center text-sm text-foreground-dim">{t('profile.loading')}</div>
          ) : viewMode === 'graph' ? (
            <div className="h-full rounded-xl border border-border-default bg-card-bg p-0.5 flex items-center justify-center text-sm text-foreground-dim">
              图形视图开发中
            </div>
          ) : (
            <div className="h-full overflow-y-auto rounded-xl border border-border-default bg-card-bg">
              <div className="divide-y divide-border-subtle">
                {filteredEntities.length === 0 ? (
                  <div className="flex h-40 items-center justify-center text-sm text-foreground-dim">
                    {search || filterTypes.size > 0 ? '无匹配实体' : '暂无实体，点击"新建实体"开始'}
                  </div>
                ) : (
                  filteredEntities.map(e => (
                    <button
                      key={e.id}
                      onClick={() => setSelectedEntity(e)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-gray-50 ${
                        selectedEntity?.id === e.id ? 'bg-brand-purple/5' : ''
                      }`}
                    >
                      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                        e.entity_type === 'concept' ? 'bg-blue-400' :
                        e.entity_type === 'person' ? 'bg-green-400' :
                        e.entity_type === 'project' ? 'bg-purple-400' : 'bg-amber-400'
                      }`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-foreground truncate">{e.name}</span>
                          <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-foreground-dim">{e.entity_type}</span>
                        </div>
                        {e.description && (
                          <p className="mt-0.5 truncate text-xs text-foreground-dim">{e.description}</p>
                        )}
                      </div>
                      <svg className="h-4 w-4 shrink-0 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right: detail panel */}
        <div className="w-80 shrink-0 overflow-y-auto">
          {selectedEntity ? renderEntityDetail() : (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border-default p-8 text-center">
              <div>
                <svg className="mx-auto mb-3 h-10 w-10 text-foreground-dim/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
                <p className="text-sm text-foreground-dim">点击图谱或列表中的实体<br />查看详情和关联关系</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Entity form dialog */}
      {showEntityForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowEntityForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">新建实体</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">名称 *</label>
                <input value={formName} onChange={e => setFormName(e.target.value)} placeholder={t('memory.form.namePlaceholder')} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('memory.form.type')}</label>
                <select value={formType} onChange={e => setFormType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">{t('memory.form.desc')}</label>
                <textarea value={formDesc} onChange={e => setFormDesc(e.target.value)} placeholder="实体描述" rows={3} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">别名（逗号分隔）</label>
                <input value={formAliases} onChange={e => setFormAliases(e.target.value)} placeholder="别名1, 别名2" className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowEntityForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">取消</button>
                <button onClick={handleCreateEntity} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('channels.create')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Relation form dialog */}
      {showRelationForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowRelationForm(false)}>
          <div className="w-full max-w-md rounded-xl bg-card-bg p-6 shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">新建关系</h3>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">源实体 *</label>
                <select value={relSource} onChange={e => setRelSource(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">选择实体...</option>
                  {entities.map(e => <option key={e.id} value={e.id}>{e.name} ({e.entity_type})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">目标实体 *</label>
                <select value={relTarget} onChange={e => setRelTarget(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  <option value="">选择实体...</option>
                  {entities.filter(e => e.id !== relSource).map(e => <option key={e.id} value={e.id}>{e.name} ({e.entity_type})</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">关系类型</label>
                <select value={relType} onChange={e => setRelType(e.target.value)} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none">
                  {RELATION_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-dim">权重</label>
                <input type="number" value={relWeight} min={0} max={1} step={0.1} onChange={e => setRelWeight(Number(e.target.value))} className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none" />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setShowRelationForm(false)} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">取消</button>
                <button onClick={handleCreateRelation} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90">{t('channels.create')}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Import dialog */}
      {showImportDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowImportDialog(false)}>
          <div className="w-full max-w-lg rounded-xl bg-card-bg p-6 shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-5 text-base font-semibold text-foreground">导入知识图谱</h3>
            <div className="space-y-4">
              <div className="flex gap-2">
                {(['text', 'json', 'context'] as const).map(s => (
                  <button
                    key={s}
                    onClick={() => setImportSource(s)}
                    className={`flex-1 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                      importSource === s ? 'border-brand-purple bg-brand-purple/5 text-brand-purple' : 'border-border-default text-foreground-dim hover:border-border-focus'
                    }`}
                  >
                    {s === 'text' ? '文本' : s === 'json' ? 'JSON' : 'Context'}
                  </button>
                ))}
              </div>
              {importSource !== 'context' && (
                <textarea
                  value={importContent}
                  onChange={e => setImportContent(e.target.value)}
                  placeholder={importSource === 'text' ? '粘贴文本内容，LLM 将自动提取实体和关系...' : '粘贴 JSON 格式的图谱数据...'}
                  rows={6}
                  className="w-full rounded-lg border border-border-default px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none resize-none"
                />
              )}
              {importSource === 'context' && (
                <p className="text-xs text-foreground-dim">将从当前会话的 Context 数据中提取知识图谱</p>
              )}
              {importResult && (
                <div className="rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-700">
                  导入完成：创建 {importResult.entities_created} 实体，更新 {importResult.entities_updated}，创建 {importResult.relations_created} 关系
                </div>
              )}
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => { setShowImportDialog(false); setImportResult(null); }} className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground hover:bg-gray-50">关闭</button>
                <button onClick={handleImport} disabled={importLoading} className="rounded-lg bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/90 disabled:opacity-50">
                  {importLoading ? '导入中...' : '开始导入'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}