'use client';

import React, { useEffect, useState } from 'react';
import { Tool } from '@/types';
import {
  getTools,
  createTool,
  updateTool,
  toggleTool,
  deleteTool,
  executeTool,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

const TYPE_LABELS: Record<string, string> = {
  browser: '浏览器',
  command: '命令行',
  file_read: '文件读取',
  file_write: '文件写入',
  http: 'HTTP 请求',
  python: 'Python',
  search: '网络搜索',
  edit: '文件编辑',
  glob: '文件匹配',
  grep: '文本搜索',
  sqlite_query: 'SQLite',
};

const TYPE_COLORS: Record<string, string> = {
  browser: 'bg-brand-purple/10 text-brand-purple',
  command: 'bg-card-bg-hover text-foreground-muted',
  file_read: 'bg-success-bg text-success-text',
  file_write: 'bg-orange-500/10 text-orange-500',
  http: 'bg-purple-100 text-purple-700',
  python: 'bg-amber-500/10 text-amber-500',
  search: 'bg-cyan-100 text-cyan-700',
  edit: 'bg-pink-100 text-pink-700',
  glob: 'bg-indigo-100 text-indigo-700',
  grep: 'bg-teal-100 text-teal-700',
  sqlite_query: 'bg-lime-100 text-lime-700',
};

export default function ToolsPage() {
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editingTool, setEditingTool] = useState<Tool | null>(null);
  const [executingTool, setExecutingTool] = useState<Tool | null>(null);
  const [execArgs, setExecArgs] = useState('{}');
  const [execResult, setExecResult] = useState('');

  // 新建表单
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newType, setNewType] = useState<'http'>('http');
  const [newConfig, setNewConfig] = useState('{}');

  useEffect(() => {
    loadTools();
  }, []);

  async function loadTools() {
    setLoading(true);
    try {
      const data = await getTools();
      setTools(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  const filtered = tools.filter(
    (t) =>
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.description.toLowerCase().includes(search.toLowerCase())
  );

  const builtinTools = filtered.filter((t) => t.is_builtin);
  const customTools = filtered.filter((t) => !t.is_builtin);

  async function handleToggle(tool: Tool) {
    try {
      await toggleTool(tool.id, !tool.enabled);
      setTools((prev) =>
        prev.map((t) => (t.id === tool.id ? { ...t, enabled: !t.enabled } : t))
      );
    } catch (e) {
      addToast('切换失败', 'error');
      console.error(e);
    }
  }

  async function handleDelete(tool: Tool) {
    const ok = await confirm(`确定删除工具 "${tool.name}"？`);
    if (!ok) return;
    try {
      await deleteTool(tool.id);
      setTools((prev) => prev.filter((t) => t.id !== tool.id));
    } catch (e) {
      addToast('删除失败', 'error');
      console.error(e);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    try {
      const config = JSON.parse(newConfig);
      await createTool({
        name: newName,
        description: newDesc,
        type: newType,
        config,
        enabled: true,
      });
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      setNewConfig('{}');
      await loadTools();
    } catch (e) {
      addToast('创建失败：' + (e as Error).message, 'error');
    }
  }

  async function handleUpdate(e: React.FormEvent) {
    e.preventDefault();
    if (!editingTool) return;
    try {
      const config = JSON.parse(newConfig);
      await updateTool(editingTool.id, {
        description: newDesc,
        config,
      });
      setEditingTool(null);
      setNewDesc('');
      setNewConfig('{}');
      await loadTools();
    } catch (e) {
      addToast('更新失败：' + (e as Error).message, 'error');
    }
  }

  async function handleExecute() {
    if (!executingTool) return;
    try {
      const args = JSON.parse(execArgs);
      const res = await executeTool(executingTool.id, args);
      setExecResult(res.result);
    } catch (e) {
      setExecResult('[Error] ' + (e as Error).message);
    }
  }

  function openEdit(tool: Tool) {
    setEditingTool(tool);
    setNewDesc(tool.description);
    setNewConfig(JSON.stringify(tool.config, null, 2));
  }

  function openExecute(tool: Tool) {
    setExecutingTool(tool);
    setExecArgs('{}');
    setExecResult('');
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Tools</h1>
          <p className="mt-1 text-sm text-foreground-dim">
            管理 Agent 可调用的工具。内置工具由系统提供，自定义工具可配置外部 HTTP API。
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
        >
          + 新建工具
        </button>
      </div>

      {/* 搜索 */}
      <div className="mb-4">
        <input
          type="text"
          placeholder="搜索工具..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-lg border border-border-default px-4 py-2 text-sm focus:border-violet-500 focus:outline-none"
        />
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-foreground-muted">加载中...</div>
      ) : (
        <div className="space-y-8">
          {/* 内置工具 */}
          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              内置工具
            </h2>
            <div className="divide-y divide-gray-100 rounded-lg border border-border-default bg-card-bg">
              {builtinTools.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-foreground-muted">
                  无匹配的内置工具
                </div>
              )}
              {builtinTools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  onToggle={() => handleToggle(tool)}
                  onEdit={() => openEdit(tool)}
                  onExecute={() => openExecute(tool)}
                  onDelete={() => handleDelete(tool)}
                />
              ))}
            </div>
          </section>

          {/* 自定义工具 */}
          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              自定义工具
            </h2>
            <div className="divide-y divide-gray-100 rounded-lg border border-border-default bg-card-bg">
              {customTools.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-foreground-muted">
                  暂无自定义工具
                </div>
              )}
              {customTools.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  onToggle={() => handleToggle(tool)}
                  onEdit={() => openEdit(tool)}
                  onExecute={() => openExecute(tool)}
                  onDelete={() => handleDelete(tool)}
                />
              ))}
            </div>
          </section>
        </div>
      )}

      {/* 新建弹窗 */}
      {showCreate && (
        <Modal onClose={() => setShowCreate(false)} title="新建工具">
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">名称</label>
              <input
                required
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                placeholder="my_api_tool"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">描述</label>
              <input
                required
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                placeholder="此工具用于..."
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">类型</label>
              <select
                value={newType}
                onChange={(e) => setNewType(e.target.value as 'http')}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
              >
                <option value="http">HTTP 请求</option>
              </select>
              <p className="mt-1 text-xs text-foreground-muted">
                当前仅支持 HTTP 类型自定义工具。如需其他类型，请联系管理员。
              </p>
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">配置 (JSON)</label>
              <ConfigExample />
              <textarea
                value={newConfig}
                onChange={(e) => setNewConfig(e.target.value)}
                rows={6}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 font-mono text-xs focus:border-violet-500 focus:outline-none"
                placeholder={`{\n  "method": "GET",\n  "url": "https://api.example.com/data",\n  "headers": {\n    "Authorization": "Bearer xxx"\n  }\n}`}
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-md border border-border-default px-4 py-2 text-sm text-foreground-dim hover:bg-elevated-bg"
              >
                取消
              </button>
              <button
                type="submit"
                className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                创建
              </button>
            </div>
          </form>
        </Modal>
      )}

      {/* 编辑弹窗 */}
      {editingTool && (
        <Modal onClose={() => setEditingTool(null)} title={`编辑: ${editingTool.name}`}>
          <form onSubmit={handleUpdate} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">描述</label>
              <input
                required
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">配置 (JSON)</label>
              <textarea
                value={newConfig}
                onChange={(e) => setNewConfig(e.target.value)}
                rows={8}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 font-mono text-xs focus:border-violet-500 focus:outline-none"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setEditingTool(null)}
                className="rounded-md border border-border-default px-4 py-2 text-sm text-foreground-dim hover:bg-elevated-bg"
              >
                取消
              </button>
              <button
                type="submit"
                className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                保存
              </button>
            </div>
          </form>
        </Modal>
      )}

      {/* 执行弹窗 */}
      {executingTool && (
        <Modal onClose={() => setExecutingTool(null)} title={`执行: ${executingTool.name}`}>
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">参数 (JSON)</label>
              <textarea
                value={execArgs}
                onChange={(e) => setExecArgs(e.target.value)}
                rows={4}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 font-mono text-xs focus:border-violet-500 focus:outline-none"
                placeholder={`{\n  "url": "https://example.com"\n}`}
              />
            </div>
            <button
              onClick={handleExecute}
              className="w-full rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
            >
              执行
            </button>
            {execResult && (
              <div className="rounded-md border border-border-default bg-elevated-bg p-3">
                <div className="mb-1 text-xs font-medium text-foreground-dim">结果</div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs text-foreground-muted">
                  {execResult}
                </pre>
              </div>
            )}
          </div>
        </Modal>
      )}

      {ConfirmDialogComponent}
    </div>
  );
}

        function ToolRow({
          tool,
          onToggle,
          onEdit,
          onExecute,
          onDelete,
        }: {
          tool: Tool;
          onToggle: () => void;
          onEdit: () => void;
          onExecute: () => void;
          onDelete: () => void;
        }) {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900">{tool.name}</span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${TYPE_COLORS[tool.type] || 'bg-card-bg-hover text-foreground-muted'}`}
          >
            {TYPE_LABELS[tool.type] || tool.type}
          </span>
          {tool.is_builtin && (
            <span className="rounded bg-card-bg-hover px-1.5 py-0.5 text-[10px] text-foreground-dim">
              内置
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-foreground-dim">{tool.description}</p>
      </div>

      <div className="flex items-center gap-2">
        {/* 执行按钮 */}
        <button
          onClick={onExecute}
          className="rounded p-1.5 text-foreground-muted hover:bg-card-bg-hover hover:text-foreground-dim"
          title="执行"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>

        {/* 编辑按钮 */}
        <button
          onClick={onEdit}
          className="rounded p-1.5 text-foreground-muted hover:bg-card-bg-hover hover:text-foreground-dim"
          title="编辑"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
        </button>

        {/* 启用开关 */}
        <button
          type="button"
          role="switch"
          aria-checked={tool.enabled}
          onClick={onToggle}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            tool.enabled ? 'bg-violet-600' : 'bg-elevated-bg'
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-card-bg transition-transform ${
              tool.enabled ? 'translate-x-[1.125rem]' : 'translate-x-[0.125rem]'
            }`}
          />
        </button>

        {/* 删除按钮 */}
        {onDelete && (
          <button
            onClick={onDelete}
            className="rounded p-1.5 text-foreground-muted hover:bg-error-bg hover:text-error-text"
            title="删除"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

function ConfigExample() {
  const [open, setOpen] = useState(false);
  const examples = [
    {
      title: 'GET 请求（固定 URL）',
      code: `{
  "method": "GET",
  "url": "https://api.example.com/v1/status",
  "headers": {
    "Authorization": "Bearer sk-xxxx"
  }
}`,
    },
    {
      title: 'POST 请求（固定 URL + 动态 body）',
      code: `{
  "method": "POST",
  "url": "https://api.example.com/v1/translate",
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-xxxx"
  }
}`,
    },
    {
      title: '动态 URL（由 Agent 传入）',
      code: `{
  "method": "GET",
  "headers": {
    "User-Agent": "Takton-Agent/1.0"
  }
}`,
    },
  ];

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-violet-400 hover:text-violet-300"
      >
        {open ? '隐藏配置范例' : '查看配置范例'}
      </button>
      {open && (
        <div className="mt-2 max-h-60 space-y-3 overflow-y-auto rounded-md border border-gray-100 bg-elevated-bg p-3">
          {examples.map((ex) => (
            <div key={ex.title}>
              <div className="mb-1 text-xs font-medium text-foreground-dim">{ex.title}</div>
              <pre className="overflow-auto rounded bg-card-bg p-2 text-[11px] leading-relaxed text-foreground-muted">
                {ex.code}
              </pre>
            </div>
          ))}
          <div className="text-[11px] text-foreground-dim">
            <p className="font-medium">配置字段说明：</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              <li><code>method</code>：HTTP 方法（GET/POST/PUT/DELETE/PATCH）</li>
              <li><code>url</code>：固定请求地址（不传则由 Agent 在执行参数中提供）</li>
              <li><code>headers</code>：固定请求头（会与执行时的 headers 合并）</li>
              <li><code>timeout</code>：超时秒数（默认 30）</li>
            </ul>
            <p className="mt-1.5 font-medium">执行参数说明：</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              <li><code>url</code>：请求地址（覆盖配置中的 url）</li>
              <li><code>headers</code>：额外请求头（与配置合并）</li>
              <li><code>body</code>：请求体（仅 POST/PUT/PATCH）</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Modal({
  children,
  onClose,
  title,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-lg border border-border-default bg-card-bg p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
          <button
            onClick={onClose}
            className="rounded p-1 text-foreground-muted hover:bg-card-bg-hover hover:text-foreground-dim"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
