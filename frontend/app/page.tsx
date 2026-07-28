'use client';

import React, { useState, useCallback, useRef } from 'react';
import { ChatWindow } from '@/components/chat/ChatWindow';
import { MessageInput, Attachment, ChatMode, type MessageInputHandle } from '@/components/chat/MessageInput';
import { FilePreviewHost } from '@/components/chat/FilePreviewHost';
import { SessionArtifactsBar } from '@/components/chat/SessionArtifactsBar';
import type { ChatArtifact } from '@/lib/artifacts';
import { TerminalPanel, formatArgsText, formatResultText } from '@/components/chat/TerminalPanel';
import { ActivityPanel } from '@/components/chat/ActivityPanel';
import { TaskPanel } from '@/components/tasks/TaskPanel';
import { TransparencyPanel } from '@/components/chat/TransparencyPanel';
import { GlobalSearch } from '@/components/search/GlobalSearch';
import { useSession } from '@/hooks/useSession';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useTaskStore } from '@/stores/taskStore';
import { useAuthStore } from '@/stores/authStore';
import { useSessionStore } from '@/stores/sessionStore';
import { Message, StatusUpdateMessage, StreamDeltaMessage, GoalUpdateMessage, GoalState, ToolEventMessage, RunEventMessage } from '@/types';
import { useTerminalStore } from '@/stores/terminalStore';
import { generateImage } from '@/lib/api';
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
import { streamSessionApi } from '@/stores/streamSessionStore';


