'use client';

/**
 * 专业模式工作区侧栏：文件树 + Agent/Shell 终端 tab
 * 由 workspaceStore 驱动；Ctrl+B / 顶栏「显示侧栏」切换
 */

import React, { useEffect, useRef, useState } from 'react';
import { FolderOpen, Plus, RefreshCw, ChevronRight, X } from 'lucide-react';
import { FileTree } from '@/components/filetree/FileTree';
import type { FileTreeItem } from '@/types';
import { useWorkspaceStore, type TerminalLine } from '@/stores/workspaceStore';
import { useT } from '@/stores/localeStore';

function toFileTreeItems(
  nodes: Array<{
    name: string;
    path: string;
    type: string;
    children?: WorkspaceTreeNode[];
    size?: number;
  }>
): FileTreeItem[] {
  return nodes.map((n) => ({
    name: n.name,
    path: n.path,
    type: n.type === 'directory' || n.type === 'dir' ? 'directory' : 'file',
    size: n.size,
    children: n.children ? toFileTreeItems(n.children) : undefined,
  }));
}

type WorkspaceTreeNode = {
  name: string;
  path: string;
  type: string;
  children?: WorkspaceTreeNode[];
  size?: number;
};

function lineClass(type: TerminalLine['type']): string {
  switch (type) {
    case 'in':
      return 'text-cyan-300';
    case 'err':
      return 'text-red-300/90';
    case 'sys':
      return 'text-zinc-500 italic';
    default:
      return 'text-zinc-300';
  }
}

