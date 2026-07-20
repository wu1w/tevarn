'use client';

/**
 * 子代理管理 — 人物卡片
 * 任务名称 / 模型 / system prompt；供主对话「集群模式」选用
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  Edit3,
  Plus,
  Power,
  RefreshCw,
  Search,
  Trash2,
  Users,
  X,
} from 'lucide-react';

import { modelInventoryApi, subAgentApi } from '@/lib/subagent-api';
import type {
  ModelInventoryItem,
  SubAgent,
  SubAgentCreate,
  SubAgentUpdate,
} from '@/types/subagent';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { t, useT } from '@/stores/localeStore';

const TOOLSET_OPTIONS = [
  { value: 'file', label: t('tools.cat.file'), icon: '📁' },
  { value: 'terminal', label: t('subagent._e118'), icon: '💻' },
  { value: 'git', label: 'Git', icon: '🔀' },
  { value: 'web', label: t('memory.search'), icon: '🌐' },
  { value: 'browser', label: t('tools.type.browser'), icon: '🖥️' },
  { value: 'code', label: t('subagent._e119'), icon: '⚡' },
];

const AVATAR_PRESETS = ['🤖', '👩‍💻', '🧑‍🔬', '🕵️', '📝', '🛠️', '🎨', '📊', '🔒', '🚀'];

function ModelSelector({
  inventory,
  value,
  onChange,
}: {
  inventory: ModelInventoryItem[];
  value: string;
  onChange: (ref: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = inventory.find((i) => i.ref === value);

  const groups = useMemo(() => {
    const g: Record<string, ModelInventoryItem[]> = {};
    for (const item of inventory) {
      const group =
        item.status === 'active' || item.status === 'default'
          ? t('subagent._e120')
          : item.status === 'fallback'
            ? t('subagent._e121')
            : t('subagent._e122');
      (g[group] ||= []).push(item);
    }
    return g;
  }, [inventory]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground hover:border-brand-purple/40"
      >
        <span className="flex min-w-0 items-center gap-2">
          {selected ? (
            <>
              <span>{selected.provider_icon}</span>
              <span className="truncate">
                {selected.provider_name} · {selected.model_name}
              </span>
            </>
          ) : (
            <span className="text-foreground-dim">从已配模型池选择…</span>
          )}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-foreground-dim" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-border-subtle bg-card-bg shadow-xl">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group}>
              <div className="sticky top-0 bg-card-bg-hover px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
                {group}
              </div>
              {items.map((item) => (
                <button
                  key={item.ref}
                  type="button"
                  onClick={() => {
                    onChange(item.ref);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-card-bg-hover ${
                    item.ref === value ? 'bg-brand-purple/10 text-brand-cyan' : 'text-foreground'
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span>{item.provider_icon}</span>
                    <span className="truncate font-mono text-xs">
                      {item.provider_name} / {item.model_name}
                    </span>
                  </span>
                  {item.ref === value && <Check className="h-3.5 w-3.5 text-brand-cyan" />}
                </button>
              ))}
            </div>
          ))}
          {inventory.length === 0 && (
            <div className="px-3 py-6 text-center text-sm text-foreground-dim">
              暂无可用模型，请先在「设置」配置服务商并拉模型
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CharacterCard({
  agent,
  onEdit,
  onDelete,
  onToggle,
}: {
  agent: SubAgent;
  onEdit: (a: SubAgent) => void;
  onDelete: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const modelShort = agent.model_ref?.includes('/')
    ? agent.model_ref.split('/').slice(-1)[0]
    : agent.model_ref;

  return (
    <div
      className={`group relative overflow-hidden rounded-2xl border bg-card-bg/50 p-4 transition-all hover:border-brand-purple/30 hover:shadow-lg hover:shadow-brand-purple/5 ${
        agent.enabled ? 'border-border-subtle' : 'border-border-subtle/50 opacity-55'
      }`}
    >
      <div className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-gradient-to-br from-brand-purple/15 to-brand-cyan/10 blur-2xl" />
      <div className="relative flex items-start gap-3">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-border-subtle bg-gradient-to-br from-brand-purple/20 to-brand-cyan/10 text-3xl shadow-inner">
          {agent.icon || '🤖'}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-foreground">{agent.name}</h3>
              <p className="mt-0.5 line-clamp-2 text-xs text-foreground-dim">
                {agent.description || t('subagent._e123')}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-0.5 opacity-80 group-hover:opacity-100">
              <button
                type="button"
                onClick={() => onToggle(agent.id, !agent.enabled)}
                className={`rounded-lg p-1.5 ${
                  agent.enabled
                    ? 'text-emerald-400 hover:bg-emerald-500/10'
                    : 'text-foreground-dim hover:bg-card-bg-hover'
                }`}
                title={agent.enabled ? t('cron.disabled') : t('channels.enable')}
              >
                <Power className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onEdit(agent)}
                className="rounded-lg p-1.5 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
                title={t('memory.edit')}
              >
                <Edit3 className="h-3.5 w-3.5" />
              </button>
              {!agent.is_builtin && (
                <button
                  type="button"
                  onClick={() => onDelete(agent.id)}
                  className="rounded-lg p-1.5 text-red-400/80 hover:bg-red-500/10"
                  title={t('memory.delete')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="rounded-md border border-brand-purple/20 bg-brand-purple/10 px-2 py-0.5 font-mono text-[10px] text-brand-cyan">
              {modelShort || t('subagent._e124')}
            </span>
            {agent.is_builtin && (
              <span className="rounded-md border border-border-subtle bg-card-bg-hover px-2 py-0.5 text-[10px] text-foreground-dim">
                内置
              </span>
            )}
            {(agent.enabled_toolsets || []).slice(0, 3).map((t) => (
              <span
                key={t}
                className="rounded-md border border-border-subtle px-1.5 py-0.5 text-[10px] text-foreground-muted"
              >
                {t}
              </span>
            ))}
          </div>
          {agent.system_prompt ? (
            <p className="mt-3 line-clamp-3 rounded-xl border border-border-subtle/60 bg-input-bg/40 px-2.5 py-2 text-[11px] leading-relaxed text-foreground-muted">
              {agent.system_prompt}
            </p>
          ) : (
            <p className="mt-3 text-[11px] italic text-foreground-dim">{t('subagent._e74')}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function SubAgentFormDialog({
  initial,
  inventory,
  onClose,
  onSaved,
}: {
  initial?: SubAgent;
  inventory: ModelInventoryItem[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!initial;
  const addToast = useToastStore((s) => s.addToast);
  const [form, setForm] = useState<SubAgentCreate>({
    name: initial?.name || '',
    description: initial?.description || '',
    icon: initial?.icon || '🤖',
    model_ref: initial?.model_ref || inventory.find((i) => i.status === 'active')?.ref || '',
    system_prompt: initial?.system_prompt || '',
    enabled_toolsets: initial?.enabled_toolsets || [],
    max_iterations: initial?.max_iterations || 5,
    temperature: initial?.temperature ?? 0.3,
    enabled: initial?.enabled ?? true,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const toggleToolset = (tool: string) => {
    const tools = form.enabled_toolsets || [];
    setForm({
      ...form,
      enabled_toolsets: tools.includes(tool) ? tools.filter((t) => t !== tool) : [...tools, tool],
    });
  };

  const handleSubmit = async () => {
    if (!form.name?.trim() || !form.model_ref) {
      setError(t('subagent._e125'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (isEdit && initial) {
        const updateData: SubAgentUpdate = { ...form };
        await subAgentApi.update(initial.id, updateData);
        addToast(t('subagent._e126'), 'success');
      } else {
        await subAgentApi.create(form);
        addToast(t('subagent._e127'), 'success');
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('channels.saveFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm">
      <div className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-2xl border border-border-subtle bg-card-bg p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-foreground">
            {isEdit ? t('subagent._e128') : t('subagent._e129')}
          </h3>
          <button type="button" onClick={onClose} className="rounded-lg p-1 text-foreground-dim hover:bg-card-bg-hover">
            <X className="h-4 w-4" />
          </button>
        </div>
        {error && (
          <div className="mb-3 flex items-center gap-2 rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            <AlertCircle className="h-4 w-4 shrink-0" /> {error}
          </div>
        )}
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('subagent._e75')}</label>
            <div className="flex flex-wrap gap-1.5">
              {AVATAR_PRESETS.map((ic) => (
                <button
                  key={ic}
                  type="button"
                  onClick={() => setForm({ ...form, icon: ic })}
                  className={`flex h-9 w-9 items-center justify-center rounded-xl border text-lg ${
                    form.icon === ic
                      ? 'border-brand-purple/40 bg-brand-purple/15'
                      : 'border-border-subtle hover:border-border-default'
                  }`}
                >
                  {ic}
                </button>
              ))}
              <input
                value={form.icon}
                onChange={(e) => setForm({ ...form, icon: e.target.value.slice(0, 8) })}
                className="h-9 w-16 rounded-xl border border-border-default bg-input-bg px-2 text-center text-sm"
                maxLength={8}
                title={t('subagent._e76')}
              />
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">
              任务名称 <span className="text-red-400">*</span>
            </label>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
              placeholder={t('subagent._e77')}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('subagent._e78')}</label>
            <input
              value={form.description || ''}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
              placeholder={t('subagent._e79')}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">
              模型 <span className="text-red-400">*</span>
              <span className="ml-1 font-normal text-foreground-dim">（Settings 已配模型池）</span>
            </label>
            <ModelSelector
              inventory={inventory}
              value={form.model_ref}
              onChange={(ref) => setForm({ ...form, model_ref: ref })}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">System Prompt</label>
            <textarea
              value={form.system_prompt || ''}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              className="min-h-[120px] w-full resize-y rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
              placeholder={t('subagent._e80')}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('subagent._e81')}</label>
            <div className="flex flex-wrap gap-2">
              {TOOLSET_OPTIONS.map((tool) => {
                const on = (form.enabled_toolsets || []).includes(tool.value);
                return (
                  <button
                    key={tool.value}
                    type="button"
                    onClick={() => toggleToolset(tool.value)}
                    className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs ${
                      on
                        ? 'border-brand-purple/30 bg-brand-purple/15 text-brand-cyan'
                        : 'border-border-subtle text-foreground-dim hover:border-border-default'
                    }`}
                  >
                    <span>{tool.icon}</span> {tool.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('subagent._e82')}</label>
              <input
                type="number"
                min={1}
                max={50}
                value={form.max_iterations || 5}
                onChange={(e) => setForm({ ...form, max_iterations: parseInt(e.target.value, 10) || 5 })}
                className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-foreground-muted">
                温度 {form.temperature}
              </label>
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={form.temperature ?? 0.3}
                onChange={(e) => setForm({ ...form, temperature: parseFloat(e.target.value) })}
                className="mt-2 w-full"
              />
            </div>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-border-default px-4 py-2 text-sm text-foreground-muted hover:bg-card-bg-hover"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading}
            className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {loading ? t('memory.saving') : t('kb.save')}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 主对话集群模式用的多选面板 */