export default function HomePage() {
  const router = useRouter();
  const { currentSession, messages, addMessage, updateMessage, createAndLoadSession, loadMessages, switchSession } = useSession();
    const { tasks } = useTaskStore();
    const token = useAuthStore((s) => s.token);
    const starredSessionIds = useSessionStore((s) => s.starredSessionIds);
    const toggleStarredSession = useSessionStore((s) => s.toggleStarredSession);
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
      appendAgentOutputTo,
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
        // 流式正文 ref：idle/stop 时落地，避免在 setState updater 内同步写 sessionStore
        const streamingContentRef = React.useRef('');
        React.useEffect(() => {
          streamingContentRef.current = streamingContent;
        }, [streamingContent]);
        const [liveToolCalls, setLiveToolCalls] = useState<ToolCallData[]>([]);
                const [streamStatusDetail, setStreamStatusDetail] = useState<string | null>(null);
        // 实时终端面板订阅（header 开关按钮的未读点）
        const termPanelOpen = useTerminalStore((s) => s.panelOpen);
        const termHasEntries = useTerminalStore((s) => s.entries.length > 0);

            const [isGeneratingImage, setIsGeneratingImage] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const [activeGoal, setActiveGoal] = useState<GoalState | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const composerRef = useRef<MessageInputHandle | null>(null);
    const [previewArtifact, setPreviewArtifact] = useState<ChatArtifact | null>(null);

    // 开发冒烟：允许 Playwright 注入消息 / 打开预览
    React.useEffect(() => {
      if (process.env.NODE_ENV === 'production') return;
      const w = window as unknown as {
        __taktonSmoke?: {
          setPreview: (a: ChatArtifact | null) => void;
          addMessage: typeof addMessage;
          setMessages: (msgs: Message[]) => void;
        };
      };
      w.__taktonSmoke = {
        setPreview: setPreviewArtifact,
        addMessage,
        setMessages: useSessionStore.getState().setMessages,
      };
      return () => {
        delete w.__taktonSmoke;
      };
    }, [addMessage]);

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


  // session 切换：保存/恢复 per-session 流式态；运行中任务不因切页而停（后端不 cancel agent）
  const prevSessionIdRef = React.useRef<string | null | undefined>(undefined);
    React.useEffect(() => {
      let cancelled = false;
      const sid = currentSession?.id;
      const prev = prevSessionIdRef.current;
      const sessionChanged = prev !== undefined && prev !== sid;

      // 离开上一会话：把本地流式态写入 store（任务仍在后台跑）
      if (sessionChanged && prev) {
        streamSessionApi().save(prev, {
          isStreaming,
          agentRunning: isStreaming,
          content: streamingContentRef.current || streamingContent,
          tools: liveToolCalls,
          statusDetail: streamStatusDetail,
        });
      }
      prevSessionIdRef.current = sid;

      (async () => {
        if (cancelled) return;
        if (!sid) {
          setIsStreaming(false);
          setStreamingContent('');
          streamingContentRef.current = '';
          setLiveToolCalls([]);
          setStreamStatusDetail(null);
          setEditingContent(null);
          setActiveGoal(null);
          return;
        }

        if (sessionChanged) {
          setEditingContent(null);
          setActiveGoal(null);
          // 切到目标会话：先用本地缓存恢复，再等 WS sync 用服务端快照校正
          const cached = streamSessionApi().get(sid);
          if (cached.agentRunning || cached.isStreaming || cached.content || cached.tools.length) {
            setIsStreaming(true);
            setStreamingContent(cached.content || '');
            streamingContentRef.current = cached.content || '';
            setLiveToolCalls(cached.tools || []);
            setStreamStatusDetail(cached.statusDetail || 'Resuming…');
          } else {
            setIsStreaming(false);
            setStreamingContent('');
            streamingContentRef.current = '';
            setLiveToolCalls([]);
            setStreamStatusDetail(null);
          }
        }
        // 同会话 remount（从别的路由回来）：不硬清流式，交给 cache + sync

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
      // 仅 session id 驱动；避免 isStreaming 等入 deps 导致切会话时反复写 store
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentSession?.id, loadMessages]);

  // 当前会话流式态持续写入 store，跳页卸载后仍可恢复
  React.useEffect(() => {
    const sid = currentSession?.id;
    if (!sid) return;
    if (!isStreaming && !streamingContent && liveToolCalls.length === 0) return;
    streamSessionApi().save(sid, {
      isStreaming,
      agentRunning: isStreaming,
      content: streamingContent,
      tools: liveToolCalls,
      statusDetail: streamStatusDetail,
    });
  }, [currentSession?.id, isStreaming, streamingContent, liveToolCalls, streamStatusDetail]);

  const handleStreamDelta = useCallback((msg: StreamDeltaMessage) => {
      setIsStreaming(true);
      setStreamingContent((prev) => {
        // 新 message_id：替换缓冲，避免 epilogue 整段重推导致重复
        const mid = msg.message_id || '';
        const store = streamSessionApi();
        const sid = currentSession?.id || '';
        const prevMid = sid ? store.get(sid).streamMessageId : null;
        let next: string;
        if (mid && prevMid && mid !== prevMid && (msg.content || '').length > 0) {
          next = msg.content || '';
        } else {
          next = prev + (msg.content || '');
        }
        streamingContentRef.current = next;
        if (sid) {
          store.patch(sid, {
            isStreaming: true,
            agentRunning: true,
            content: next,
            streamMessageId: mid || prevMid,
          });
        }
        return next;
      });
    }, [currentSession?.id]);

    const handleToolEvent = useCallback((msg: ToolEventMessage) => {
      setIsStreaming(true);
      useTerminalStore.getState().upsert({
        callId: msg.tool_call_id,
        name: msg.name,
        argsText: formatArgsText(msg.arguments || {}),
        status: msg.phase === 'start' ? 'running' : msg.status === 'failed' ? 'failed' : 'completed',
        resultText: msg.phase === 'start' ? '' : formatResultText(msg.result ?? undefined),
      });
      setLiveToolCalls((prev) => {
        const idx = prev.findIndex(
          (t) => t.id === msg.tool_call_id || (t.name === msg.name && t.status === 'running')
        );
        let nextList: ToolCallData[];
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
            nextList = copy;
          } else {
            nextList = [...prev, next];
          }
        } else {
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
            nextList = copy;
          } else {
            nextList = [...prev, ended];
          }
        }
        const sid = currentSession?.id || '';
        if (sid) {
          streamSessionApi().patch(sid, {
            isStreaming: true,
            agentRunning: true,
            tools: nextList,
          });
        }
        return nextList;
      });
      if (msg.phase === 'start') {
        setStreamStatusDetail(`${t('chat.executing')} ${msg.name}…`);
      } else {
        setStreamStatusDetail(
          msg.status === 'failed' ? `${msg.name} ${t('chat.failed')}` : `${msg.name} ${t('chat.completed')}`
        );
      }

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
    }, [appendAgentOutput, t, currentSession?.id]);

    const lastWsToastAtRef = React.useRef(0);
    const toastWsError = useCallback(
      (err: string, opts?: { force?: boolean }) => {
        const msg = (err || '').trim() || t('chat.wsError');
        console.error('WebSocket error:', msg);
        const now = Date.now();
        const soft = /connection error|not connected|reconnect/i.test(msg)
          && !/limit reached|Invalid|creation failed|Unknown/i.test(msg);
        if (!opts?.force && soft && now - lastWsToastAtRef.current < 4000) {
          return;
        }
        lastWsToastAtRef.current = now;
        addToast(msg, soft ? 'info' : 'error');
        // 软断线：Agent 可能仍在后台跑，禁止把 isStreaming 打成 false（否则输入解锁/假 idle）
        if (!soft || opts?.force) {
          const sid = currentSession?.id || '';
          const still = sid ? streamSessionApi().get(sid).agentRunning : false;
          if (!still) setIsStreaming(false);
        }
      },
      [addToast, t, currentSession?.id]
    );

    const handleStatusUpdate = useCallback((msg: StatusUpdateMessage) => {
      const sid = currentSession?.id || '';
      if (msg.state === 'thinking' || msg.state === 'tool_executing' || msg.state === 'optimizing') {
        setIsStreaming(true);
        if (msg.detail) setStreamStatusDetail(msg.detail);
        if (sid) {
          streamSessionApi().markRunning(sid, msg.detail || null);
        }
      } else if (msg.state === 'error') {
        setIsStreaming(false);
        const detail = msg.detail || t('chat.error');
        setStreamStatusDetail(detail);
        addToast(detail, 'error');
        if (sid) streamSessionApi().markIdle(sid);
      } else if (msg.state === 'idle') {
              setIsStreaming(false);
              setStreamStatusDetail(null);
              // 禁止在 setStreamingContent updater 内 addMessage（会触发 Sidebar 渲染期更新）
              const leftover = streamingContentRef.current;
              streamingContentRef.current = '';
              setStreamingContent('');
              setLiveToolCalls([]);
              if (sid) streamSessionApi().markIdle(sid);
              if (leftover || sid) {
                setTimeout(() => {
                  if (leftover) {
                    addMessage({
                      id: generateUUID(),
                      session_id: sid,
                      role: 'assistant',
                      content: leftover,
                      tool_calls: null,
                      token_count: null,
                      created_at: new Date().toISOString(),
                    });
                  }
                  if (sid) {
                    loadMessages(sid).catch(console.error);
                  }
                }, 0);
              }
            }
          }, [addMessage, addToast, currentSession, loadMessages, t]);

  const handleGoalUpdate = useCallback((msg: GoalUpdateMessage) => {
      if (msg.goal) {
        setActiveGoal(msg.goal);
        // Goal 进度改在任务看板，有更新时自动打开
        if (msg.goal.status === 'active' || (msg.goal.todos && msg.goal.todos.length > 0)) {
          setIsTaskPanelOpen(true);
        }
      }
    }, []);

  // Durable Run 生命周期事件 → 状态行（tool.* 已由 tool_event 覆盖，不重复显示）
  const handleRunEvent = useCallback((msg: RunEventMessage) => {
      const d = msg.data || {};
      if (msg.topic === 'run.status_changed') {
        const to = d.to || '';
        const keyMap: Record<string, Parameters<typeof t>[0]> = {
          planning: 'run.planning',
          executing: 'chat.executing',
          waiting: 'run.waiting',
          verifying: 'run.verifying',
        };
        const key = keyMap[to];
        if (key) setStreamStatusDetail(t(key));
      } else if (msg.topic === 'approval.requested') {
        setStreamStatusDetail(`${t('run.waiting')}: ${d.tool || ''}`.trim());
      } else if (msg.topic === 'approval.resolved') {
        setStreamStatusDetail(d.approved ? t('run.approved') : t('run.denied'));
      } else if (msg.topic === 'run.completed') {
        setStreamStatusDetail(t('run.done'));
      } else if (msg.topic === 'run.failed') {
        setStreamStatusDetail(t('run.runFailed'));
      } else if (msg.topic === 'run.cancelled') {
        setStreamStatusDetail(t('run.cancelled'));
      } else if (msg.topic === 'computer.exec') {
        // Agent Computer：按 agent_key 路由到对应终端 tab
        const key = (d.agent_key as string) || 'main';
        const label = (d.agent_label as string) || (key === 'main' ? 'Agent' : key);
        if (d.phase === 'start') {
          appendAgentOutputTo(key, label, `$ ${d.command || ''}`, 'in');
        } else {
          if (d.stdout_tail) appendAgentOutputTo(key, label, String(d.stdout_tail), 'out');
          if (d.stderr_tail) appendAgentOutputTo(key, label, String(d.stderr_tail), 'err');
          const tag = d.sandboxed ? ` · ${d.backend || 'sandbox'}` : '';
          appendAgentOutputTo(
            key, label,
            `exit ${d.exit_code ?? '?'}${tag} · ${Math.round(Number(d.duration_ms) || 0)}ms`,
            'sys'
          );
        }
      }
    }, [t, appendAgentOutputTo]);

  const handleSyncResponse = useCallback((payload: {
        messages: Array<{ id: string; role: string; content: string; created_at?: string | null }>;
        agent_running?: boolean;
        state?: string;
        partial_content?: string;
        stream_status?: string | null;
        stream_message_id?: string | null;
        live_tools?: Array<{
          id?: string;
          name?: string;
          arguments?: Record<string, unknown>;
          status?: string;
          result?: string | null;
        }>;
      }) => {
        const sid = currentSession?.id || '';
        if (payload.agent_running) {
          setIsStreaming(true);
          const partial = payload.partial_content ?? '';
          // 服务端快照优先；本地更长则保留本地（可能刚收到更多 delta）
          const local = streamingContentRef.current || '';
          const content = partial.length >= local.length ? partial : local || partial;
          if (content) {
            streamingContentRef.current = content;
            setStreamingContent(content);
          }
          if (payload.stream_status) {
            setStreamStatusDetail(payload.stream_status);
          } else {
            setStreamStatusDetail((d) => d || 'Resuming…');
          }
          if (payload.live_tools?.length) {
            const tools: ToolCallData[] = payload.live_tools.map((t) => ({
              id: String(t.id || ''),
              name: String(t.name || 'tool'),
              arguments: (t.arguments && typeof t.arguments === 'object' ? t.arguments : {}) as Record<string, unknown>,
              status: (t.status === 'failed' ? 'failed' : t.status === 'running' ? 'running' : 'completed') as ToolCallData['status'],
              result: t.result ?? undefined,
            }));
            setLiveToolCalls(tools);
          }
          if (sid) {
            streamSessionApi().save(sid, {
              isStreaming: true,
              agentRunning: true,
              content: content || streamingContentRef.current,
              tools: payload.live_tools?.length
                ? payload.live_tools.map((t) => ({
                    id: String(t.id || ''),
                    name: String(t.name || 'tool'),
                    arguments: (t.arguments as Record<string, unknown>) || {},
                    status: (t.status === 'failed' ? 'failed' : t.status === 'running' ? 'running' : 'completed') as ToolCallData['status'],
                    result: t.result ?? undefined,
                  }))
                : streamSessionApi().get(sid).tools,
              statusDetail: payload.stream_status || 'Resuming…',
              streamMessageId: payload.stream_message_id || null,
            });
          }
        } else {
          // 后台已结束：以 DB 为准刷新，避免停留在半截流式 UI
          setIsStreaming(false);
          setStreamStatusDetail(null);
          streamingContentRef.current = '';
          setStreamingContent('');
          setLiveToolCalls([]);
          if (sid) {
            streamSessionApi().markIdle(sid);
            loadMessages(sid).catch(console.error);
          }
        }
        // 合并漏掉的消息（按 id 去重）
        if (payload.messages?.length && sid) {
          const st = useSessionStore.getState();
          const have = new Set((st.messages || []).map((m) => m.id));
          for (const m of payload.messages) {
            if (!m?.id || have.has(m.id)) continue;
            addMessage({
              id: m.id,
              session_id: sid,
              role: (m.role as 'user' | 'assistant' | 'system') || 'assistant',
              content: m.content || '',
              tool_calls: null,
              token_count: null,
              created_at: m.created_at || new Date().toISOString(),
            });
          }
        }
      }, [addMessage, currentSession?.id, loadMessages]);

  const { isConnected, isConnecting, sendMessage, sendStop, waitForConnection, connect } = useWebSocket({
        sessionId: currentSession?.id || '',
        token,
        onStreamDelta: handleStreamDelta,
        onStatusUpdate: handleStatusUpdate,
        onToolEvent: handleToolEvent,
        onRunEvent: handleRunEvent,
        onGoalUpdate: handleGoalUpdate,
        onSyncResponse: handleSyncResponse,
        getLastMessageId: () => {
          const msgs = useSessionStore.getState().messages || [];
          for (let i = msgs.length - 1; i >= 0; i--) {
            const id = msgs[i]?.id;
            if (id && !String(id).startsWith('streaming') && id !== 'streaming') return id;
          }
          return undefined;
        },
        onError: (err) => toastWsError(err),
        onSettingsChanged: (keys) => {
          // 通知全局模型目录刷新（被设置页同步、多标签页切换等场景复用）
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('takton:settings-changed', { detail: keys }));
          }
        },
      });

  // 使用 useSession hook 中的 switchSession 用于全局搜索
  const { switchSession: switchSession_ } = useSession();

  // 发送消息（乐观 UI：先出用户气泡 + streaming，session/WS 后台并行）
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

        if (mode === 'cluster' && (!subAgentIds || subAgentIds.length === 0)) {
          addToast(t('chat.clusterNeedAgent'), 'error');
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

        let displayContent = content;
        if (attachments.length > 0) {
          const attNames = attachments.map((a) => `[${a.filename}]`).join(' ');
          displayContent = `${attNames}\n${content}`;
        }

        // 乐观：先落用户消息 + 思考态，再等 WS（避免「卡死感」）
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
        setIsStreaming(true);
        setStreamingContent('');
        setLiveToolCalls([]);
        setStreamStatusDetail(t('chat.connectingSend'));

        const ready = await waitForConnection(session.id, 15000);
        if (!ready) {
          addToast(t('chat.channelNotConnected'), 'error');
          setIsStreaming(false);
          setStreamStatusDetail(null);
          return;
        }

        setStreamStatusDetail(mode === 'cluster' ? t('chat.clusterWorking') : t('chat.thinking'));
        const sent = sendMessage(content, attachments, mode, subAgentIds);
        if (!sent) {
          addToast(t('chat.sendFailedDisconnected'), 'error');
          setIsStreaming(false);
          setStreamStatusDetail(null);
          return;
        }
      },
      [currentSession, addMessage, addToast, sendMessage, createAndLoadSession, waitForConnection, t]
    );

  // 重新生成
  const handleRegenerate = useCallback(
    async (_message: Message) => {
      if (!currentSession) return;
      const msgs = useSessionStore.getState().messages;
      const lastUserMsg = [...msgs].reverse().find((m) => m.role === 'user');
      if (!lastUserMsg?.content) return;
      setIsStreaming(true);
      setStreamingContent('');
      setLiveToolCalls([]);
      setStreamStatusDetail(t('chat.connectingSend'));
      const ready = await waitForConnection(currentSession.id, 15000);
      if (!ready) {
        addToast(t('chat.channelNotConnected2'), 'error');
        setIsStreaming(false);
        setStreamStatusDetail(null);
        return;
      }
      setStreamStatusDetail(t('chat.thinking'));
      if (sendMessage(lastUserMsg.content, [], 'default')) {
        setIsStreaming(true);
        setStreamingContent('');
      } else {
        addToast(t('chat.sendFailedDisconnected'), 'error');
        setIsStreaming(false);
        setStreamStatusDetail(null);
      }
    },
    [currentSession, sendMessage, waitForConnection, addToast, t]
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
      setStreamStatusDetail(null);
      setLiveToolCalls([]);
      const leftover = streamingContentRef.current || streamingContent;
      streamingContentRef.current = '';
      setStreamingContent('');
      const sid = currentSession?.id || '';
      if (sid) streamSessionApi().markIdle(sid);
      if (leftover) {
        // 下一 tick 写 store，避免与本组件 setState 同栈交叉更新 Sidebar
        setTimeout(() => {
          addMessage({
            id: generateUUID(),
            session_id: sid,
            role: 'assistant',
            content: leftover,
            tool_calls: null,
            token_count: null,
            created_at: new Date().toISOString(),
          });
          if (sid) loadMessages(sid).catch(console.error);
        }, 0);
      }
    }, [sendStop, streamingContent, addMessage, currentSession, loadMessages]);

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
      if (!files || files.length === 0) return;
      // 与点「附件」完全一致：只挂发送栏上方 chip，绝不 addMessage / sendMessage
      if (composerRef.current?.ingestFiles) {
        await composerRef.current.ingestFiles(files);
      } else {
        addToast(t('chat.uploadFailed') + 'composer unavailable', 'error');
      }
    },
    [addToast, t]
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
      className="chat-page-root relative flex h-full min-h-0 max-h-full w-full flex-1 flex-col overflow-hidden"
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
            <p className="mt-3 text-sm font-medium text-foreground-muted">{t('chat.dropToAttach')}</p>
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
                    {workspaceName || workspaceRoot || t('chat.selectProject')}
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
                                onClick={() => useTerminalStore.getState().togglePanel()}
                                className="relative rounded-lg border border-border-subtle bg-card-bg px-3.5 py-1.5 text-xs font-medium text-foreground-muted transition-all hover:border-border-default hover:bg-card-bg-hover"
                                title={t('terminal.title')}
                              >
                                {t('terminal.toggle')}
                                {termHasEntries && !termPanelOpen && (
                                  <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-brand-cyan" />
                                )}
                              </button>
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
                          onPreviewArtifact={setPreviewArtifact}
                        />
                      </div>
                      {!isConnected && !isConnecting && !!currentSession && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-card-bg/60 px-3 py-1.5 text-[11px] text-foreground-dim">
                          <span>{t('chat.channelIdle')}</span>
                        </div>
                      )}
                      <SessionArtifactsBar
                        messages={displayMessages}
                        onPreview={setPreviewArtifact}
                      />
                      <ActivityPanel
                        liveToolCalls={liveToolCalls}
                        streamStatusDetail={streamStatusDetail}
                        isStreaming={isStreaming}
                      />
                      <MessageInput
                                              ref={composerRef}
                                              key={editingContent ?? 'default'}
                                              onSend={handleSend}
                                              onGenerateImage={handleGenerateImage}
                                              disabled={isStreaming || isGeneratingImage || creatingSession}
                                              isStreaming={isStreaming}
                                              sessionId={currentSession?.id}
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

                    {previewArtifact && (
                      <FilePreviewHost
                        artifact={previewArtifact}
                        onClose={() => setPreviewArtifact(null)}
                      />
                    )}
                    <WorkspaceDock />

                    {/* 实时终端面板：desktop/shell 工具调用命令流 */}
                    <TerminalPanel />

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
