'use client';

import React, { useState, useCallback, useRef } from 'react';
import { ChatWindow } from '@/components/chat/ChatWindow';
import { MessageInput, Attachment, ChatMode } from '@/components/chat/MessageInput';
import { TaskPanel } from '@/components/tasks/TaskPanel';
import { TransparencyPanel } from '@/components/chat/TransparencyPanel';
import { GlobalSearch } from '@/components/search/GlobalSearch';
import { useSession } from '@/hooks/useSession';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useTaskStore } from '@/stores/taskStore';
import { useAuthStore } from '@/stores/authStore';
import { useSessionStore } from '@/stores/sessionStore';
import { Message, StatusUpdateMessage, StreamDeltaMessage, GoalUpdateMessage, GoalState, ToolEventMessage } from '@/types';
import { generateImage, uploadFile } from '@/lib/api';
import { generateUUID } from '@/lib/uuid';
import { useRouter } from 'next/navigation';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { WorkspaceDock } from '@/components/workspace/WorkspaceDock';
import { OpenProjectModal } from '@/components/workspace/OpenProjectModal';
import { DangerConfirmDialog } from '@/components/chat/DangerConfirmDialog';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';
import { useWsStore } from '@/stores/wsStore';

export const dynamic = 'force-dynamic';

export default function HomePage() {
  const router = useRouter();
  const { currentSession, messages, addMessage, updateMessage, createAndLoadSession, loadMessages, switchSession } = useSession();
  const { tasks } = useTaskStore();
    const { token } = useAuthStore();
    const { starredSessionIds, toggleStarredSession } = useSessionStore();
    const {
        uiMode,
        setUiMode,
        dockOpen,
        setDockOpen,
        toggleDock,
        root: workspaceRoot,
        name: workspaceName,
        setForceProjectOpen,
        appendAgentOutput,
        unreadTerminal,
        bindRoot,
      } = useWorkspaceStore();

      // 恢复持久化的项目根到后端
      React.useEffect(() => {
        if (workspaceRoot) {
          bindRoot(workspaceRoot).catch(() => null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);

    const [isTaskPanelOpen, setIsTaskPanelOpen] = useState(false);
    const [isTransparencyOpen, setIsTransparencyOpen] = useState(false);
    const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamingContent, setStreamingContent] = useState('');
    const [liveToolCalls, setLiveToolCalls] = useState<ToolCallData[]>([]);
    const [streamStatusDetail, setStreamStatusDetail] = useState<string | null>(null);
    const [isGeneratingImage, setIsGeneratingImage] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const [activeGoal, setActiveGoal] = useState<GoalState | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [editingContent, setEditingContent] = useState<string | null>(null);
  // 设备页「用此设备对话」带入的草稿
  React.useEffect(() => {
    try {
      const d = sessionStorage.getItem('takton-compose-draft');
      if (d) {
        setEditingContent(d);
        sessionStorage.removeItem('takton-compose-draft');
      }
    } catch { /* ignore */ }
  }, []);

    const [creatingSession, setCreatingSession] = useState(false);
    const { addToast } = useToastStore();
    const t = useT();


  // session 切换 / 初始化：清流式、加载历史、恢复 Goal 面板
    React.useEffect(() => {
      let cancelled = false;
      const sid = currentSession?.id;
      (async () => {
        if (cancelled) return;
        setIsStreaming(false);
        setStreamingContent('');
        setLiveToolCalls([]);
        setStreamStatusDetail(null);
        setEditingContent(null);
        setActiveGoal(null);
        if (!sid) return;
        try {
          await loadMessages(sid);
        } catch (e) {
          console.error(e);
        }
        if (cancelled) return;
        try {
          const { getSessionCheckpoint } = await import('@/lib/api');
          const cp = await getSessionCheckpoint(sid);
          if (cancelled) return;
          if (cp?.goal) {
            setActiveGoal(cp.goal);
            if (
              cp.goal.status === 'active' ||
              (cp.goal.todos && cp.goal.todos.length > 0)
            ) {
              setIsTaskPanelOpen(true);
            }
          }
        } catch (e) {
          console.error('restore goal failed', e);
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [currentSession?.id, loadMessages]);

  const handleStreamDelta = useCallback((msg: StreamDeltaMessage) => {
      setIsStreaming(true);
      setStreamingContent((prev) => prev + msg.content);
    }, []);

    const handleToolEvent = useCallback((msg: ToolEventMessage) => {
          setIsStreaming(true);
          setLiveToolCalls((prev) => {
            const idx = prev.findIndex(
              (t) => t.id === msg.tool_call_id || (t.name === msg.name && t.status === 'running')
            );
            if (msg.phase === 'start') {
              const next: ToolCallData = {
                id: msg.tool_call_id,
                name: msg.name,
                arguments: msg.arguments || {},
                status: 'running',
              };
              if (idx >= 0) {
                const copy = [...prev];
                copy[idx] = { ...copy[idx], ...next };
                return copy;
              }
              return [...prev, next];
            }
            // end
            const ended: ToolCallData = {
              id: msg.tool_call_id,
              name: msg.name,
              arguments: msg.arguments || (idx >= 0 ? prev[idx].arguments : {}),
              result: msg.result ?? undefined,
              status: msg.status === 'failed' ? 'failed' : 'completed',
            };
            if (idx >= 0) {
              const copy = [...prev];
              copy[idx] = { ...copy[idx], ...ended };
              return copy;
            }
            return [...prev, ended];
          });
          if (msg.phase === 'start') {
            setStreamStatusDetail(`${t('chat.executing')} ${msg.name}…`);
          } else {
            setStreamStatusDetail(
              msg.status === 'failed' ? `${msg.name} ${t('chat.failed')}` : `${msg.name} ${t('chat.completed')}`
            );
          }

          // D10：命令类工具镜像到专业模式 Agent 终端
          const termTools = new Set([
            'command',
            'bash',
            'shell',
            'run_command',
            'CommandTool',
            'execute_command',
          ]);
          if (termTools.has(msg.name) || /command|bash|shell/i.test(msg.name)) {
            if (msg.phase === 'start') {
              const args = msg.arguments || {};
              const cmdline =
                (args.command as string) ||
                (args.cmd as string) ||
                (args.script as string) ||
                JSON.stringify(args);
              appendAgentOutput(`$ ${cmdline}`, 'in');
            } else if (msg.result) {
              appendAgentOutput(
                String(msg.result).slice(0, 12000),
                msg.status === 'failed' ? 'err' : 'out'
              );
            }
          }
        }, [appendAgentOutput, t]);

    const handleStatusUpdate = useCallback((msg: StatusUpdateMessage) => {
      if (msg.state === 'thinking' || msg.state === 'tool_executing') {
        setIsStreaming(true);
        if (msg.detail) setStreamStatusDetail(msg.detail);
      } else if (msg.state === 'error') {
        setIsStreaming(false);
        setStreamStatusDetail(msg.detail || t('chat.error'));
      } else if (msg.state === 'idle') {
        setIsStreaming(false);
        setStreamStatusDetail(null);
        // 把残留流式文本先落地，再拉全量（含 tool 消息）
        setStreamingContent((prev) => {
          if (prev) {
            addMessage({
              id: generateUUID(),
              session_id: currentSession?.id || '',
              role: 'assistant',
              content: prev,
              tool_calls: null,
              token_count: null,
              created_at: new Date().toISOString(),
            });
          }
          return '';
        });
        setLiveToolCalls([]);
        if (currentSession?.id) {
          loadMessages(currentSession.id).catch(console.error);
        }
      }
    }, [addMessage, currentSession, loadMessages, t]);

  const handleGoalUpdate = useCallback((msg: GoalUpdateMessage) => {
      if (msg.goal) {
        setActiveGoal(msg.goal);
        // Goal 进度改在任务看板，有更新时自动打开
        if (msg.goal.status === 'active' || (msg.goal.todos && msg.goal.todos.length > 0)) {
          setIsTaskPanelOpen(true);
        }
      }
    }, []);

  const { isConnected, isConnecting, sendMessage, sendStop, waitForConnection, connect } = useWebSocket({
        sessionId: currentSession?.id || '',
        token,
        onStreamDelta: handleStreamDelta,
        onStatusUpdate: handleStatusUpdate,
        onToolEvent: handleToolEvent,
        onGoalUpdate: handleGoalUpdate,
        onError: (err) => console.error('WebSocket error:', err),
        onSettingsChanged: (keys) => {
          // 通知全局模型目录刷新（被设置页同步、多标签页切换等场景复用）
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('takton:settings-changed', { detail: keys }));
          }
        },
      });

  // 使用 useSession hook 中的 switchSession 用于全局搜索
  const { switchSession: switchSession_ } = useSession();

  // 发送消息（自动创建 session → 等 WS 就绪 → 发送）
  // 发送成功后会话将出现在「历史会话」中
  const handleSend = useCallback(
      async (
        content: string,
        attachments: Attachment[] = [],
        mode: ChatMode = 'default',
        subAgentIds?: string[]
      ) => {
        // D10 专业模式：强制项目文件夹
        if (useWorkspaceStore.getState().uiMode === 'pro' && !useWorkspaceStore.getState().root) {
          useWorkspaceStore.getState().setForceProjectOpen(true);
          return;
        }

        let session = currentSession;
        if (!session) {
          setCreatingSession(true);
          try {
            session = await createAndLoadSession();
          } catch (e) {
            console.error(t('page._e1'), e);
            addToast(t('chat.createSessionFailed'), 'error');
            return;
          } finally {
            setCreatingSession(false);
          }
          if (!session) {
            addToast(t('chat.createSessionFailed2'), 'error');
            return;
          }
        }

        // 等待该 session 的 WebSocket 就绪（新建会话后需要一点时间建连）
        const ready = await waitForConnection(session.id, 15000);
        if (!ready) {
          addToast(t('chat.channelNotConnected'), 'error');
          setIsStreaming(false);
          return;
        }

        if (mode === 'cluster' && (!subAgentIds || subAgentIds.length === 0)) {
          addToast(t('chat.clusterNeedAgent'), 'error');
          return;
        }

        let displayContent = content;
                if (attachments.length > 0) {
                  const attNames = attachments.map((a) => `[${a.filename}]`).join(' ');
                  displayContent = `${attNames}\n${content}`;
                }

        const userMsg: Message = {
          id: generateUUID(),
          session_id: session.id,
          role: 'user',
          content: displayContent,
          tool_calls: null,
          token_count: null,
          created_at: new Date().toISOString(),
        };
        addMessage(userMsg);
        const sent = sendMessage(content, attachments, mode, subAgentIds);
        if (!sent) {
          addToast(t('chat.sendFailedDisconnected'), 'error');
          return;
        }
        setIsStreaming(true);
        setStreamingContent('');
        setLiveToolCalls([]);
        setStreamStatusDetail(mode === 'cluster' ? t('chat.clusterWorking') : t('chat.thinking'));
      },
      [currentSession, addMessage, sendMessage, createAndLoadSession, waitForConnection, t]
    );

  // 重新生成
  const handleRegenerate = useCallback(
    async (_message: Message) => {
      if (!currentSession) return;
      const msgs = useSessionStore.getState().messages;
      const lastUserMsg = [...msgs].reverse().find((m) => m.role === 'user');
      if (!lastUserMsg?.content) return;
      const ready = await waitForConnection(currentSession.id, 15000);
      if (!ready) {
        addToast(t('chat.channelNotConnected2'), 'error');
        return;
      }
      if (sendMessage(lastUserMsg.content, [], 'default')) {
        setIsStreaming(true);
        setStreamingContent('');
      }
    },
    [currentSession, sendMessage, waitForConnection]
  );

  // 编辑并重新发送
  const handleEdit = useCallback(
    (message: Message) => {
      // 将内容回填到编辑状态（由 MessageInput 处理）
      setEditingContent(message.content);
    },
    []
  );

  const handleGenerateImage = useCallback(
    async (prompt: string) => {
      if (!currentSession) return;
      setIsGeneratingImage(true);

      const userMsg: Message = {
        id: generateUUID(),
        session_id: currentSession.id,
        role: 'user',
        content: `[${t('chat.imageGenTag')}] ${prompt}`,
        tool_calls: null,
        token_count: null,
        created_at: new Date().toISOString(),
      };
      addMessage(userMsg);

      try {
        const result = await generateImage(prompt, { width: 1024, height: 1024, n: 1 });
        const imageUrls = (result.images || [])
          .map((img) => {
            if (img.url && /^https?:\/\//i.test(img.url)) {
              return `![${t('chat.imageGenAlt')}](${img.url})`;
            }
            return '';
          })
          .filter(Boolean)
          .join('\n');

        const assistantContent = imageUrls || t('chat.imageGenDone');
        addMessage({
          id: generateUUID(),
          session_id: currentSession.id,
          role: 'assistant',
          content: assistantContent,
          tool_calls: null,
          token_count: null,
          created_at: new Date().toISOString(),
        });
      } catch (err) {
        console.error('Image generation failed:', err);
        addMessage({
          id: generateUUID(),
          session_id: currentSession.id,
          role: 'assistant',
          content: `[Error] ${t('chat.imageGenFailed')}: ${err instanceof Error ? err.message : String(err)}`,
          tool_calls: null,
          token_count: null,
          created_at: new Date().toISOString(),
        });
      } finally {
        setIsGeneratingImage(false);
      }
    },
    [currentSession, addMessage, t]
  );

  const handleStopStreaming = useCallback(() => {
    sendStop();
    setIsStreaming(false);
    if (streamingContent) {
      addMessage({
        id: generateUUID(),
        session_id: currentSession?.id || '',
        role: 'assistant',
        content: streamingContent,
        tool_calls: null,
        token_count: null,
        created_at: new Date().toISOString(),
      });
      setStreamingContent('');
    }
  }, [sendStop, streamingContent, addMessage, currentSession]);

  const handleTagClick = useCallback(
    (tagKey: string) => {
      if (tagKey === 'image') {
        // 图片生成模式，提示输入
        return;
      }
      // 其他模式——模式通过 MessageInput 的工具栏触发
    },
    []
  );

  // 全局搜索选择会话 → 直接进入该会话
  const handleSearchSelect = useCallback(
    async (sessionId: string) => {
      setSearchOpen(false);
      setIsStreaming(false);
      setStreamingContent('');
      if (switchSession) {
        await switchSession(sessionId);
      }
    },
    [switchSession]
  );

  // ====== Keyboard Shortcuts ======
  useKeyboardShortcuts([
    { key: 'k', meta: true, handler: () => setSearchOpen(true) },
    { key: 'Escape', handler: () => setSearchOpen(false), preventDefault: false },
    { key: 'n', meta: true, shift: true, handler: () => createAndLoadSession().catch(console.error) },
    { key: ',', meta: true, handler: () => router.push('/settings') },
    { key: '/', meta: true, handler: () => setSearchOpen(true) },
    { key: 'b', ctrl: true, handler: () => toggleDock() },
    { key: 'Enter', meta: true, handler: () => { const textarea = document.querySelector<HTMLTextAreaElement>('.chat-composer-textarea'); if (textarea && !textarea.disabled) { const form = textarea.closest('form'); form?.requestSubmit(); } }, preventDefault: true },
  ]);

  // ====== Drag & Drop ======
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Only hide if leaving the main container
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);

      const files = e.dataTransfer.files;
      if (!files || files.length === 0 || !currentSession) return;

      // Upload each file and create attachment message
      for (const file of Array.from(files)) {
        try {
          const result = await uploadFile(file);
          const content = `[${t('chat.attachment')}: ${result.filename}](${result.url})`;
          addMessage({
            id: generateUUID(),
            session_id: currentSession.id,
            role: 'user',
            content,
            tool_calls: null,
            token_count: null,
            created_at: new Date().toISOString(),
          });
        } catch (err) {
          console.error('Upload failed:', err);
        }
      }
    },
    [currentSession, addMessage, t]
  );

  const displayMessages = [...messages];
    // 实时气泡：文本 + tool call 边产生边展示（不要等 idle 整包刷）
    if (isStreaming || streamingContent || liveToolCalls.length > 0) {
      const liveToolCallsForMsg =
        liveToolCalls.length > 0
          ? liveToolCalls.map((tc) => ({
              id: tc.id,
              name: tc.name,
              arguments: tc.arguments,
              result: tc.result,
              status: tc.status,
            }))
          : null;
      let liveContent = streamingContent;
      if (!liveContent && streamStatusDetail && liveToolCalls.length === 0) {
        liveContent = '';
      }
      displayMessages.push({
        id: 'streaming',
        session_id: currentSession?.id || '',
        role: 'assistant',
        content: liveContent || (liveToolCalls.length ? '' : streamStatusDetail ? `_${streamStatusDetail}_` : ''),
        tool_calls: liveToolCallsForMsg as Message['tool_calls'],
        token_count: null,
        created_at: new Date().toISOString(),
      });
    }

  return (
    <div
      className="flex h-full min-h-0 flex-1 flex-col relative overflow-hidden"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Global Search Modal */}
      <GlobalSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSelectSession={handleSearchSelect}
      />

      {/* Drag & Drop Overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-page-bg/80 backdrop-blur-sm border-2 border-dashed border-brand-purple/40 rounded-lg">
          <div className="text-center">
            <svg className="mx-auto h-12 w-12 text-brand-purple/60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <p className="mt-3 text-sm font-medium text-foreground-muted">{t('chat.dropToUpload')}</p>
          </div>
        </div>
      )}

      {/* 顶部状态栏 */}
            <header className="flex items-center justify-between border-b border-border-subtle/50 bg-page-bg/80 backdrop-blur-xl px-5 py-2.5 sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <h1 className="text-[0.8125rem] font-semibold tracking-tight text-foreground">Chat</h1>
                {currentSession && (
                  <span className="chat-meta font-mono text-foreground-dim">
                    {currentSession.id.slice(0, 8)}
                  </span>
                )}
                {uiMode === 'pro' && (
                  <button
                    type="button"
                    onClick={() => setForceProjectOpen(true)}
                    className="max-w-[200px] truncate rounded-full border border-border-subtle bg-card-bg px-2.5 py-0.5 text-[11px] text-foreground-muted hover:border-brand-purple/40"
                    title={workspaceRoot || t('chat.selectProjectTitle')}
                  >
                    📁 {workspaceName || workspaceRoot || t('chat.selectProject')}
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
                              {/* 简洁 / 专业 */}
                              <div className="flex rounded-lg border border-border-subtle p-0.5 text-[11px]">
                                <button
                                  type="button"
                                  onClick={() => setUiMode('simple')}
                                  className={`rounded-md px-2 py-1 ${
                                    uiMode === 'simple'
                                      ? 'bg-brand-purple/15 text-brand-cyan'
                                      : 'text-foreground-dim hover:text-foreground'
                                  }`}
                                >
                                  {t('chat.simple')}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setUiMode('pro')}
                                  className={`rounded-md px-2 py-1 ${
                                    uiMode === 'pro'
                                      ? 'bg-brand-purple/15 text-brand-cyan'
                                      : 'text-foreground-dim hover:text-foreground'
                                  }`}
                                >
                                  {t('chat.pro')}
                                </button>
                              </div>
                              {uiMode === 'pro' && (
                                <button
                                  type="button"
                                  onClick={toggleDock}
                                  className="relative rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-foreground-muted hover:bg-card-bg-hover"
                                  title={t('chat.dockTitle')}
                                >
                                  {dockOpen ? t('chat.hideDock') : t('chat.showDock')}
                                  {unreadTerminal && !dockOpen && (
                                    <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-cyan" />
                                  )}
                                </button>
                              )}
                              {/* 仅在「有会话却未连上」时提示，避免与 TitleBar「服务就绪」重复 */}
                              {!!currentSession && !isConnected && !isConnecting && (
                                <button
                                  type="button"
                                  onClick={() => connect()}
                                  className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-500/15"
                                  title={t('chat.reconnectTitle')}
                                >
                                  {t('chat.reconnect')}
                                </button>
                              )}
                              {isConnecting && (
                                <span className="text-[11px] text-foreground-dim">{t('chat.connecting')}</span>
                              )}
                              {isGeneratingImage && (
                                <span className="flex items-center gap-1.5 text-xs text-brand-cyan">
                                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-cyan" />
                                  {t('chat.generatingImage')}
                                </span>
                              )}
                              <button
                                onClick={() => setIsTransparencyOpen(true)}
                                className="rounded-lg border border-border-subtle bg-card-bg px-3.5 py-1.5 text-xs font-medium text-foreground-muted transition-all hover:border-border-default hover:bg-card-bg-hover"
                              >
                                {t('chat.transparency')}
                              </button>
                              <button
                                onClick={() => setIsTaskPanelOpen(true)}
                                className="relative rounded-lg border border-border-subtle bg-card-bg px-3.5 py-1.5 text-xs font-medium text-foreground-muted transition-all hover:border-border-default hover:bg-card-bg-hover"
                              >
                                {t('chat.taskBoard')}
                                {activeGoal &&
                                  (activeGoal.status === 'active' ||
                                    (activeGoal.todos && activeGoal.todos.length > 0)) && (
                                    <span className="absolute -right-1 -top-1 flex h-2.5 w-2.5">
                                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-cyan opacity-60" />
                                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-brand-cyan" />
                                    </span>
                                  )}
                              </button>
                            </div>
            </header>

            {/* 主内容区：消息可滚动 + 底部固定 composer（防输入框被盖住） */}
                  <div className="relative flex min-h-0 flex-1 overflow-hidden">
                    <main className="chat-main-column">
                      <div className="chat-messages-pane">
                        <ChatWindow
                          messages={displayMessages}
                          isStreaming={isStreaming}
                          onStopStreaming={handleStopStreaming}
                          onTagClick={handleTagClick}
                          onRegenerate={handleRegenerate}
                          onEdit={handleEdit}
                          onExampleSelect={(text) => setEditingContent(text)}
                        />
                      </div>
                      {!isConnected && !isConnecting && !!currentSession && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-card-bg/60 px-3 py-1.5 text-[11px] text-foreground-dim">
                          <span>{t('chat.channelIdle')}</span>
                        </div>
                      )}
                      <MessageInput
                                              key={editingContent ?? 'default'}
                                              onSend={handleSend}
                                              onGenerateImage={handleGenerateImage}
                                              disabled={isStreaming || isGeneratingImage || creatingSession}
                                              isStreaming={isStreaming}
                                              onStopStreaming={handleStopStreaming}
                                              placeholder={
                                                creatingSession
                                                  ? t('chat.creating')
                                                  : isStreaming
                                                    ? t('chat.aiReplying')
                                                    : uiMode === 'pro' && !workspaceRoot
                                                      ? t('chat.proSelectProject')
                                                      : !currentSession
                                                        ? t('chat.inputHint')
                                                        : isConnecting
                                                          ? t('chat.connectingCanSend')
                                                          : !isConnected
                                                            ? t('chat.sendAutoConnect')
                                                            : t('chat.send')
                                              }
                                              initialContent={editingContent ?? undefined}
                                              onClearEdit={() => setEditingContent(null)}
                                            />
                    </main>

                    <WorkspaceDock />

                    {/* 任务面板抽屉：Goal + 已进行操作（可跳转会话） */}
                                        <TaskPanel
                                          messages={messages}
                                          liveToolCalls={liveToolCalls}
                                          isOpen={isTaskPanelOpen}
                                          onClose={() => setIsTaskPanelOpen(false)}
                                          goal={activeGoal}
                                          onClearGoal={() => setActiveGoal(null)}
                                          highlightedMessageId={highlightMessageId}
                                          onJumpToMessage={(messageId) => {
                                            if (messageId === 'streaming') {
                                              const el = document.getElementById('msg-streaming');
                                              el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                              return;
                                            }
                                            const el = document.getElementById(`msg-${messageId}`);
                                            if (!el) return;
                                            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                            setHighlightMessageId(messageId);
                                            el.classList.remove('msg-flash');
                                            void el.offsetWidth;
                                            el.classList.add('msg-flash');
                                            window.setTimeout(() => {
                                              el.classList.remove('msg-flash');
                                              setHighlightMessageId(null);
                                            }, 1600);
                                          }}
                                        />
                                        <TransparencyPanel
                                          sessionId={currentSession?.id || ''}
                                          visible={isTransparencyOpen}
                                          onClose={() => setIsTransparencyOpen(false)}
                                        />
                                      </div>

                                      <OpenProjectModal />
                                      <DangerConfirmDialog />
                                    </div>
                                  );
                                }