export function ClusterModePanel({
  agents,
  selectedIds,
  onToggle,
  compact,
}: {
  agents: SubAgent[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  compact?: boolean;
}) {
  const enabledAgents = agents.filter((a) => a.enabled);

  return (
    <div
      className={`rounded-xl border border-brand-purple/25 bg-brand-purple/5 ${
        compact ? 'p-2.5' : 'p-4'
      }`}
    >
      <div className="mb-2 flex items-center gap-2">
        <Users className="h-3.5 w-3.5 text-brand-cyan" />
        <span className="text-xs font-medium text-foreground">{t('subagent._e83')}</span>
        <span className="text-[10px] text-foreground-dim">
          已选 {selectedIds.length}/{enabledAgents.length}
        </span>
      </div>
      {enabledAgents.length === 0 ? (
        <div className="py-3 text-center text-xs text-foreground-dim">
          暂无可用子代理，请先在侧栏「子代理」中配置
        </div>
      ) : (
        <div className={`grid gap-1.5 ${compact ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1'}`}>
          {enabledAgents.map((agent) => {
            const on = selectedIds.includes(agent.id);
            return (
              <label
                key={agent.id}
                className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
                  on
                    ? 'border-brand-purple/35 bg-brand-purple/10'
                    : 'border-transparent hover:bg-card-bg-hover'
                }`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onToggle(agent.id)}
                  className="rounded border-border-default"
                />
                <span className="text-base leading-none">{agent.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-foreground">{agent.name}</div>
                  <div className="truncate font-mono text-[10px] text-foreground-dim">
                    {agent.model_ref}
                  </div>
                </div>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function SubAgentPanel() {
  const t = useT();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const addToast = useToastStore((s) => s.addToast);
  const [agents, setAgents] = useState<SubAgent[]>([]);
  const [inventory, setInventory] = useState<ModelInventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [formDialog, setFormDialog] = useState<SubAgent | null | 'new'>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [agentList, invResp] = await Promise.all([subAgentApi.list(), modelInventoryApi.list()]);
      setAgents(Array.isArray(agentList.data) ? agentList.data : []);
      setInventory(invResp.data?.inventory || []);
    } catch (e) {
      console.error('Failed to load SubAgent data:', e);
      addToast(t('subagent._e130'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
      loadData();
      const onSettings = (e: Event) => {
        const detail = (e as CustomEvent).detail || [];
        if (
          !detail.length ||
          detail.some((k: string) =>
            ['active_provider_id', 'active_model', 'llm_provider', 'llm_model', 'llm_base_url', 'llm_model_catalog'].includes(
              k
            )
          )
        ) {
          void loadData();
        }
      };
      window.addEventListener('takton:settings-changed', onSettings);
      return () => window.removeEventListener('takton:settings-changed', onSettings);
    }, [loadData]);

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await subAgentApi.update(id, { enabled });
      loadData();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm(t('subagent._e131'));
    if (!ok) return;
    try {
      await subAgentApi.delete(id);
      addToast(t('channels.deleted'), 'success');
      loadData();
    } catch (e) {
      console.error(e);
    }
  };

  const filtered = search
    ? agents.filter(
        (a) =>
          a.name.toLowerCase().includes(search.toLowerCase()) ||
          (a.description || '').toLowerCase().includes(search.toLowerCase())
      )
    : agents;

  const builtin = filtered.filter((a) => a.is_builtin);
  const custom = filtered.filter((a) => !a.is_builtin);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <RefreshCw className="h-6 w-6 animate-spin text-foreground-dim" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-brand-cyan" />
            <h2 className="text-lg font-semibold text-foreground">{t('nav.profiles')}</h2>
            <span className="text-xs text-foreground-dim">{agents.length} 个</span>
          </div>
          <p className="mt-1 text-xs text-foreground-dim">
            为每个子代理配置任务名、模型与 system prompt；主对话开启「集群模式」后可多选协作
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-foreground-dim" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-44 rounded-xl border border-border-default bg-input-bg py-1.5 pl-8 pr-3 text-sm text-foreground"
              placeholder={t('subagent._e84')}
            />
          </div>
          <button
            type="button"
            onClick={() => setFormDialog('new')}
            className="inline-flex items-center gap-1 rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-3 py-1.5 text-sm font-medium text-white"
          >
            <Plus className="h-4 w-4" /> 新建
          </button>
        </div>
      </div>

      {inventory.length > 0 && (
        <div className="rounded-xl border border-border-subtle bg-card-bg/40 p-3">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
            模型池
          </div>
          <div className="flex flex-wrap gap-1.5">
            {inventory.slice(0, 16).map((item) => (
              <span
                key={item.ref}
                className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] ${
                  item.status === 'active'
                    ? 'bg-emerald-500/15 text-emerald-300'
                    : 'bg-card-bg-hover text-foreground-muted'
                }`}
              >
                {item.provider_icon} {item.model_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {builtin.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-medium text-foreground-dim">{t('subagent._e85')}</h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {builtin.map((agent) => (
              <CharacterCard
                key={agent.id}
                agent={agent}
                onEdit={(a) => setFormDialog(a)}
                onDelete={handleDelete}
                onToggle={handleToggle}
              />
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="mb-2 text-xs font-medium text-foreground-dim">{t('subagent._e86')}</h3>
        {custom.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {custom.map((agent) => (
              <CharacterCard
                key={agent.id}
                agent={agent}
                onEdit={(a) => setFormDialog(a)}
                onDelete={handleDelete}
                onToggle={handleToggle}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-border-subtle py-12 text-center text-sm text-foreground-dim">
            {search ? t('subagent._e132') : t('subagent._e133')}
          </div>
        )}
      </section>

      {formDialog !== null && (
        <SubAgentFormDialog
          initial={formDialog === 'new' ? undefined : formDialog}
          inventory={inventory}
          onClose={() => setFormDialog(null)}
          onSaved={loadData}
        />
      )}
      {ConfirmDialogComponent}
    </div>
  );
}
