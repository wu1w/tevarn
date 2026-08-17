'use client';

/**
 * 对话右侧唯一工作区：项目文件 / 预览 / 终端 / 运行 / 轨迹。
 * 一次只开一栏，避免多个抽屉并排。
 */
import React, { useEffect, useRef } from 'react';
import { Eye, FolderOpen, ListTodo, ScanSearch, Terminal, X } from 'lucide-react';
import type { Message, GoalState } from '@/types';
import type { ChatArtifact } from '@/lib/artifacts';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';
import { FilePreviewHost } from '@/components/chat/FilePreviewHost';
import { TerminalPanel } from '@/components/chat/TerminalPanel';
import { TransparencyPanel } from '@/components/chat/TransparencyPanel';
import { TaskPanel } from '@/components/tasks/TaskPanel';
import { WorkspaceDock } from '@/components/workspace/WorkspaceDock';
import { ColResizer } from '@/components/ui/ColResizer';
import { useColResize } from '@/hooks/useColResize';
import { maxRightPanelWidth } from '@/lib/colResize';
import {
  useChatInspectorStore,
  type ChatInspectorTab,
} from '@/stores/chatInspectorStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useTerminalStore } from '@/stores/terminalStore';
import { useT } from '@/stores/localeStore';

const TAB_ICONS: Record<ChatInspectorTab, React.ComponentType<{ className?: string }>> = {
  files: FolderOpen,
  preview: Eye,
  terminal: Terminal,
  run: ListTodo,
  trace: ScanSearch,
};

export function ChatInspector({
  uiMode,
  previewArtifact,
  onClosePreview,
  messages,
  liveToolCalls,
  sessionId,
  goal,
  onClearGoal,
  highlightedMessageId,
  onJumpToMessage,
}: {
  uiMode: 'simple' | 'pro';
  previewArtifact: ChatArtifact | null;
  onClosePreview: () => void;
  messages: Message[];
  liveToolCalls: ToolCallData[];
  sessionId?: string;
  goal?: GoalState | null;
  onClearGoal?: () => void;
  highlightedMessageId?: string | null;
  onJumpToMessage?: (messageId: string) => void;
}) {
  const t = useT();
  const tab = useChatInspectorStore((s) => s.tab);
  const setTab = useChatInspectorStore((s) => s.setTab);
  const panelRef = useRef<HTMLElement | null>(null);
  const inspectResize = useColResize({
    storageKey: 'tk-inspect-w',
    defaultWidth: 360,
    min: 260,
    max: () => maxRightPanelWidth(panelRef.current),
    edge: 'left',
  });

  useEffect(() => {
    if (uiMode !== 'pro' && tab === 'files') setTab(null);
  }, [uiMode, tab, setTab]);

  useEffect(() => {
    const unsub = useTerminalStore.subscribe((s, prev) => {
      if (s.panelOpen && prev && !prev.panelOpen && useChatInspectorStore.getState().tab == null) {
        useChatInspectorStore.getState().setTab('terminal');
      }
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (uiMode === 'pro' && useWorkspaceStore.getState().dockOpen) {
      if (useChatInspectorStore.getState().tab == null) {
        useChatInspectorStore.getState().setTab('files');
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const close = () => {
    setTab(null);
    useWorkspaceStore.getState().setDockOpen(false);
    useTerminalStore.getState().setPanelOpen(false);
  };

  if (!tab) return null;

  const tabs: Array<{ id: ChatInspectorTab; label: string; hidden?: boolean }> = [
    { id: 'files', label: t('chat.inspectorFiles'), hidden: uiMode !== 'pro' },
    { id: 'preview', label: t('chat.inspectorPreview') },
    { id: 'terminal', label: t('terminal.toggle') },
    { id: 'run', label: t('chat.inspectorRun') },
    { id: 'trace', label: t('chat.inspectorTrace') },
  ];
  const current = tabs.find((item) => item.id === tab);
  const TitleIcon = TAB_ICONS[tab];

  return (
    <aside
      ref={panelRef as React.RefObject<HTMLElement>}
      className="relative flex min-w-[260px] shrink-0 flex-col border-l border-border-subtle bg-card-bg"
      style={{ width: inspectResize.width, flex: '0 1 auto' }}
      data-testid="chat-inspector"
    >
      <ColResizer
        className="tk-edge-resizer"
        label={t('layout.resizeDrawer' as never)}
        onStart={inspectResize.onStart}
        onDrag={inspectResize.onDrag}
        onEnd={inspectResize.onEnd}
        onDoubleClick={inspectResize.onReset}
      />
      <div className="flex h-8 shrink-0 items-center gap-1.5 border-b border-border-subtle px-2">
        <TitleIcon className="h-3.5 w-3.5 text-brand-cyan" />
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground">
          {current?.label}
        </span>
        <button
          type="button"
          onClick={close}
          className="inline-flex h-7 w-7 items-center justify-center rounded-[3px] text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
          title={t('chat.inspectorClose')}
          aria-label={t('chat.inspectorClose')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === 'files' ? <WorkspaceDock embedded /> : null}
        {tab === 'preview' ? (
          <FilePreviewHost
            embedded
            artifact={previewArtifact}
            onClose={() => {
              onClosePreview();
              setTab(uiMode === 'pro' ? 'files' : 'run');
            }}
          />
        ) : null}
        {tab === 'terminal' ? <TerminalPanel embedded /> : null}
        {tab === 'run' ? (
          <TaskPanel
            embedded
            isOpen
            messages={messages}
            liveToolCalls={liveToolCalls}
            onClose={close}
            goal={goal}
            onClearGoal={onClearGoal}
            highlightedMessageId={highlightedMessageId}
            onJumpToMessage={onJumpToMessage}
          />
        ) : null}
        {tab === 'trace' ? (
          <TransparencyPanel embedded sessionId={sessionId || ''} visible onClose={close} />
        ) : null}
      </div>
    </aside>
  );
}
