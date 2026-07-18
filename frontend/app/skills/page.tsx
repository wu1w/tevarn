'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Skill } from '@/types';
import {
  getSkills,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
  getCommunitySkills,
  importCommunitySkills,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { Skeleton } from '@/components/desktop/Skeleton';
import { EmptyState } from '@/components/desktop/EmptyState';
import SkillStorePanel from '@/components/skills/SkillStorePanel';

type TabKey = 'builtin' | 'custom' | 'community' | 'store';

const DEFAULT_COMMUNITY_URL =
  'https://raw.githubusercontent.com/takton-ai/community-skills/main/index.json';

const SKILL_BADGE =
  'rounded border border-border-subtle bg-elevated-bg/80 px-1.5 py-0.5 text-[10px] font-medium text-foreground-muted';

type SkillCategory = 'coding' | 'research' | 'ops' | 'media' | 'integration' | 'other';

const SKILL_CATEGORIES: { id: SkillCategory | 'all'; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'coding', label: '编码' },
  { id: 'research', label: '调研' },
  { id: 'ops', label: '运维' },
  { id: 'media', label: '媒体' },
  { id: 'integration', label: '集成' },
  { id: 'other', label: '其他' },
];

function skillCategory(skill: { name: string; description: string | null; handler: string }): SkillCategory {
  const t = `${skill.name} ${skill.description || ''}`.toLowerCase();
  if (/code|git|refactor|test|debug|lint|pr|编程|代码|开发/.test(t)) return 'coding';
  if (/search|research|wiki|paper|调研|搜索|知识/.test(t)) return 'research';
  if (/deploy|ops|docker|cron|server|运维|监控|日志/.test(t)) return 'ops';
  if (/image|video|audio|ppt|report|图|视频|报告|幻灯/.test(t)) return 'media';
  if (/http|api|mcp|webhook|slack|telegram|集成|通道/.test(t) || skill.handler === 'http')
    return 'integration';
  return 'other';
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>('builtin');
  const [categoryFilter, setCategoryFilter] = useState<SkillCategory | 'all'>('all');
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();

  // Custom skill modal
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formSchema, setFormSchema] = useState('');
  const [formHandler, setFormHandler] = useState<'http' | 'python'>('http');
  const [formConfig, setFormConfig] = useState('');
  const [formEnabled, setFormEnabled] = useState(true);

  // Community
  const [communityUrl, setCommunityUrl] = useState('');
  const [communitySkills, setCommunitySkills] = useState<Skill[]>([]);
  const [communityLoading, setCommunityLoading] = useState(false);
  const [selectedCommunity, setSelectedCommunity] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getSkills();
      setSkills(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
      addToast('加载 Skill 列表失败', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const builtinSkills = useMemo(
      () => skills.filter((s) => s.is_builtin),
      [skills]
    );
    const customSkills = useMemo(
      () => skills.filter((s) => !s.is_builtin),
      [skills]
    );

    const filterByCat = (list: Skill[]) =>
      list.filter(
        (s) => categoryFilter === 'all' || skillCategory(s) === categoryFilter
      );

    const groupSkills = (list: Skill[]) => {
      const order: SkillCategory[] = [
        'coding',
        'research',
        'ops',
        'media',
        'integration',
        'other',
      ];
      const map = new Map<SkillCategory, Skill[]>();
      for (const c of order) map.set(c, []);
      for (const s of list) {
        map.get(skillCategory(s))!.push(s);
      }
      return order
        .map((id) => ({
          id,
          label: SKILL_CATEGORIES.find((x) => x.id === id)?.label || id,
          items: map.get(id) || [],
        }))
        .filter((g) => g.items.length > 0);
    };

    const openCreate = () => {
    setEditingSkill(null);
    setFormName('');
    setFormDescription('');
    setFormSchema(JSON.stringify({ type: 'object', properties: {} }, null, 2));
    setFormHandler('http');
    setFormConfig(JSON.stringify({ url: '', method: 'GET' }, null, 2));
    setFormEnabled(true);
    setModalOpen(true);
  };

  const openEdit = (skill: Skill) => {
    setEditingSkill(skill);
    setFormName(skill.name);
    setFormDescription(skill.description || '');
    setFormSchema(JSON.stringify(skill.schema, null, 2));
    setFormHandler(skill.handler);
    setFormConfig(JSON.stringify(skill.handler_config, null, 2));
    setFormEnabled(skill.enabled);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingSkill(null);
  };

  const parseJson = (text: string) => {
    return text.trim() ? JSON.parse(text) : {};
  };

  const handleSaveCustom = async () => {
    if (!formName.trim()) {
      addToast('名称不能为空', 'error');
      return;
    }
    let schema: Record<string, unknown>;
    let handlerConfig: Record<string, unknown>;
    try {
      schema = parseJson(formSchema);
      handlerConfig = parseJson(formConfig);
    } catch (e) {
      addToast('JSON 格式错误', 'error');
      return;
    }
    const payload = {
      name: formName.trim(),
      description: formDescription.trim(),
      schema,
      enabled: formEnabled,
      handler: formHandler,
      handler_config: handlerConfig,
    };
    try {
      if (editingSkill) {
        await updateSkill(editingSkill.id, payload);
        addToast('Skill 已更新', 'success');
      } else {
        await createSkill(payload);
        addToast('Skill 已创建', 'success');
      }
      closeModal();
      load();
    } catch (e: any) {
      console.error(e);
      addToast('保存失败：' + (e?.response?.data?.detail || e?.message || '未知错误'), 'error');
    }
  };

  const handleDelete = async (skill: Skill) => {
    const ok = await confirm(`确定删除自定义 Skill "${skill.name}"？`, '删除 Skill', 'danger');
    if (!ok) return;
    try {
      await deleteSkill(skill.id);
      addToast('Skill 已删除', 'success');
      load();
    } catch (e: any) {
      console.error(e);
      addToast('删除失败：' + (e?.response?.data?.detail || e?.message || '未知错误'), 'error');
    }
  };

  const handleToggle = async (skill: Skill) => {
    try {
      await toggleSkill(skill.id, !skill.enabled);
      addToast(skill.enabled ? 'Skill 已禁用' : 'Skill 已启用', 'success');
      load();
    } catch (e: any) {
      console.error(e);
      addToast('切换失败：' + (e?.response?.data?.detail || e?.message || '未知错误'), 'error');
    }
  };

  const handleFetchCommunity = async () => {
    setCommunityLoading(true);
    setSelectedCommunity(new Set());
    try {
      const url = communityUrl.trim() || undefined;
      const data = await getCommunitySkills(url);
      setCommunitySkills(data);
      addToast(`获取到 ${data.length} 个社区 Skill`, 'info');
    } catch (e: any) {
      console.error(e);
      addToast('获取社区 Skill 失败：' + (e?.response?.data?.detail || e?.message || '未知错误'), 'error');
      setCommunitySkills([]);
    } finally {
      setCommunityLoading(false);
    }
  };

  const toggleSelection = (name: string) => {
    setSelectedCommunity((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleImportCommunity = async () => {
    if (selectedCommunity.size === 0) return;
    setImporting(true);
    try {
      const url = communityUrl.trim() || undefined;
      const res = await importCommunitySkills(Array.from(selectedCommunity), url);
      addToast(`成功导入 ${res.imported} 个 Skill`, 'success');
      setSelectedCommunity(new Set());
      load();
    } catch (e: any) {
      console.error(e);
      addToast('导入失败：' + (e?.response?.data?.detail || e?.message || '未知错误'), 'error');
    } finally {
      setImporting(false);
    }
  };

  const renderSkillCard = (skill: Skill, allowEdit: boolean) => (
        <div
          key={skill.id}
          className={`flex items-start justify-between rounded-xl border px-4 py-3 transition-colors ${
            skill.enabled
              ? 'border-border-default bg-card-bg'
              : 'border-border-subtle bg-card-bg/60 opacity-90'
          }`}
        >
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-foreground">{skill.name}</span>
              <span className={SKILL_BADGE}>
                {SKILL_CATEGORIES.find((c) => c.id === skillCategory(skill))?.label || '其他'}
              </span>
              {skill.is_builtin && <span className={SKILL_BADGE}>内置</span>}
              {!skill.is_builtin && <span className={SKILL_BADGE}>{skill.handler}</span>}
              {skill.enabled && <span className={SKILL_BADGE}>启用</span>}
            </div>
            <div className="mt-0.5 text-sm text-foreground-muted">
              {skill.description || 'No description'}
            </div>
          </div>
        <div className="ml-3 flex items-center gap-2">
          <button
            onClick={() => handleToggle(skill)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              skill.enabled ? 'bg-brand-purple' : 'bg-elevated-bg'
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                skill.enabled ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
          {allowEdit && (
            <>
              <button
                onClick={() => openEdit(skill)}
                className="rounded-md border border-border-default px-2 py-1 text-xs text-foreground-muted hover:bg-elevated-bg"
              >
                编辑
              </button>
              <button
                onClick={() => handleDelete(skill)}
                className="rounded-md bg-error-bg px-2 py-1 text-xs text-error-text hover:bg-error-bg"
              >
                删除
              </button>
            </>
          )}
        </div>
      </div>
    );

  return (
    <div className="p-6">
      {ConfirmDialogComponent}
      <h1 className="mb-6 text-xl font-bold text-foreground">Skill 管理</h1>

      <div className="mb-4 flex items-center gap-2 border-b border-border-default">
              {[
                { key: 'builtin', label: `内置 (${builtinSkills.length})` },
                { key: 'custom', label: `自定义 (${customSkills.length})` },
                { key: 'community', label: '社区' },
                { key: 'store', label: '🛍 商店' },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key as TabKey)}
                  className={`px-3 py-2 text-sm font-medium ${
                    activeTab === tab.key
                      ? 'border-b-2 border-violet-400 text-violet-400'
                      : 'text-foreground-dim hover:text-foreground-muted'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {activeTab !== 'community' && activeTab !== 'store' && (
              <div className="mb-4 flex flex-wrap gap-1.5">
                {SKILL_CATEGORIES.map((c) => {
                  const pool = activeTab === 'builtin' ? builtinSkills : customSkills;
                  const count =
                    c.id === 'all' ? pool.length : pool.filter((s) => skillCategory(s) === c.id).length;
                  const active = categoryFilter === c.id;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setCategoryFilter(c.id)}
                      className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                        active
                          ? 'border-brand-purple/40 bg-brand-purple/10 text-foreground'
                          : 'border-border-subtle bg-card-bg text-foreground-muted hover:border-border-default'
                      }`}
                    >
                      {c.label}
                      <span className="ml-1 tabular-nums text-foreground-dim">{count}</span>
                    </button>
                  );
                })}
              </div>
            )}

            {loading && activeTab !== 'store' ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => (
                  <Skeleton key={i} height="72px" borderRadius="8px" />
                ))}
              </div>
            ) : (
              <>
                {activeTab === 'builtin' && (
                  <div className="space-y-4">
                    {groupSkills(filterByCat(builtinSkills)).map((g) => (
                      <div key={g.id}>
                        <div className="mb-1.5 flex items-center gap-2">
                          <span className="text-xs font-medium text-foreground-muted">{g.label}</span>
                          <span className="h-px flex-1 bg-border-subtle" />
                          <span className="text-[10px] text-foreground-dim">{g.items.length}</span>
                        </div>
                        <div className="space-y-2">
                          {g.items.map((s) => renderSkillCard(s, false))}
                        </div>
                      </div>
                    ))}
                    {filterByCat(builtinSkills).length === 0 && (
                      <EmptyState title="暂无内置 Skill" description="系统内置 Skill 将在初始化时自动创建，或切换分类查看" />
                    )}
                  </div>
                )}

                {activeTab === 'custom' && (
                  <>
                    <div className="mb-4">
                      <button
                        onClick={openCreate}
                        className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700"
                      >
                        + 新建自定义 Skill
                      </button>
                    </div>
                    <div className="space-y-4">
                      {groupSkills(filterByCat(customSkills)).map((g) => (
                        <div key={g.id}>
                          <div className="mb-1.5 flex items-center gap-2">
                            <span className="text-xs font-medium text-foreground-muted">{g.label}</span>
                            <span className="h-px flex-1 bg-border-subtle" />
                            <span className="text-[10px] text-foreground-dim">{g.items.length}</span>
                          </div>
                          <div className="space-y-2">
                            {g.items.map((s) => renderSkillCard(s, true))}
                          </div>
                        </div>
                      ))}
                      {filterByCat(customSkills).length === 0 && (
                        <EmptyState title="暂无自定义 Skill" description="点击上方按钮创建，或切换分类" />
                      )}
                    </div>
                  </>
                )}

                          {activeTab === 'community' && (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={communityUrl}
                  onChange={(e) => setCommunityUrl(e.target.value)}
                  placeholder={`默认：${DEFAULT_COMMUNITY_URL}`}
                  className="flex-1 rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                />
                <button
                  onClick={handleFetchCommunity}
                  disabled={communityLoading}
                  className="rounded-md border border-border-default bg-card-bg px-3 py-2 text-sm text-foreground-muted hover:bg-elevated-bg disabled:opacity-50"
                >
                  {communityLoading ? '获取中...' : '获取列表'}
                </button>
              </div>

              {communitySkills.length > 0 ? (
                <>
                  <div className="space-y-2">
                    {communitySkills.map((s) => (
                      <label
                        key={s.name}
                        className="flex cursor-pointer items-start gap-3 rounded-lg border border-border-default bg-card-bg px-4 py-3 hover:bg-elevated-bg"
                      >
                        <input
                          type="checkbox"
                          checked={selectedCommunity.has(s.name)}
                          onChange={() => toggleSelection(s.name)}
                          className="mt-1 h-4 w-4 accent-violet-600"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="font-medium text-foreground">{s.name}</div>
                          <div className="text-sm text-foreground-dim">{s.description || 'No description'}</div>
                          <div className="mt-1 text-[10px] text-foreground-muted">handler: {s.handler}</div>
                        </div>
                      </label>
                    ))}
                  </div>
                  <button
                    onClick={handleImportCommunity}
                    disabled={importing || selectedCommunity.size === 0}
                    className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    {importing ? '导入中...' : `导入所选 (${selectedCommunity.size})`}
                  </button>
                </>
              ) : (
                !communityLoading && (
                  <EmptyState
                    title="获取社区 Skill"
                    description="输入社区 Skill 仓库 URL 并点击获取列表"
                    icon="🌐"
                  />
                )
              )}
            </div>
          )}

          {activeTab === 'store' && <SkillStorePanel />}
        </>
      )}

      {/* Custom Skill Modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={closeModal}>
          <div
            className="w-full max-w-2xl max-h-[80vh] overflow-y-auto rounded-lg bg-card-bg p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-4 text-base font-semibold text-foreground">
              {editingSkill ? '编辑自定义 Skill' : '新建自定义 Skill'}
            </h3>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-muted">名称</label>
                <input
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  disabled={!!editingSkill}
                  placeholder="如：get_weather"
                  className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 disabled:bg-card-bg-hover"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-muted">描述</label>
                <input
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                  placeholder="一句话说明用途"
                  className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-foreground-muted">Handler</label>
                  <select
                    value={formHandler}
                    onChange={(e) => setFormHandler(e.target.value as 'http' | 'python')}
                    className="w-full rounded-md border border-border-default bg-card-bg px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                  >
                    <option value="http">HTTP 请求</option>
                    <option value="python">Python 脚本</option>
                  </select>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    id="skillEnabled"
                    type="checkbox"
                    checked={formEnabled}
                    onChange={(e) => setFormEnabled(e.target.checked)}
                    className="h-4 w-4 accent-violet-600"
                  />
                  <label htmlFor="skillEnabled" className="text-sm text-foreground-muted">默认启用</label>
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-muted">
                  JSON Schema（供 LLM 识别参数）
                </label>
                <textarea
                  value={formSchema}
                  onChange={(e) => setFormSchema(e.target.value)}
                  rows={5}
                  className="w-full rounded-md border border-border-default px-3 py-2 font-mono text-xs focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-foreground-muted">
                  Handler Config（{formHandler === 'http' ? 'url / method / headers' : 'code'}）
                </label>
                <textarea
                  value={formConfig}
                  onChange={(e) => setFormConfig(e.target.value)}
                  rows={8}
                  className="w-full rounded-md border border-border-default px-3 py-2 font-mono text-xs focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500"
                />
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={closeModal}
                className="rounded-md border border-border-default px-4 py-2 text-sm text-foreground-muted hover:bg-elevated-bg"
              >
                取消
              </button>
              <button
                onClick={handleSaveCustom}
                className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}