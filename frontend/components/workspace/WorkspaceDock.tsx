'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileTree } from '@/components/filetree/FileTree';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { FileTreeItem } from '@/types';
import { useT } from '@/stores/localeStore';

/** 将 workspace tree 转为 FileTree 组件结构 */
function toFileTreeItems(
  nodes: WorkspaceTreeNode[]
): FileTreeItem[] {
  return nodes.map((n) => ({
    name: n.name,
    path: n.path,
    type: n.type === 'directory' ? 'directory' : 'file',
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

export function WorkspaceDock() {
  const t = useT();
  const {
    uiMode,
    dockOpen,
    setDockOpen,
    root,
    name,
    tree,
    treeLoading,
    selectedPath,
    selectPath,
    refreshTree,
    tabs,
    activeTabId,
    setActiveTab,
    addShellTab,
    closeTab,
    runCommand,
    unreadTerminal,
    clearUnread,
    setForceProjectOpen,
  } = useWorkspaceStore();

  const [split, setSplit] = useState(48); // % height for file tree
  const [cmd, setCmd] = useState('');
  const termEndRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    if (uiMode === 'pro' && root) {
      refreshTree().catch(() => null);
    }
  }, [uiMode, root, refreshTree]);

  useEffect(() => {
    termEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [tabs, activeTabId]);

  const onSplitterDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startY = e.clientY;
    const startSplit = split;
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const panel = document.getElementById('takton-workspace-dock');
      if (!panel) return;
      const rect = panel.getBoundingClientRect();
      const dy = ev.clientY - startY;
      const pct = startSplit + (dy / rect.height) * 100;
      setSplit(Math.min(75, Math.max(25, pct)));
    };
    const onUp = () => {
      dragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [split]);

  if (uiMode !== 'pro' || !dockOpen) return null;

  const active = tabs.find((t) => t.id === activeTabId) || tabs[0];
  const items = toFileTreeItems(tree as WorkspaceTreeNode[]);

  return (
    <aside
      id="takton-workspace-dock"
      className="flex h-full w-[min(100%,380px)] shrink-0 flex-col border-l border-border-subtle bg-card-bg/95"
    >
      {/* 顶栏 */}
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">
          {name || root || t('workspace._e163')}
        </span>
        <button
          type="button"
          onClick={() => setForceProjectOpen(true)}
          className="rounded-md border border-border-subtle px-2 py-0.5 text-[10px] text-foreground-muted hover:bg-card-bg-hover"
          title={t('workspace._e32')}
        >
          切换
        </button>
        <button
          type="button"
          onClick={() => refreshTree()}
          className="rounded-md px-1.5 py-0.5 text-[10px] text-foreground-dim hover:text-foreground"
          title={t('workspace._e33')}
        >
          ↻
        </button>
        <button
          type="button"
          onClick={() => setDockOpen(false)}
          className="rounded-md px-1.5 py-0.5 text-[10px] text-foreground-dim hover:text-foreground"
          title={t('workspace._e34')}
        >
          ⟩
        </button>
      </div>

      {/* 上：文件树 */}
      <div className="min-h-[100px] min-h-0 overflow-auto" style={{ height: `${split}%` }}>
        <div className="px-2 py-1.5 text-[10px] font-medium uppercase tracking-wider text-foreground-dim">
          项目文件
        </div>
        {!root ? (
          <p className="px-3 py-6 text-center text-[11px] text-foreground-dim">
            请先选择项目文件夹
          </p>
        ) : treeLoading ? (
          <p className="px-3 py-4 text-[11px] text-foreground-dim">加载中…</p>
        ) : (
          <FileTree
            items={items}
            selectedPath={selectedPath || undefined}
            onSelectFile={(p) => selectPath(p)}
          />
        )}
      </div>

      {/* 分割条 */}
      <div
        role="separator"
        onMouseDown={onSplitterDown}
        className="h-1.5 shrink-0 cursor-row-resize border-y border-border-subtle bg-page-bg hover:bg-brand-purple/20"
      />

      {/* 下：终端 */}
      <div className="flex min-h-0 flex-1 flex-col" style={{ height: `${100 - split}%` }}>
        <div className="flex items-center gap-0.5 overflow-x-auto border-b border-border-subtle bg-page-bg/80 px-1 py-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setActiveTab(t.id);
                clearUnread();
              }}
              className={`group flex max-w-[120px] items-center gap-1 rounded-md px-2 py-1 text-[11px] ${
                activeTabId === t.id
                  ? 'bg-brand-purple/15 text-brand-cyan'
                  : 'text-foreground-dim hover:bg-card-bg-hover'
              }`}
            >
              <span className="truncate">{t.title}</span>
              {t.kind === 'shell' && (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    closeTab(t.id);
                  }}
                  className="ml-0.5 hidden text-[10px] group-hover:inline"
                >
                  ×
                </span>
              )}
            </button>
          ))}
          <button
            type="button"
            onClick={addShellTab}
            className="rounded-md px-2 py-1 text-[11px] text-foreground-dim hover:bg-card-bg-hover"
            title={t('workspace._e35')}
          >
            +
          </button>
          {unreadTerminal && (
            <span className="ml-auto mr-1 h-1.5 w-1.5 rounded-full bg-brand-cyan" />
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-[#0d1117] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-zinc-300">
          {active?.lines.map((line) => (
            <div
              key={line.id}
              className={
                line.type === 'in'
                  ? 'text-brand-cyan'
                  : line.type === 'err'
                    ? 'text-red-400'
                    : line.type === 'sys'
                      ? 'text-zinc-500'
                      : 'text-zinc-300'
              }
            >
              <pre className="whitespace-pre-wrap break-words font-mono">{line.text}</pre>
            </div>
          ))}
          <div ref={termEndRef} />
        </div>

        {active?.kind === 'shell' && (
          <form
            className="flex items-center gap-1 border-t border-white/5 bg-[#0d1117] px-2 py-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              const c = cmd.trim();
              if (!c || active.status === 'running') return;
              setCmd('');
              runCommand(c, active.id);
            }}
          >
            <span className="text-[11px] text-brand-cyan">$</span>
            <input
              value={cmd}
              onChange={(e) => setCmd(e.target.value)}
              disabled={!root || active.status === 'running'}
              placeholder={root ? t('workspace._e164') : t('workspace._e165')}
              className="min-w-0 flex-1 bg-transparent font-mono text-[11px] text-zinc-200 outline-none placeholder:text-zinc-600"
              spellCheck={false}
              autoComplete="off"
            />
            {active.status === 'running' && (
              <span className="text-[10px] text-amber-400">运行中…</span>
            )}
          </form>
        )}
        {active?.kind === 'agent' && (
          <div className="border-t border-white/5 bg-[#0d1117] px-2 py-1 text-[10px] text-zinc-600">
            Agent 工具输出（只读）· 新建 shell 页可手动执行命令
          </div>
        )}
      </div>
    </aside>
  );
}
