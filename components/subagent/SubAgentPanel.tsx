'use client';

/**
 * SubAgentPanel - 阶段6: 子代理集群管理面板
 *
 * 功能：
 * 1. 子代理列表（内置模板 + 用户自定义）
 * 2. 新建/编辑子代理（模型从 Inventory 下拉选）
 * 3. 模型池状态可视化
 * 4. 集群模式开关（聊天界面集成用）
 */

import React, { useCallback, useEffect, useState } from 'react';
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

// ────────────────── 工具集选项 ──────────────────

const TOOLSET_OPTIONS = [
  { value: 'file', label: '文件读写', icon: '📁' },
  { value: 'terminal', label: '终端执行', icon: '💻' },
  { value: 'git', label: 'Git 操作', icon: '🔀' },
  { value: 'web', label: 'Web 搜索', icon: '🌐' },
  { value: 'browser', label: '浏览器', icon: '🖥️' },
  { value: 'code', label: '代码执行', icon: '⚡' },
];

// ────────────────── 子组件：模型选择器 ──────────────────

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

  const groups: Record<string, ModelInventoryItem[]> = {};
  for (const item of inventory) {
    const group = item.status === 'active' || item.status === 'default'
      ? '推荐'
      : item.status === 'fallback'
        ? '备用'
        : '其他可用';
    groups[group] = groups[group] || [];
    groups[group].push(item);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between border rounded px-3 py-1.5 text-sm dark:bg-gray-800 hover:border-indigo-400"
      >
        <span className="flex items-center gap-2">
          {selected ? (
            <>
              <span>{selected.provider_icon}</span>
              <span>{selected.provider_name} · {selected.model_name}</span>
              {selected.status === 'active' && <span className="text-xs text-green-500">当前</span>}
              {selected.status === 'default' && <span className="text-xs text-blue-500">默认</span>}
            </>
          ) : (
            <span className="text-gray-400">选择模型...</span>
          )}
        </span>
        <ChevronDown className="w-4 h-4 text-gray-400" />
      </button>

      {open && (
        <div className="absolute z-50 w-full mt-1 bg-white dark:bg-gray-900 border rounded shadow-lg max-h-64 overflow-auto">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group}>
              <div className="px-3 py-1 text-xs font-medium text-gray-400 bg-gray-50 dark:bg-gray-800 sticky top-0">{group}</div>
              {items.map((item) => (
                <button
                  key={item.ref}
                  type="button"
                  onClick={() => { onChange(item.ref); setOpen(false); }}
                  className={`w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800 ${item.ref === value ? 'bg-indigo-50 dark:bg-indigo-900/20' : ''}`}
                >
                  <span className="flex items-center gap-2">
                    <span>{item.provider_icon}</span>
                    <span>{item.provider_name}</span>
                    <span className="text-gray-400">·</span>
                    <span className="font-mono text-xs">{item.model_name}</span>
                  </span>
                  <span className="flex items-center gap-1">
                    {!item.connected && <span className="text-xs text-yellow-500">⚠️</span>}
                    {item.ref === value && <Check className="w-3 h-3 text-indigo-500" />}
                  </span>
                </button>
              ))}
            </div>
          ))}
          {inventory.length === 0 && (
            <div className="px-3 py-4 text-center text-gray-400 text-sm">暂无可用模型，请先在 Settings 配置服务商</div>
          )}
        </div>
      )}
    </div>
  );
}

// ────────────────── 子组件：子代理卡片 ──────────────────

