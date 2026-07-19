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
import { useT } from '@/stores/localeStore';

export const dynamic = 'force-dynamic';

const TYPE_KEYS = [
  'browser',
  'command',
  'file_read',
  'file_write',
  'http',
  'python',
  'search',
  'edit',
  'glob',
  'grep',
  'sqlite_query',
] as const;

/** 类型标签统一弱化：不再彩虹配色 */
const TYPE_BADGE =
  'rounded border border-border-subtle bg-elevated-bg/80 px-1.5 py-0.5 text-[10px] font-medium text-foreground-muted';

/** 功能分类（列表分组 + 筛选） */
type ToolCategory = 'file' | 'exec' | 'network' | 'data' | 'other';

const TOOL_CATEGORY_IDS = ['all', 'file', 'exec', 'network', 'data', 'other'] as const;

const TYPE_TO_CATEGORY: Record<string, ToolCategory> = {
  file_read: 'file',
  file_write: 'file',
  edit: 'file',
  glob: 'file',
  grep: 'file',
  command: 'exec',
  python: 'exec',
  browser: 'network',
  http: 'network',
  search: 'network',
  sqlite_query: 'data',
};

function toolCategory(tool: { type: string; name: string }): ToolCategory {
  if (TYPE_TO_CATEGORY[tool.type]) return TYPE_TO_CATEGORY[tool.type];
  const n = tool.name.toLowerCase();
  if (/file|edit|glob|grep|read|write|fs/.test(n)) return 'file';
  if (/command|shell|bash|python|exec|run/.test(n)) return 'exec';
  if (/http|browser|search|web|fetch|api/.test(n)) return 'network';
  if (/sql|db|data|vector|embed/.test(n)) return 'data';
  return 'other';
}