export function WorkspaceDock() {
  const t = useT();
  const uiMode = useWorkspaceStore((s) => s.uiMode);
  const dockOpen = useWorkspaceStore((s) => s.dockOpen);
  const setDockOpen = useWorkspaceStore((s) => s.setDockOpen);
  const root = useWorkspaceStore((s) => s.root);
  const name = useWorkspaceStore((s) => s.name);
  const tree = useWorkspaceStore((s) => s.tree);
  const treeLoading = useWorkspaceStore((s) => s.treeLoading);
  const selectedPath = useWorkspaceStore((s) => s.selectedPath);
  const selectPath = useWorkspaceStore((s) => s.selectPath);
  const refreshTree = useWorkspaceStore((s) => s.refreshTree);
  const setForceProjectOpen = useWorkspaceStore((s) => s.setForceProjectOpen);
  const tabs = useWorkspaceStore((s) => s.tabs);
  const activeTabId = useWorkspaceStore((s) => s.activeTabId);
  const setActiveTab = useWorkspaceStore((s) => s.setActiveTab);
  const addShellTab = useWorkspaceStore((s) => s.addShellTab);
  const closeTab = useWorkspaceStore((s) => s.closeTab);
  const runCommand = useWorkspaceStore((s) => s.runCommand);
  const clearUnread = useWorkspaceStore((s) => s.clearUnread);

  const [cmd, setCmd] = useState('');
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeTab = tabs.find((tab) => tab.id === activeTabId) || tabs[0];
  const lines = activeTab?.lines ?? [];

  useEffect(() => {
    if (uiMode === 'pro' && root) {
      void refreshTree();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uiMode, root]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length, activeTabId]);

  if (uiMode !== 'pro' || !dockOpen) return null;

  const treeItems = toFileTreeItems(tree as WorkspaceTreeNode[]);
  const shellActive = activeTab?.kind === 'shell';

  const submitCmd = async () => {
    const c = cmd.trim();
    if (!c || !shellActive) return;
    setCmd('');
    await runCommand(c, activeTabId);
  };

  return (
    <aside className="flex w-[min(380px,42vw)] min-w-[280px] max-w-[480px] shrink-0 flex-col border-l border-border-subtle bg-zinc-950/95">
      {/* 项目头 */}
      <div className="flex items-center gap-1.5 border-b border-zinc-800 px-2.5 py-2">
        <button
          type="button"
          onClick={() => setForceProjectOpen(true)}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-zinc-900"
          title={root || t('workspace._e32')}
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-brand-cyan" />
          <span className="min-w-0 truncate text-[11px] font-medium text-zinc-200">
            {name || root || t('workspace._e163')}
          </span>
        </button>
        <button
          type="button"
          onClick={() => void refreshTree()}
          disabled={!root || treeLoading}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300 disabled:opacity-40"
          title={t('workspace._e33')}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${treeLoading ? 'animate-spin' : ''}`} />
        </button>
        <button
          type="button"
          onClick={() => setDockOpen(false)}
          className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
          title={t('workspace._e34')}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 文件树 */}
      <div className="min-h-0 flex-1 overflow-y-auto border-b border-zinc-800">
        {!root ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 py-8 text-center">
            <p className="text-[11px] text-zinc-500">{t('workspace._e163')}</p>
            <button
              type="button"
              onClick={() => setForceProjectOpen(true)}
              className="rounded-lg border border-brand-purple/40 bg-brand-purple/10 px-3 py-1.5 text-[11px] font-medium text-brand-cyan hover:bg-brand-purple/15"
            >
              {t('chat.selectProject') === 'chat.selectProject'
                ? '选择项目'
                : t('chat.selectProject')}
            </button>
          </div>
        ) : treeLoading && treeItems.length === 0 ? (
          <div className="px-3 py-6 text-center text-[11px] text-zinc-600">…</div>
        ) : (
          <FileTree
            items={treeItems}
            selectedPath={selectedPath || undefined}
            onSelectFile={(p) => selectPath(p)}
          />
        )}
      </div>

      {/* 终端 tabs */}
      <div className="flex h-[42%] min-h-[160px] max-h-[280px] flex-col">
        <div className="flex items-center gap-0.5 overflow-x-auto border-b border-zinc-800 px-1 py-1">
          {tabs.map((tab) => (
            <div
              key={tab.id}
              className={`group flex shrink-0 items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[10px] ${
                tab.id === activeTabId
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setActiveTab(tab.id);
                  clearUnread();
                }}
                className="max-w-[96px] truncate"
              >
                {tab.title}
                {tab.status === 'running' ? ' ·' : ''}
              </button>
              {tab.id !== 'agent' ? (
                <button
                  type="button"
                  onClick={() => closeTab(tab.id)}
                  className="rounded p-0.5 opacity-0 group-hover:opacity-100 hover:bg-zinc-700"
                  aria-label="Close tab"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              ) : null}
            </div>
          ))}
          <button
            type="button"
            onClick={addShellTab}
            className="ml-0.5 rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
            title={t('workspace._e35')}
          >
            <Plus className="h-3 w-3" />
          </button>
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 py-1.5 font-mono text-[10.5px] leading-relaxed"
        >
          {lines.length === 0 ? (
            <div className="py-4 text-center text-zinc-600">
              {shellActive ? t('workspace._e164') : 'Agent'}
            </div>
          ) : (
            lines.map((line) => (
              <div
                key={line.id}
                className={`whitespace-pre-wrap break-all ${lineClass(line.type)}`}
              >
                {line.text}
              </div>
            ))
          )}
        </div>

        {shellActive ? (
          <form
            className="flex items-center gap-1 border-t border-zinc-800 px-2 py-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              void submitCmd();
            }}
          >
            <span className="select-none text-[11px] text-emerald-500">$</span>
            <input
              value={cmd}
              onChange={(e) => setCmd(e.target.value)}
              disabled={!root || activeTab?.status === 'running'}
              placeholder={root ? t('workspace._e164') : t('workspace._e165')}
              className="min-w-0 flex-1 bg-transparent font-mono text-[11px] text-zinc-200 outline-none placeholder:text-zinc-600 disabled:opacity-50"
              autoComplete="off"
              spellCheck={false}
            />
          </form>
        ) : null}
      </div>
    </aside>
  );
}