function SubAgentCard({
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
  return (
    <div className={`border rounded-lg p-4 bg-white dark:bg-gray-900 shadow-sm ${!agent.enabled ? 'opacity-60' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xl">{agent.icon}</span>
          <span className="font-medium text-sm">{agent.name}</span>
          {agent.is_builtin && <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500">内置</span>}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => onToggle(agent.id, !agent.enabled)} className={`p-1.5 rounded ${agent.enabled ? 'text-green-500 hover:bg-green-50 dark:hover:bg-green-900/20' : 'text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}`} title={agent.enabled ? '禁用' : '启用'}>
            <Power className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => onEdit(agent)} className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded" title="编辑">
            <Edit3 className="w-3.5 h-3.5" />
          </button>
          {!agent.is_builtin && (
            <button onClick={() => onDelete(agent.id)} className="p-1.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded" title="删除">
              <Trash2 className="w-3.5 h-3.5 text-red-500" />
            </button>
          )}
        </div>
      </div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mb-2">{agent.description}</div>
      <div className="flex items-center gap-3 text-xs text-gray-400">
        <span className="font-mono">{agent.model_ref}</span>
        <span>·</span>
        <span>工具: {agent.enabled_toolsets.join(', ') || '无'}</span>
        <span>·</span>
        <span>温度: {agent.temperature}</span>
      </div>
    </div>
  );
}

// ────────────────── 子组件：创建/编辑对话框 ──────────────────

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
  const [form, setForm] = useState<SubAgentCreate>({
    name: initial?.name || '',
    description: initial?.description || '',
    icon: initial?.icon || '🤖',
    model_ref: initial?.model_ref || '',
    system_prompt: initial?.system_prompt || '',
    enabled_toolsets: initial?.enabled_toolsets || [],
    max_iterations: initial?.max_iterations || 5,
    temperature: initial?.temperature || 0.3,
    enabled: initial?.enabled ?? true,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const toggleToolset = (tool: string) => {
    const tools = form.enabled_toolsets || [];
    setForm({ ...form, enabled_toolsets: tools.includes(tool) ? tools.filter((t) => t !== tool) : [...tools, tool] });
  };

  const handleSubmit = async () => {
    if (!form.name || !form.model_ref) {
      setError('名称和模型不能为空');
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (isEdit && initial) {
        const updateData: SubAgentUpdate = { ...form };
        await subAgentApi.update(initial.id, updateData);
      } else {
        await subAgentApi.create(form);
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-900 rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-auto">
        <h3 className="text-lg font-semibold mb-4">{isEdit ? '编辑子代理' : '新建子代理'}</h3>
        {error && <div className="flex items-center gap-2 text-red-600 text-sm mb-3"><AlertCircle className="w-4 h-4" /> {error}</div>}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1">名称</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" placeholder="例: 代码审查员" />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">图标</label>
              <input value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" maxLength={8} />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">描述</label>
            <input value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" placeholder="简短描述子代理的职责" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">模型 <span className="text-gray-400">（从已配模型池选择）</span></label>
            <ModelSelector inventory={inventory} value={form.model_ref} onChange={(ref) => setForm({ ...form, model_ref: ref })} />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">系统提示词</label>
            <textarea value={form.system_prompt || ''} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} className="w-full border rounded px-3 py-2 text-sm dark:bg-gray-800 min-h-[100px]" placeholder="定义子代理的角色和行为..." />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">工具集</label>
            <div className="flex flex-wrap gap-2">
              {TOOLSET_OPTIONS.map((tool) => (
                <button key={tool.value} type="button" onClick={() => toggleToolset(tool.value)} className={`flex items-center gap-1 px-2 py-1 rounded text-xs border ${(form.enabled_toolsets || []).includes(tool.value) ? 'bg-indigo-50 dark:bg-indigo-900/20 border-indigo-300 text-indigo-700 dark:text-indigo-300' : 'bg-gray-50 dark:bg-gray-800 border-gray-200 text-gray-500'} text-xs border`}>
                  <span>{tool.icon}</span> {tool.label}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1">最大工具轮次</label>
              <input type="number" value={form.max_iterations || 5} onChange={(e) => setForm({ ...form, max_iterations: parseInt(e.target.value) || 5 })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" min={1} max={50} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">创意度 (Temperature): {form.temperature}</label>
              <input type="range" value={form.temperature || 0.3} onChange={(e) => setForm({ ...form, temperature: parseFloat(e.target.value) })} className="w-full" min={0} max={2} step={0.1} />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50 dark:hover:bg-gray-800">取消</button>
          <button onClick={handleSubmit} disabled={loading} className="px-4 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50">{loading ? '保存中...' : '保存'}</button>
        </div>
      </div>
    </div>
  );
}

// ────────────────── 子组件：集群模式面板 ──────────────────

export function ClusterModePanel({
  agents,
  selectedIds,
  onToggle,
}: {
  agents: SubAgent[];
  selectedIds: string[];
  onToggle: (id: string) => void;
}) {
  const enabledAgents = agents.filter((a) => a.enabled);

  return (
    <div className="border rounded-lg p-4 bg-white dark:bg-gray-900 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <Users className="w-4 h-4 text-indigo-500" />
        <span className="font-medium text-sm">集群模式</span>
        <span className="text-xs text-gray-400">已选 {selectedIds.length}/{enabledAgents.length} 个子代理</span>
      </div>
      {enabledAgents.length === 0 ? (
        <div className="text-center text-gray-400 text-sm py-4">暂无可用子代理，请先在 /subagents 页面配置</div>
      ) : (
        <div className="space-y-2">
          {enabledAgents.map((agent) => (
            <label key={agent.id} className={`flex items-center gap-3 px-3 py-2 rounded cursor-pointer ${selectedIds.includes(agent.id) ? 'bg-indigo-50 dark:bg-indigo-900/20' : 'hover:bg-gray-50 dark:hover:bg-gray-800'}`}>
              <input type="checkbox" checked={selectedIds.includes(agent.id)} onChange={() => onToggle(agent.id)} className="rounded" />
              <span className="text-lg">{agent.icon}</span>
              <div className="flex-1">
                <div className="text-sm font-medium">{agent.name}</div>
                <div className="text-xs text-gray-400 font-mono">{agent.model_ref}</div>
              </div>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ────────────────── 主组件 ──────────────────

export default function SubAgentPanel() {
  const [agents, setAgents] = useState<SubAgent[]>([]);
  const [inventory, setInventory] = useState<ModelInventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [formDialog, setFormDialog] = useState<SubAgent | null | 'new'>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [agentList, invResp] = await Promise.all([
        subAgentApi.list(),
        modelInventoryApi.list(),
      ]);
      setAgents(agentList.data);
      setInventory(invResp.data.inventory);
    } catch (e) {
      console.error('Failed to load SubAgent data:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleToggle = async (id: string, enabled: boolean) => {
    try { await subAgentApi.update(id, { enabled }); loadData(); } catch (e) { console.error('Toggle subagent failed:', e); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此子代理？')) return;
    try { await subAgentApi.delete(id); loadData(); } catch (e) { console.error('Delete subagent failed:', e); }
  };

  const filtered = search
    ? agents.filter((a) => a.name.toLowerCase().includes(search.toLowerCase()) || a.description.toLowerCase().includes(search.toLowerCase()))
    : agents;

  const builtin = filtered.filter((a) => a.is_builtin);
  const custom = filtered.filter((a) => !a.is_builtin);

  if (loading) {
    return <div className="flex items-center justify-center h-64"><RefreshCw className="w-6 h-6 animate-spin text-gray-400" /></div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-indigo-500" />
          <h2 className="text-lg font-semibold">子代理管理</h2>
          <span className="text-xs text-gray-400">{agents.length} 个</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} className="border rounded pl-8 pr-3 py-1.5 text-sm dark:bg-gray-800 w-48" placeholder="搜索子代理..." />
          </div>
          <button onClick={() => setFormDialog('new')} className="flex items-center gap-1 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700">
            <Plus className="w-4 h-4" /> 新建
          </button>
        </div>
      </div>

      {/* 模型池状态概览 */}
      {inventory.length > 0 && (
        <div className="border rounded-lg p-3 bg-gray-50 dark:bg-gray-800/50">
          <div className="text-xs font-medium text-gray-500 mb-2">模型池状态</div>
          <div className="flex flex-wrap gap-2">
            {inventory.map((item) => (
              <span key={item.ref} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${item.status === 'active' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' : item.status === 'default' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' : item.status === 'fallback' ? 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'}`}>
                {item.provider_icon} {item.model_name} {!item.connected && ' ⚠️'}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 内置模板 */}
      {builtin.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-500 mb-2">内置模板</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {builtin.map((agent) => <SubAgentCard key={agent.id} agent={agent} onEdit={(a) => setFormDialog(a)} onDelete={handleDelete} onToggle={handleToggle} />)}
          </div>
        </div>
      )}

      {/* 用户自定义 */}
      {custom.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-500 mb-2">自定义子代理</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {custom.map((agent) => <SubAgentCard key={agent.id} agent={agent} onEdit={(a) => setFormDialog(a)} onDelete={handleDelete} onToggle={handleToggle} />)}
          </div>
        </div>
      )}

      {filtered.length === 0 && (
        <div className="text-center text-gray-400 py-8">{search ? '未找到匹配的子代理' : '暂无子代理，点击"新建"创建'}</div>
      )}

      {formDialog !== null && (
        <SubAgentFormDialog initial={formDialog === 'new' ? undefined : formDialog} inventory={inventory} onClose={() => setFormDialog(null)} onSaved={loadData} />
      )}
    </div>
  );
}