export default function ToolsPage() {
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const t = useT();
  const typeLabel = (type: string) =>
    (TYPE_KEYS as readonly string[]).includes(type) ? t(`tools.type.${type}` as never) : type;
  const catLabel = (id: ToolCategory | 'all') => t(`tools.cat.${id}` as never);
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<ToolCategory | 'all'>('all');
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

  const filtered = tools.filter((t) => {
    const q = search.toLowerCase();
    const matchQ =
      !q ||
      t.name.toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      typeLabel(t.type).includes(search);
    const cat = toolCategory(t);
    const matchCat = categoryFilter === 'all' || cat === categoryFilter;
    return matchQ && matchCat;
  });

  const builtinTools = filtered.filter((t) => t.is_builtin);
  const customTools = filtered.filter((t) => !t.is_builtin);

  function groupByCategory(list: Tool[]) {
    const order: ToolCategory[] = ['file', 'exec', 'network', 'data', 'other'];
    const map = new Map<ToolCategory, Tool[]>();
    for (const c of order) map.set(c, []);
    for (const tool of list) {
      const c = toolCategory(tool);
      map.get(c)!.push(tool);
    }
    return order
      .map((id) => ({
        id,
        label: catLabel(id),
        items: map.get(id) || [],
      }))
      .filter((g) => g.items.length > 0);
  }

  async function handleToggle(tool: Tool) {
    try {
      await toggleTool(tool.id, !tool.enabled);
      setTools((prev) =>
        prev.map((t) => (t.id === tool.id ? { ...t, enabled: !t.enabled } : t))
      );
    } catch (e) {
      addToast(t('tools.toggleFailed'), 'error');
      console.error(e);
    }
  }

  async function handleDelete(tool: Tool) {
    const ok = await confirm(t('tools.confirmDelete').replace('{name}', tool.name));
    if (!ok) return;
    try {
      await deleteTool(tool.id);
      setTools((prev) => prev.filter((t) => t.id !== tool.id));
    } catch (e) {
      addToast(t('tools.deleteFailed'), 'error');
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
      addToast(t('tools.createFailed') + (e as Error).message, 'error');
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
      addToast(t('tools.updateFailed') + (e as Error).message, 'error');
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
          <h1 className="text-2xl font-bold text-foreground">Tools</h1>
          <p className="mt-1 text-sm text-foreground-dim">
            {t('tools.subtitle')}
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
        >
          {t('tools.newTool')}
        </button>
      </div>

      {/* 搜索 + 分类 */}
      <div className="mb-4 space-y-3">
        <input
          type="text"
          placeholder={t('tools.searchPlaceholder')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-lg border border-border-default bg-input-bg px-4 py-2 text-sm text-foreground focus:border-violet-500 focus:outline-none"
        />
        <div className="flex flex-wrap gap-1.5">
          {TOOL_CATEGORY_IDS.map((id) => {
            const active = categoryFilter === id;
            const count =
              id === 'all'
                ? tools.length
                : tools.filter((x) => toolCategory(x) === id).length;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setCategoryFilter(id)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                  active
                    ? 'border-brand-purple/40 bg-brand-purple/10 text-foreground'
                    : 'border-border-subtle bg-card-bg text-foreground-muted hover:border-border-default hover:text-foreground'
                }`}
              >
                {catLabel(id)}
                <span className="ml-1 tabular-nums text-foreground-dim">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-foreground-muted">{t('common.loading')}</div>
      ) : (
        <div className="space-y-8">
          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              {t('tools.builtin')}
              <span className="ml-2 font-normal normal-case text-foreground-dim">
                {builtinTools.length}
              </span>
            </h2>
            {builtinTools.length === 0 ? (
              <div className="rounded-lg border border-border-default bg-card-bg px-4 py-8 text-center text-sm text-foreground-muted">
                {t('tools.noMatchBuiltin')}
              </div>
            ) : (
              <div className="space-y-4">
                {groupByCategory(builtinTools).map((g) => (
                  <div key={g.id}>
                    <div className="mb-1.5 flex items-center gap-2 px-0.5">
                      <span className="text-xs font-medium text-foreground-muted">{g.label}</span>
                      <span className="h-px flex-1 bg-border-subtle" />
                      <span className="text-[10px] text-foreground-dim">{g.items.length}</span>
                    </div>
                    <div className="divide-y divide-border-subtle rounded-lg border border-border-default bg-card-bg">
                      {g.items.map((tool) => (
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
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-muted">
              {t('tools.custom')}
              <span className="ml-2 font-normal normal-case text-foreground-dim">
                {customTools.length}
              </span>
            </h2>
            <div className="divide-y divide-border-subtle rounded-lg border border-border-default bg-card-bg">
              {customTools.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-foreground-muted">
                  {t('tools.noCustom')}
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
        <Modal onClose={() => setShowCreate(false)} title={t('tools.modal.create')}>
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.name')}</label>
              <input
                required
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                placeholder="my_api_tool"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.desc')}</label>
              <input
                required
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                placeholder={t('tools.form.descPlaceholder')}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.type')}</label>
              <select
                value={newType}
                onChange={(e) => setNewType(e.target.value as 'http')}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
              >
                <option value="http">{t('tools.type.http')}</option>
              </select>
              <p className="mt-1 text-xs text-foreground-muted">
                {t('tools.form.httpOnly')}
              </p>
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.config')}</label>
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
                {t('common.cancel')}
              </button>
              <button
                type="submit"
                className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                {t('tools.create')}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {/* 编辑弹窗 */}
      {editingTool && (
        <Modal onClose={() => setEditingTool(null)} title={t('tools.modal.edit').replace('{name}', editingTool.name)}>
          <form onSubmit={handleUpdate} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.desc')}</label>
              <input
                required
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.config')}</label>
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
                {t('common.cancel')}
              </button>
              <button
                type="submit"
                className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
              >
                {t('common.save')}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {/* 执行弹窗 */}
      {executingTool && (
        <Modal onClose={() => setExecutingTool(null)} title={t('tools.modal.execute').replace('{name}', executingTool.name)}>
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-foreground-muted">{t('tools.form.args')}</label>
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
              {t('tools.execute')}
            </button>
            {execResult && (
              <div className="rounded-md border border-border-default bg-elevated-bg p-3">
                <div className="mb-1 text-xs font-medium text-foreground-dim">{t('tools.form.result')}</div>
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
  const t = useT();
  const typeLabel = (type: string) =>
    (TYPE_KEYS as readonly string[]).includes(type) ? t(`tools.type.${type}` as never) : type;
  const catLabel = (id: ToolCategory | 'all') => t(`tools.cat.${id}` as never);
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground">{tool.name}</span>
          <span
            className={TYPE_BADGE}
          >
            {typeLabel(tool.type)}
          </span>
          <span className={TYPE_BADGE}>
            {catLabel(toolCategory(tool))}
          </span>
          {tool.is_builtin && (
            <span className={TYPE_BADGE}>{t('tools.badge.builtin')}</span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-foreground-dim">{tool.description}</p>
      </div>

      <div className="flex items-center gap-2">
        {/* 执行按钮 */}
        <button
          onClick={onExecute}
          className="rounded p-1.5 text-foreground-muted hover:bg-card-bg-hover hover:text-foreground-dim"
          title={t('tools.execute')}
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
          title={t('common.edit')}
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
            title={t('common.delete')}
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
  const t = useT();
  const examples = [
    {
      title: t('tools.cfg.ex1'),
      code: `{
  "method": "GET",
  "url": "https://api.example.com/v1/status",
  "headers": {
    "Authorization": "Bearer sk-xxxx"
  }
}`,
    },
    {
      title: t('tools.cfg.ex2'),
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
      title: t('tools.cfg.ex3'),
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
        {open ? t('tools.cfg.hide') : t('tools.cfg.show')}
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
            <p className="font-medium">{t('tools.cfg.fieldsTitle')}</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              <li><code>method</code>: {t('tools.cfg.method')}</li>
              <li><code>url</code>: {t('tools.cfg.url')}</li>
              <li><code>headers</code>: {t('tools.cfg.headers')}</li>
              <li><code>timeout</code>: {t('tools.cfg.timeout')}</li>
            </ul>
            <p className="mt-1.5 font-medium">{t('tools.cfg.argsTitle')}</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              <li><code>url</code>: {t('tools.cfg.argUrl')}</li>
              <li><code>headers</code>: {t('tools.cfg.argHeaders')}</li>
              <li><code>body</code>: {t('tools.cfg.argBody')}</li>
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
          <h3 className="text-lg font-semibold text-foreground">{title}</h3>
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
