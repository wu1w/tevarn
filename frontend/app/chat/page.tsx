'use client';

import React, { Suspense, useState, useCallback, useRef, useEffect } from 'react';
import Link from 'next/link';
import { ChatWindow } from '@/components/chat/ChatWindow';
import { ProjectGroupView } from '@/components/chat/ProjectGroupView';
import { MessageInput, Attachment, ChatMode, type MessageInputHandle } from '@/components/chat/MessageInput';
import type { ChatArtifact } from '@/lib/artifacts';
import { formatArgsText, formatResultText } from '@/components/chat/TerminalPanel';
import { ComposerContextStrip } from '@/components/chat/ComposerContextStrip';
import { ChatInspector } from '@/components/chat/ChatInspector';
import { GlobalSearch } from '@/components/search/GlobalSearch';
import { useSession } from '@/hooks/useSession';
import { useChatWsBridge } from '@/stores/chatWsBridge';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useSessionStore } from '@/stores/sessionStore';
import { Message, StatusUpdateMessage, StreamDeltaMessage, GoalUpdateMessage, GoalState, ToolEventMessage, RunEventMessage } from '@/types';
import { CodingDeliveryCard, type CodingDelivery } from '@/components/chat/CodingDeliveryCard';
import { useTerminalStore } from '@/stores/terminalStore';
import { generateImage, type SessionRecoveryPayload } from '@/lib/api';
import { ChatRecoveryCard } from '@/components/chat/ChatRecoveryCard';
import { generateUUID } from '@/lib/uuid';
import { useRouter, useSearchParams } from 'next/navigation';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { OpenProjectModal } from '@/components/workspace/OpenProjectModal';
import { ContactSessionPicker } from '@/components/chat/ContactSessionPicker';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';
import { streamSessionApi } from '@/stores/streamSessionStore';
import { clearDeletedSessionLocalState } from '@/lib/sessionLocalCleanup';
import { openSessionTabChannel } from '@/lib/sessionTabChannel';
import { useChatInspectorStore, type ChatInspectorTab } from '@/stores/chatInspectorStore';
import { Eye, FolderOpen, ListTodo, ScanSearch, Terminal } from 'lucide-react';


export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="tk-route-enter-fill flex h-full min-h-0 flex-1 items-center justify-center text-sm text-foreground-dim">
          …
        </div>
      }
    >
      <ChatPageInner />
    </Suspense>
  );
}

function mapStreamStatusDetail(
  detail: string | null | undefined,
  t: (key: string) => string,
): string | null {
  const raw = (detail || '').trim();
  if (!raw || raw === 'Ready') return null;
  const queued = raw.match(/^已排队/) || raw.match(/^Queued \(/i);
  if (queued) return raw.startsWith('Queued') ? t('chat.queued') : raw;
  const table: Record<string, string> = {
    '已停止': t('chat.stopped'),
    'Generation stopped by user': t('chat.stopped'),
    'Generation stopped': t('chat.stopped'),
    '正在恢复…': t('chat.resuming'),
    'Resuming…': t('chat.resuming'),
    '开始处理排队消息…': t('chat.startingQueued'),
    'Starting queued message…': t('chat.startingQueued'),
    '正在结束上一轮…': t('chat.stoppingPrevious'),
    'Stopping previous run to start new input...': t('chat.stoppingPrevious'),
    '思考中…': t('chat.thinking'),
    '思考中': t('chat.thinking'),
    '继续推进…': t('chat.thinking'),
    '正在给出答复…': t('chat.thinking'),
    '正在收束并给出答复…': t('chat.thinking'),
    '已收到补充说明…': t('chat.thinking'),
    '正在整理上下文…': t('chat.thinking'),
    '网络不稳定，正在重试…': t('chat.thinking'),
  };
  if (table[raw]) return table[raw];
  if (
    /^(场景 |model=|上下文已压缩|Applying user steer|Token 预算|自动续跑|后台任务|Goal |模型空回复|空回复|LLM |补充取证|模型返回空工具|进程已挂起|进程已恢复|layers=)/i.test(
      raw,
    ) ||
    /top_up|force_final|kernel|dropped=/i.test(raw)
  ) {
    return t('chat.thinking');
  }
  return raw;
}

function snapshotStoppedTools(tools: ToolCallData[]): ToolCallData[] {
  return tools.map((t) => {
    if (t.status === 'failed' || t.status === 'completed') return t;
    if (t.status === 'cancelled') {
      return {
        ...t,
        result: t.result || '[Cancelled] stopped by user',
      };
    }
    return {
      ...t,
      status: 'cancelled' as const,
      result: t.result || '[Cancelled] stopped by user',
    };
  });
}

function keepPartialAssistantOnIdle(
  sid: string,
  leftover: string,
  loadMessages: (id: string) => Promise<unknown>,
  addMessage: (m: Message) => void,
  leftoverTools: ToolCallData[] = [],
) {
  const finish = () => {
    const tools = leftoverTools.length
      ? leftoverTools.map((tc) => ({
          id: tc.id,
          name: tc.name,
          arguments: tc.arguments,
          result: tc.result,
          status: tc.status,
        }))
      : [];
    if (!leftover.trim() && tools.length === 0) return;
    const msgs = useSessionStore.getState().messages || [];
    const lastA = [...msgs].reverse().find(
      (m) =>
        m.role === 'assistant' &&
        !String(m.id || '').startsWith('streaming') &&
        !String(m.id || '').startsWith('optimistic:'),
    );
    const head = leftover.trim().slice(0, 80);
    const haveText = leftover.trim()
      ? lastA && String(lastA.content || '').includes(head)
      : Boolean(lastA);
    if (!haveText) {
      addMessage({
        id: generateUUID(),
        session_id: sid,
        role: 'assistant',
        content: leftover,
        tool_calls: tools.length ? (tools as Message['tool_calls']) : null,
        token_count: null,
        created_at: new Date().toISOString(),
      });
      return;
    }
    if (tools.length && lastA && !lastA.tool_calls?.length) {
      useSessionStore.getState().updateMessage(lastA.id, {
        tool_calls: tools as Message['tool_calls'],
      });
    }
  };
  if (sid) {
    void loadMessages(sid).then(finish).catch((e) => {
      console.error(e);
      finish();
    });
  } else {
    finish();
  }
}

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const contactIdentity = (searchParams.get('identity') || '').trim();
  const projectGroupId = (searchParams.get('group') || '').trim();
  const { currentSession, messages, addMessage, createAndLoadSession, openContactSession, loadMessages, switchSession, error: sessionLoadError } = useSession();
  const reconcileMessage = useSessionStore((s) => s.reconcileMessage);
    // token 由 AppShell GlobalChatWs 使用；chat 页不再直接连 WS
    const {
      uiMode,
      setUiMode,
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

        const [highlightMessageId, setHighlightMessageId] = useState<string | null>(null);
        const [isStreaming, setIsStreaming] = useState(false);
  const [codingDelivery, setCodingDelivery] = useState<CodingDelivery | null>(null);
  const [injectedSkills, setInjectedSkills] = useState<string[]>([]);
  const [planPhase, setPlanPhase] = useState<string | null>(null);

  // 切会话时清交付卡，避免串台（推迟 setState，减少 effect 级联）
  useEffect(() => {
    const t = window.setTimeout(() => setCodingDelivery(null), 0);
    return () => window.clearTimeout(t);
  }, [currentSession?.id]);
        /** Stop 后等待服务端 idle，避免假停后仍收 stream_delta（按会话，防切会话误伤） */
        const [isStopping, setIsStopping] = useState(false);
        const stoppingBySessionRef = useRef<Record<string, boolean>>({});
        const isStoppingSid = useCallback((sid?: string | null) => {
          if (!sid) return false;
          return Boolean(stoppingBySessionRef.current[sid]);
        }, []);
        const setStoppingSid = useCallback((sid: string | null | undefined, v: boolean) => {
          if (!sid) return;
          if (v) stoppingBySessionRef.current[sid] = true;
          else delete stoppingBySessionRef.current[sid];
          const cur = useSessionStore.getState().currentSession?.id;
          if (cur === sid) setIsStopping(v);
        }, []);
        /** 本地已强制收束：忽略 late delta，避免「停了又活」 */
        const locallyStoppedRef = useRef<Set<string>>(new Set());
        const streamFlushTimerRef = useRef<number | null>(null);
        React.useEffect(() => {
          return () => {
            if (streamFlushTimerRef.current != null) {
              window.clearTimeout(streamFlushTimerRef.current);
            }
          };
        }, []);
        /** 流式活动时间戳：长时间无 delta 则视为卡住，露出恢复入口 */
        const lastStreamActivityRef = useRef<number>(0);
        const seenRunEventIdsRef = useRef<Set<string>>(new Set());
        const currentRunGenerationRef = useRef<number>(0);
        /** 假 Resuming：等 sync 完成后再 arm 短超时（弱网 auth+sync 常 >4s） */
        const syncSeenForResumeRef = useRef<Record<string, boolean>>({});
        const pendingResumeFallbackRef = useRef<Record<string, () => void>>({});
        /** 同会话其它 Tab 正在跑流式（BroadcastChannel） */
        const [peerOccupied, setPeerOccupied] = useState(false);
        const tabChannelRef = useRef<ReturnType<typeof openSessionTabChannel> | null>(null);
        const isStreamingRef = useRef(false);
        const isStoppingRef = useRef(false);
        const [streamStuck, setStreamStuck] = useState(false);
        const [streamingContent, setStreamingContent] = useState('');
        // 流式正文 ref：idle/stop 时落地，避免在 setState updater 内同步写 sessionStore
        const streamingContentRef = React.useRef('');
        React.useEffect(() => {
          streamingContentRef.current = streamingContent;
        }, [streamingContent]);
        const [liveToolCalls, setLiveToolCalls] = useState<ToolCallData[]>([]);
        const liveToolCallsRef = React.useRef<ToolCallData[]>([]);
        React.useEffect(() => {
          liveToolCallsRef.current = liveToolCalls;
        }, [liveToolCalls]);
                const [streamStatusDetail, setStreamStatusDetail] = useState<string | null>(null);
        const termHasEntries = useTerminalStore((s) => s.entries.length > 0);

            const [isGeneratingImage, setIsGeneratingImage] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const [activeGoal, setActiveGoal] = useState<GoalState | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const composerRef = useRef<MessageInputHandle | null>(null);
    const [previewArtifact, setPreviewArtifact] = useState<ChatArtifact | null>(null);
    const inspectorTab = useChatInspectorStore((s) => s.tab);
    const toggleInspectorTab = useCallback((id: ChatInspectorTab) => {
      useChatInspectorStore.getState().toggleTab(id);
      const next = useChatInspectorStore.getState().tab;
      if (id === 'files' || next === null) {
        useWorkspaceStore.getState().setDockOpen(next === 'files');
      }
      if (id === 'terminal' || next === null) {
        useTerminalStore.getState().setPanelOpen(next === 'terminal');
      }
    }, []);
    const openPreview = useCallback((artifact: ChatArtifact) => {
      setPreviewArtifact(artifact);
      useChatInspectorStore.getState().setTab('preview');
    }, []);
    const [recovery, setRecovery] = useState<SessionRecoveryPayload | null>(null);
    const [runCaps, setRunCaps] = useState<{
      caps?: number;
      tools?: number;
      soft?: number;
    } | null>(null);
    // 按会话缓存上轮 caps，切回已结束会话仍可回顾
    const runCapsCacheRef = useRef<
      Record<string, { caps?: number; tools?: number; soft?: number }>
    >({});
    /** 本轮实际调用模型（WS status.model） */
    const [liveModel, setLiveModel] = useState<string | null>(null);
    const liveModelCacheRef = useRef<Record<string, string>>({});

    // 开发冒烟：允许 Playwright 注入消息 / 打开预览
    React.useEffect(() => {
      if (process.env.NODE_ENV === 'production') return;
      const w = window as unknown as {
        __tevarnSmoke?: {
          setPreview: (a: ChatArtifact | null) => void;
          addMessage: typeof addMessage;
          setMessages: (msgs: Message[]) => void;
        };
      };
      w.__tevarnSmoke = {
        setPreview: (a) => {
          setPreviewArtifact(a);
          if (a) useChatInspectorStore.getState().setTab('preview');
          else if (useChatInspectorStore.getState().tab === 'preview') {
            useChatInspectorStore.getState().setTab(null);
          }
        },
        addMessage,
        setMessages: useSessionStore.getState().setMessages,
      };
      return () => {
        delete w.__tevarnSmoke;
      };
    }, [addMessage]);

    const [editingContent, setEditingContent] = useState<string | null>(null);
  // 设备页「用此设备对话」带入的草稿
  React.useEffect(() => {
    try {
      const d = sessionStorage.getItem('tevarn-compose-draft');
      if (d) {
        sessionStorage.removeItem('tevarn-compose-draft');
        const id = window.setTimeout(() => setEditingContent(d), 0);
        return () => window.clearTimeout(id);
      }
    } catch { /* ignore */ }
  }, []);

  // 企业 IM：/chat?identity=名称 → 一人一会话（find-or-create，不堆 session）
  React.useEffect(() => {
    if (!contactIdentity) return;
    let cancelled = false;
    (async () => {
      try {
        await openContactSession(contactIdentity);
      } catch (e) {
        console.error(e);
      } finally {
        if (!cancelled) {
          router.replace('/chat');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contactIdentity]);

    const [creatingSession, setCreatingSession] = useState(false);
    const { addToast } = useToastStore();
    const t = useT();
    const sessionIdentity =
      (currentSession?.config as { contact_agent?: string } | undefined)?.contact_agent || '';


  // session 切换：保存/恢复 per-session 流式态；运行中任务不因切页而停（后端不 cancel agent）
  const prevSessionIdRef = React.useRef<string | null | undefined>(undefined);
    React.useEffect(() => {
      let cancelled = false;
      const sid = currentSession?.id;
      const prev = prevSessionIdRef.current;
      const sessionChanged = prev !== undefined && prev !== sid;

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
          // 停止态按会话恢复，不继承 A 的 isStopping 到 B
          setIsStopping(isStoppingSid(sid));
          // 恢复该会话上轮能力芯片（已结束会话也能回顾）
          setRunCaps(runCapsCacheRef.current[sid] || null);
          setLiveModel(liveModelCacheRef.current[sid] || null);
          const cached = streamSessionApi().get(sid);
          // 标记：等 sync 完成后再启假 Resuming 超时（弱网 auth+sync 常 >4s）
          syncSeenForResumeRef.current[sid] = false;
          if (cached.agentRunning || cached.isStreaming || cached.content || cached.tools.length) {
            setIsStreaming(true);
            setStreamingContent(cached.content || '');
            streamingContentRef.current = cached.content || '';
            setLiveToolCalls(cached.tools || []);
            setStreamStatusDetail(cached.statusDetail || t('chat.resuming'));
            lastStreamActivityRef.current = Date.now();
            const resumeSid = sid;
            // 假 Resuming：sync 到达后 6s、或最多等 12s 仍无活动则收束
            const armIdleFallback = (delayMs: number) => {
              window.setTimeout(() => {
                if (cancelled) return;
                if (useSessionStore.getState().currentSession?.id !== resumeSid) return;
                const st = streamSessionApi().get(resumeSid);
                if (Date.now() - lastStreamActivityRef.current < delayMs - 500) return;
                if (!st.agentRunning && !st.isStreaming) return;
                streamSessionApi().markIdle(resumeSid);
                setIsStreaming(false);
                setStreamStatusDetail(null);
                setLiveToolCalls([]);
                streamingContentRef.current = '';
                setStreamingContent('');
                loadMessages(resumeSid).catch(console.error);
              }, delayMs);
            };
            // 兜底上限 12s（sync 一直不来）
            armIdleFallback(12_000);
            // sync 后由 handleSyncResponse 再 arm 6s
            pendingResumeFallbackRef.current[resumeSid] = () => armIdleFallback(6_000);
          } else {
            setIsStreaming(false);
            setStreamingContent('');
            streamingContentRef.current = '';
            setLiveToolCalls([]);
            setStreamStatusDetail(null);
          }
        }

        // 先确认会话仍在库里（localStorage 可能残留已删 id）
        try {
          const { getSession } = await import('@/lib/api');
          await getSession(sid);
        } catch (e) {
          const status = (e as { response?: { status?: number } })?.response?.status;
          if (status === 404) {
            useSessionStore.getState().setCurrentSession(null);
            useSessionStore.getState().clearMessages();
            try {
              window.dispatchEvent(
                new CustomEvent('tevarn:session-invalid', { detail: { sessionId: sid } })
              );
            } catch {
              /* ignore */
            }
            return;
          }
        }
        if (cancelled) return;
        try {
          await loadMessages(sid);
        } catch (e) {
          const status = (e as { response?: { status?: number } })?.response?.status;
          if (status === 404) {
            try {
              window.dispatchEvent(
                new CustomEvent('tevarn:session-invalid', { detail: { sessionId: sid } })
              );
            } catch {
              /* ignore */
            }
          } else if (status !== 404) console.error(e);
        }
        if (cancelled) return;
        try {
          const { getSessionCheckpoint } = await import('@/lib/api');
          const cp = await getSessionCheckpoint(sid);
          if (cancelled) return;
          if (cp?.goal) {
            setActiveGoal(cp.goal);
          }
          // R-02：仅在可恢复时展示卡片
          if (cp?.recovery?.show) {
            setRecovery(cp.recovery);
          } else if (cp?.can_resume) {
            setRecovery({
              show: true,
              can_resume: true,
              exit: {
                code: 'checkpoint_resume',
                title: '可从断点续跑',
                message: '检测到未完成的 Goal / checkpoint。',
                severity: 'info',
              },
            });
          } else {
            setRecovery(null);
          }
        } catch (e) {
          const status = (e as { response?: { status?: number } })?.response?.status;
          if (status && status !== 404) console.warn('restore goal failed', e);
          setRecovery(null);
        }
      })();
      return () => {
        cancelled = true;
      };
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

  // 长时间 thinking 无 delta → 卡住，露出恢复/停止入口
  React.useEffect(() => {
    if (!isStreaming || isStopping) {
      const id = window.setTimeout(() => setStreamStuck(false), 0);
      return () => window.clearTimeout(id);
    }
    const STUCK_MS = 90_000;
    const tick = window.setInterval(() => {
      if (Date.now() - lastStreamActivityRef.current >= STUCK_MS) {
        setStreamStuck(true);
        setStreamStatusDetail((d) => d || t('chat.streamStuck') || '长时间无响应，可停止或恢复');
      }
    }, 10_000);
    return () => clearInterval(tick);
  }, [isStreaming, isStopping, t]);

  // 404 会话跨标签同步 + 发现 WS 基址
  React.useEffect(() => {
    const onInvalid = (e: Event) => {
      const id = (e as CustomEvent).detail?.sessionId as string | undefined;
      if (id) {
        clearDeletedSessionLocalState(id);
        setStoppingSid(id, false);
      }
      const cur = useSessionStore.getState().currentSession?.id;
      if (id && cur === id) {
        useSessionStore.getState().setCurrentSession(null);
        useSessionStore.getState().clearMessages();
        setIsStreaming(false);
        setStreamStatusDetail(null);
        setLiveToolCalls([]);
        streamingContentRef.current = '';
        setStreamingContent('');
      }
    };
    window.addEventListener('tevarn:session-invalid', onInvalid);
    // 启动时拉一次 endpoints，覆盖魔法 8090
    void import('@/lib/api')
      .then(({ getRuntimeEndpoints }) => getRuntimeEndpoints())
      .then((ep) => {
        if (ep?.ws_base) {
          import('@/hooks/useWebSocket').then((m) => {
            m.setDiscoveredWsBase?.(ep.ws_base);
          });
        }
      })
      .catch(() => undefined);
    return () => window.removeEventListener('tevarn:session-invalid', onInvalid);
  }, [setStoppingSid]);

  const handleStreamDelta = useCallback((msg: StreamDeltaMessage) => {
      const sid = currentSession?.id || '';
      if (isStoppingSid(sid)) return;
      if (sid && locallyStoppedRef.current.has(sid)) return;
      lastStreamActivityRef.current = Date.now();
      setStreamStuck(false);
      const mid = msg.message_id || '';
      const store = streamSessionApi();
      const prevMid = sid ? store.get(sid).streamMessageId : null;
      let next: string;
      const prev = streamingContentRef.current;
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
      if (streamFlushTimerRef.current != null) return;
      streamFlushTimerRef.current = window.setTimeout(() => {
        streamFlushTimerRef.current = null;
        setIsStreaming(true);
        setStreamingContent(streamingContentRef.current);
      }, 50);
    }, [currentSession?.id, isStoppingSid]);

    /** 伪 tool 回收：用后端清洗后的 content 替换已流式气泡 */
    const handleContentReset = useCallback((msg: { content?: string; reason?: string; message_id?: string }) => {
      const sid = currentSession?.id || '';
      if (isStoppingSid(sid)) return;
      const store = streamSessionApi();
      const curMid = sid ? store.get(sid).streamMessageId : null;
      const mid = (msg.message_id || '').trim();
      // 多气泡：仅当 message_id 匹配当前流或未指定 id 时重置
      if (mid && curMid && mid !== curMid) {
        return;
      }
      const cleaned = typeof msg.content === 'string' ? msg.content : '';
      streamingContentRef.current = cleaned;
      setStreamingContent(cleaned);
      if (sid) {
        store.patch(sid, {
          content: cleaned,
          isStreaming: true,
          agentRunning: true,
          streamMessageId: mid || curMid,
        });
      }
    }, [currentSession?.id, isStoppingSid]);


    const handleToolEvent = useCallback((msg: ToolEventMessage) => {
      const sid = currentSession?.id || '';
      if (isStoppingSid(sid)) return;
      setIsStreaming(true);
      lastStreamActivityRef.current = Date.now();
      setStreamStuck(false);
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
            duration_ms: msg.duration_ms ?? undefined,
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
          const raw = String(msg.result);
          const clipped = raw.length > 12000;
          appendAgentOutput(
            clipped ? `${raw.slice(0, 12000)}\n…[output truncated]` : raw,
            msg.status === 'failed' ? 'err' : 'out'
          );
        }
      }
    }, [appendAgentOutput, t, currentSession?.id, isStoppingSid]);

    const lastWsToastAtRef = React.useRef(0);
    const toastWsError = useCallback(
      (err: string, opts?: { force?: boolean }) => {
        const msg = (err || '').trim() || t('chat.wsError');
        console.error('WebSocket error:', msg);
        const now = Date.now();
        const soft = /connection error|not connected|reconnect|slow retry/i.test(msg)
          && !/Invalid|creation failed|Unknown/i.test(msg);
        const strategyNote = /slow retry/i.test(msg);
        if (soft && !opts?.force && !strategyNote) {
          setStreamStatusDetail(t('chat.reconnecting') || '连接中断，正在重连…');
          if (now - lastWsToastAtRef.current < 12_000) return;
        } else if (!opts?.force && now - lastWsToastAtRef.current < 4000) {
          return;
        }
        lastWsToastAtRef.current = now;
        addToast(msg, soft ? 'info' : 'error');
        // 软断线：Agent 可能仍在后台跑，禁止假 idle
        if (!soft || opts?.force) {
          const sid = currentSession?.id || '';
          const still = sid ? streamSessionApi().get(sid).agentRunning : false;
          if (!still) setIsStreaming(false);
        }
      },
      [addToast, t, currentSession?.id]
    );

    // Host wipe：清空 process 相关 UI / 恢复卡，避免 resume 死 id
    React.useEffect(() => {
      const onEpoch = (e: Event) => {
        const epoch = (e as CustomEvent).detail?.host_epoch;
        setRunCaps(null);
        setRecovery(null);
        setStreamStatusDetail(t('chat.hostWiped') || 'Host 已重置，请重新发送');
        // 清当前会话 stream 缓存里的假 running（进程表已 wipe）
        const sid = useSessionStore.getState().currentSession?.id;
        if (sid) {
          streamSessionApi().markIdle(sid);
        }
        if (epoch != null) {
          try {
            sessionStorage.setItem('tevarn:last_host_epoch', String(epoch));
          } catch {
            /* ignore */
          }
        }
      };
      window.addEventListener('tevarn:host-epoch', onEpoch);
      return () => window.removeEventListener('tevarn:host-epoch', onEpoch);
    }, [t]);

    const handleStatusUpdate = useCallback((msg: StatusUpdateMessage) => {
      const sid = currentSession?.id || '';
      if (msg.state === 'thinking' || msg.state === 'tool_executing' || msg.state === 'optimizing') {
        // 用户已点停止：忽略迟到的 running 态，避免假停被冲掉
        if (isStoppingSid(sid)) {
          setStreamStatusDetail(
            mapStreamStatusDetail(msg.detail, t) || t('chat.stopping'),
          );
          return;
        }
        setIsStreaming(true);
        lastStreamActivityRef.current = Date.now();
        if (msg.detail) {
          setStreamStatusDetail(mapStreamStatusDetail(msg.detail, t));
        }
        // 本轮实际模型（优先结构化字段）
        const modelFromMsg =
          (typeof msg.model === 'string' && msg.model.trim()) ||
          (() => {
            const m = String(msg.detail || '').match(
              /(?:model|模型)\s*[=:：]?\s*([^\s,·|]+)/i,
            );
            return m ? m[1] : null;
          })();
        if (modelFromMsg) {
          setLiveModel(modelFromMsg);
          if (sid) liveModelCacheRef.current[sid] = modelFromMsg;
        }
        // 优先结构化 caps/tools；文案正则仅作回落
        const capsN =
          typeof msg.caps_count === 'number'
            ? msg.caps_count
            : (() => {
                const m = String(msg.detail || '').match(
                  /(?:能力|caps)\s*(\d+)/i,
                );
                return m ? Number(m[1]) : null;
              })();
        const toolsN =
          typeof msg.tools_count === 'number'
            ? msg.tools_count
            : (() => {
                const m = String(msg.detail || '').match(
                  /(?:工具|tools)\s*(\d+)/i,
                );
                return m ? Number(m[1]) : null;
              })();
        if (capsN != null || toolsN != null) {
          setRunCaps((prev) => {
            const next = {
              caps: capsN ?? prev?.caps ?? 0,
              tools: toolsN ?? prev?.tools ?? 0,
              soft: prev?.soft,
            };
            if (sid) runCapsCacheRef.current[sid] = next;
            return next;
          });
        }
        if (sid) streamSessionApi().markRunning(sid, msg.detail || null);
      } else if (msg.state === 'error') {
        setStoppingSid(sid, false);
        setIsStreaming(false);
        const detail = msg.detail || t('chat.error');
        setStreamStatusDetail(detail);
        addToast(detail, 'error');
        if (sid) streamSessionApi().markIdle(sid);
        // 服务端入队后错误：回滚最近未 ack 的乐观用户气泡（防幽灵消息）
        if (sid) {
          const st = useSessionStore.getState();
          const opts = (st.messages || []).filter(
            (m) =>
              String(m.id || '').startsWith('optimistic:') &&
              m.role === 'user' &&
              (!m.session_id || m.session_id === sid),
          );
          // 优先删最近一条；若 detail 含「稍后再发」类则全清本会话乐观用户
          const bulk =
            /仍在结束|稍后再发|busy|上一轮/i.test(detail) || opts.length > 1;
          if (bulk) {
            for (const o of opts) st.removeMessage(o.id);
          } else if (opts.length) {
            st.removeMessage(opts[opts.length - 1].id);
          }
        }
      } else if (msg.state === 'idle') {
              if (msg.agent_running) {
                // Duplicate-ack / stale idle while the agent is still running.
                // Must not unlock the composer or freeze a ghost assistant bubble.
                if (msg.detail) setStreamStatusDetail(msg.detail);
                lastStreamActivityRef.current = Date.now();
                return;
              }
              setStoppingSid(sid, false);
              setStreamStuck(false);
              setIsStreaming(false);
              setStreamStatusDetail(null);
              // keep last runCaps for this session (switch-back + post-run)
              if (sid && runCapsCacheRef.current[sid] == null) {
                /* already cached on status updates */
              }
              const leftover = streamingContentRef.current;
              const leftoverTools = snapshotStoppedTools(liveToolCallsRef.current);
              streamingContentRef.current = '';
              setStreamingContent('');
              setLiveToolCalls(leftoverTools);
              if (sid) streamSessionApi().markIdle(sid);
              // Stop keeps a local partial if history has not landed yet (ChatGPT/Cursor).
              if (leftover || leftoverTools.length || sid) {
                setTimeout(() => {
                  keepPartialAssistantOnIdle(
                    sid,
                    leftover || '',
                    loadMessages,
                    addMessage,
                    leftoverTools,
                  );
                  setLiveToolCalls([]);
                }, 0);
              }
            }
          }, [addMessage, addToast, currentSession, loadMessages, t, isStoppingSid, setStoppingSid]);

  const handleGoalUpdate = useCallback((msg: GoalUpdateMessage) => {
      if (msg.goal) {
        setActiveGoal(msg.goal);
      }
    }, []);

  // Durable Run 生命周期事件 → 状态行（tool.* 已由 tool_event 覆盖，不重复显示）
  const handleRunEvent = useCallback((msg: RunEventMessage) => {
      // Unified: event === topic (backend fills both)
      const ev = msg.event || msg.topic || '';
      const d = (msg.data || msg.payload || {}) as Record<string, unknown>;
      // Dedup replay (event_id preferred, else session:seq:event)
      const eid =
        (msg as { event_id?: string }).event_id ||
        (typeof msg.seq === 'number'
          ? `${msg.session_id || ''}:${msg.seq}:${ev}`
          : '');
      if (eid) {
        if (seenRunEventIdsRef.current.has(eid)) return;
        seenRunEventIdsRef.current.add(eid);
        if (seenRunEventIdsRef.current.size > 800) {
          seenRunEventIdsRef.current = new Set(
            [...seenRunEventIdsRef.current].slice(-400),
          );
        }
      }
      // Drop late events from prior runs
      const msgGen = Number(
        (msg as { run_generation?: number; generation?: number }).run_generation ??
          (msg as { generation?: number }).generation ??
          0,
      );
      if (msgGen > 0) {
        if (msgGen > currentRunGenerationRef.current) {
          currentRunGenerationRef.current = msgGen;
        } else if (msgGen < currentRunGenerationRef.current) {
          return;
        }
      }

      // coding delivery card (ignore cross-session if session_id present)
      if (ev === 'coding.delivery' && (msg.payload || msg.data)) {
        const sid = (msg as { session_id?: string }).session_id;
        const cur = useSessionStore.getState().currentSession?.id;
        if (sid && cur && sid !== cur) return;
        setCodingDelivery((msg.payload || msg.data) as CodingDelivery);
        return;
      }
      if (ev === 'run.started') {
        setCodingDelivery(null);
      setInjectedSkills([]);
      setPlanPhase(null);
      }
      if (ev === 'skills.injected') {
        const skills = (d as { skills?: string[] }).skills || [];
        if (Array.isArray(skills) && skills.length) {
          setInjectedSkills(skills.map(String));
          setStreamStatusDetail(
            `Skills · ${skills.slice(0, 4).join(', ')}${skills.length > 4 ? '…' : ''}`,
          );
        }
      }
      if (ev === 'plan.phase' || ev === 'run.planning') {
        const ph = String((d as { phase?: string }).phase || msg.detail || 'planning');
        setPlanPhase(ph);
        setStreamStatusDetail(`Plan · ${ph}`);
      }
      if (ev === 'coding.phase') {
        const phase = String((d as { phase?: string }).phase || msg.detail || '');
        if (phase) setStreamStatusDetail(`${t('chat.executing') || '执行中'} · ${phase}`);
      }
      // terminal events — clear streaming status
      if (ev === 'run.completed' || ev === 'run.cancelled' || ev === 'run.failed') {
        if (ev === 'run.completed') setStreamStatusDetail(t('run.done'));
        else if (ev === 'run.failed') setStreamStatusDetail(t('run.runFailed'));
        else setStreamStatusDetail(t('run.cancelled'));
        // do not return — allow topic-style fallthrough no-ops
      }
      if (msg.topic === 'run.status_changed' || ev === 'run.status_changed') {
        const to = String((d as { to?: unknown }).to ?? '');
        const keyMap: Record<string, Parameters<typeof t>[0]> = {
          planning: 'run.planning',
          executing: 'chat.executing',
          waiting: 'run.waiting',
          verifying: 'run.verifying',
        };
        const key = keyMap[to];
        if (key) setStreamStatusDetail(t(key));
      } else if (msg.topic === 'approval.requested' || ev === 'approval.requested') {
        setStreamStatusDetail(`${t('run.waiting')}: ${String((d as { tool?: unknown }).tool ?? '')}`.trim());
      } else if (msg.topic === 'approval.resolved' || ev === 'approval.resolved') {
        setStreamStatusDetail((d as { approved?: unknown }).approved ? t('run.approved') : t('run.denied'));
      } else if (msg.topic === 'run.completed' || ev === 'run.completed') {
        setStreamStatusDetail(t('run.done'));
      } else if (msg.topic === 'run.failed' || ev === 'run.failed') {
        setStreamStatusDetail(t('run.runFailed'));
      } else if (msg.topic === 'run.cancelled' || ev === 'run.cancelled') {
        setStreamStatusDetail(t('run.cancelled'));
      } else if (msg.topic === 'computer.exec' || ev === 'computer.exec') {
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
        // sync 为权威：标记已见，并触发「sync 后再 arm 6s 假 Resuming 超时」
        if (sid) {
          syncSeenForResumeRef.current[sid] = true;
          lastStreamActivityRef.current = Date.now();
          const arm = pendingResumeFallbackRef.current[sid];
          if (arm) {
            delete pendingResumeFallbackRef.current[sid];
            // agent 仍在跑才 arm 短超时；已 idle 则无需
            if (payload.agent_running) arm();
          }
        }
        if (payload.agent_running) {
          setIsStreaming(true);
          const partial = payload.partial_content ?? '';
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
              statusDetail: payload.stream_status || t('chat.resuming'),
              streamMessageId: payload.stream_message_id || null,
            });
          }
        } else {
          if (sid) setStoppingSid(sid, false);
          setIsStreaming(false);
          setStreamStatusDetail(null);
          const leftover = streamingContentRef.current || payload.partial_content || '';
          const leftoverTools = snapshotStoppedTools(
            liveToolCallsRef.current.length
              ? liveToolCallsRef.current
              : (payload.live_tools || []).map((t) => ({
                  id: String(t.id || ''),
                  name: String(t.name || 'tool'),
                  arguments: (t.arguments && typeof t.arguments === 'object'
                    ? t.arguments
                    : {}) as Record<string, unknown>,
                  status: (t.status === 'failed'
                    ? 'failed'
                    : t.status === 'running'
                      ? 'running'
                      : t.status === 'cancelled'
                        ? 'cancelled'
                        : 'completed') as ToolCallData['status'],
                  result: t.result ?? undefined,
                })),
          );
          streamingContentRef.current = '';
          setStreamingContent('');
          setLiveToolCalls(leftoverTools);
          if (sid) {
            streamSessionApi().markIdle(sid);
            keepPartialAssistantOnIdle(
              sid,
              leftover,
              loadMessages,
              addMessage,
              leftoverTools,
            );
          }
          window.setTimeout(() => setLiveToolCalls([]), 0);
        }
        if (payload.messages?.length && sid) {
          for (const m of payload.messages) {
            if (!m?.id) continue;
            reconcileMessage({
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
      }, [reconcileMessage, currentSession?.id, loadMessages, addMessage, setStoppingSid]);

const handleUserMessageAck = useCallback(
        (payload: {
          id: string;
          role: string;
          content: string;
          created_at?: string | null;
          display_content?: string | null;
        }) => {
          const sid = currentSession?.id || '';
          if (!sid || !payload.id) return;
          const enriched = payload.content || '';
          const disp = (payload.display_content || '').trim();
          // 一次 reconcile：正文用落库内容，匹配池同时含原文（乐观气泡）+ enrich
          // 绝不可先 append enrich 再二次 reconcile（haveById 会跳过，乐观残留=双气泡）
          reconcileMessage(
            {
              id: payload.id,
              session_id: sid,
              role: (payload.role as 'user' | 'assistant' | 'system') || 'user',
              content: enriched,
              tool_calls: null,
              token_count: null,
              created_at: payload.created_at || new Date().toISOString(),
            },
            { matchContents: [enriched, disp].filter(Boolean) },
          );
        },
        [currentSession?.id, reconcileMessage]
      );

  const handleUserInputIgnored = useCallback(
    (payload: { reason?: string; detail?: string; agent_running?: boolean }) => {
      const sid = currentSession?.id || '';
      const detail =
        payload.detail || t('chat.duplicateIgnored') || '忽略重复发送（短时相同内容）';
      setStreamStatusDetail(detail);
      lastStreamActivityRef.current = Date.now();
      if (sid) {
        const st = useSessionStore.getState();
        const opts = (st.messages || []).filter(
          (m) =>
            String(m.id || '').startsWith('optimistic:') &&
            m.role === 'user' &&
            (!m.session_id || m.session_id === sid),
        );
        if (opts.length) st.removeMessage(opts[opts.length - 1].id);
      }
    },
    [currentSession?.id, t],
  );

  // AppShell GlobalChatWs 常驻连接；本页注册 handlers，并在回页后主动 sync 补漏 delta
  React.useEffect(() => {
    const getLast = () => {
      const msgs = useSessionStore.getState().messages || [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        const id = msgs[i]?.id;
        if (!id) continue;
        const s = String(id);
        if (
          s.startsWith('streaming') ||
          s === 'streaming' ||
          s.startsWith('optimistic:') ||
          s.startsWith('local:')
        ) {
          continue;
        }
        if (s.length < 32) continue;
        return s;
      }
      return undefined;
    };
    useChatWsBridge.getState().setHandlers({
      onStreamDelta: handleStreamDelta,
      onContentReset: handleContentReset,
      onStatusUpdate: handleStatusUpdate,
      onSyncResponse: handleSyncResponse,
      onUserMessageAck: handleUserMessageAck,
      onUserInputIgnored: handleUserInputIgnored,
      onSlashResult: (payload) => {
        // /new → 切换到新会话，并记为该员工「最后选择」
        if (payload.new_session_id) {
          const contact =
            (payload as { contact_agent?: string }).contact_agent ||
            (useSessionStore.getState().currentSession?.config as
              | { contact_agent?: string }
              | undefined)?.contact_agent ||
            '';
          if (contact) {
            useSessionStore
              .getState()
              .rememberContactSession(String(contact).trim(), payload.new_session_id);
          }
          void switchSession(payload.new_session_id).catch(console.error);
          addToast(payload.reply || '已新建会话', 'success');
          return;
        }
        // 其余命令：stream_delta 已推正文；这里 idle + 刷新保证落库一致
        setIsStreaming(false);
        setStreamingContent('');
        setLiveToolCalls([]);
        setStreamStatusDetail(null);
        const sid = currentSession?.id;
        if (sid) {
          streamSessionApi().markIdle(sid);
          void loadMessages(sid).catch(console.error);
        }
        if (payload.reply) {
          // 若 stream 路径未拼出气泡，补一条 assistant
          const has = (useSessionStore.getState().messages || []).some(
            (m) => m.id === payload.message_id,
          );
          if (!has && payload.message_id) {
            addMessage({
              id: payload.message_id,
              session_id: sid || '',
              role: 'assistant',
              content: payload.reply,
              tool_calls: null,
              token_count: null,
              created_at: new Date().toISOString(),
            });
          }
        }
      },
      onToolEvent: handleToolEvent,
      onRunEvent: handleRunEvent,
      onGoalUpdate: handleGoalUpdate,
      onError: (err) => toastWsError(err),
      onSessionDeleted: (sid) => {
        addToast(t('chat.sessionDeleted') || '会话已删除', 'info');
        try {
          window.dispatchEvent(
            new CustomEvent('tevarn:session-invalid', { detail: { sessionId: sid } }),
          );
        } catch {
          /* ignore */
        }
      },
      getLastMessageId: getLast,
    });
    // 回 /chat：连接已在、handler 刚挂上 → 主动 sync 一次，补切页期间丢的 delta。
    // 不用固定 200ms：轮询等 isConnected（最多 ~1.5s），避免 session 切换瞬间绑在旧会话。
    let cancelled = false;
    let attempts = 0;
    const trySync = () => {
      if (cancelled) return;
      const api = useChatWsBridge.getState().api;
      if (api?.isConnected) {
        api.sendSync?.(getLast());
        return;
      }
      attempts += 1;
      if (attempts < 15) {
        window.setTimeout(trySync, 100);
      }
    };
    const t = window.setTimeout(trySync, 50);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
      useChatWsBridge.getState().setHandlers(null);
    };
  }, [
    handleStreamDelta,
    handleContentReset,
    handleStatusUpdate,
    handleSyncResponse,
    handleUserMessageAck,
    handleUserInputIgnored,
    handleToolEvent,
    handleRunEvent,
    handleGoalUpdate,
    toastWsError,
    switchSession,
    loadMessages,
    addMessage,
    addToast,
    currentSession?.id,
  ]);

  const bridgeApi = useChatWsBridge((s) => s.api);
  const isConnected = bridgeApi?.isConnected ?? false;
  const isConnecting = bridgeApi?.isConnecting ?? false;
  const kickedByPeer = bridgeApi?.kickedByPeer ?? false;
  const reclaimConnection = useCallback(
    () => bridgeApi?.reclaimConnection(),
    [bridgeApi],
  );
  const sendMessage = useCallback(
    (
      content: string,
      attachments?: Array<{
        filename: string;
        url: string;
        type: string;
        text_content?: string;
      }>,
      mode?: string,
      subAgentIds?: string[],
      opts?: { regenerate?: boolean; control?: 'steer' | 'queue' | 'interrupt' | 'stop' },
    ) => bridgeApi?.sendMessage(content, attachments, mode, subAgentIds, opts) ?? false,
    [bridgeApi],
  );
  const sendStop = useCallback(
    () => bridgeApi?.sendStop() ?? false,
    [bridgeApi],
  );
  const waitForConnection = useCallback(
    (sid?: string, ms?: number) =>
      bridgeApi?.waitForConnection(sid, ms) ?? Promise.resolve(false),
    [bridgeApi],
  );
  const connect = useCallback(
    (sid?: string, opts?: { force?: boolean }) => bridgeApi?.connect(sid, opts),
    [bridgeApi],
  );

  // 保持 streaming/stopping 最新值供 BroadcastChannel hello 回复
  React.useEffect(() => {
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);
  React.useEffect(() => {
    isStoppingRef.current = isStopping;
  }, [isStopping]);

  // 同会话多 Tab：BroadcastChannel 仅作占用提示（两边仍各连 WS 收 stream）
  // 不改本端 isStreaming，避免对端停跑时本端误收束 / 死锁
  React.useEffect(() => {
    const sid = currentSession?.id || '';
    tabChannelRef.current?.close();
    tabChannelRef.current = null;
    if (!sid) {
      const id = window.setTimeout(() => setPeerOccupied(false), 0);
      return () => window.clearTimeout(id);
    }
    const idClear = window.setTimeout(() => setPeerOccupied(false), 0);
    let peerLiveUntil = 0;
    const ch = openSessionTabChannel(sid, (msg) => {
      if (msg.type === 'stream_state' || msg.type === 'peer_claim') {
        const peerOn = Boolean(msg.isStreaming);
        if (peerOn) {
          peerLiveUntil = Date.now() + 15_000;
          setPeerOccupied(true);
        } else {
          peerLiveUntil = 0;
          setPeerOccupied(false);
        }
      } else if (msg.type === 'hello') {
        // 新 Tab 探活：若本端在跑，立刻宣告
        if (isStreamingRef.current) {
          ch.post({
            type: 'stream_state',
            sessionId: sid,
            isStreaming: true,
            isStopping: isStoppingRef.current,
            statusDetail: null,
          });
        }
      }
    });
    tabChannelRef.current = ch;
    ch.post({ type: 'hello', sessionId: sid });
    // 对端若崩溃未发 stop：15s 无心跳则清 banner
    const tick = window.setInterval(() => {
      if (peerLiveUntil && Date.now() > peerLiveUntil) {
        peerLiveUntil = 0;
        setPeerOccupied(false);
      }
    }, 3000);
    return () => {
      window.clearTimeout(idClear);
      window.clearInterval(tick);
      ch.close();
      if (tabChannelRef.current === ch) tabChannelRef.current = null;
    };
  }, [currentSession?.id]);

  // 本端 streaming 变化 → 广播给其它 Tab
  React.useEffect(() => {
    const sid = currentSession?.id;
    if (!sid || !tabChannelRef.current) return;
    tabChannelRef.current.post({
      type: 'stream_state',
      sessionId: sid,
      isStreaming,
      isStopping,
      statusDetail: streamStatusDetail,
    });
  }, [isStreaming, isStopping, streamStatusDetail, currentSession?.id]);

  const handleLoadOlder = useCallback(async () => {
    const sid = currentSession?.id;
    if (!sid) return { loaded: 0, hasMore: false };
    return useSessionStore.getState().loadOlderMessages(sid);
  }, [currentSession?.id]);

  // 发送消息（乐观 UI：先出用户气泡 + streaming，session/WS 后台并行）
  // 发送成功后会话将出现在「历史会话」中
  const sendInFlightRef = useRef(false);
  const handleSend = useCallback(
      async (
        content: string,
        attachments: Attachment[] = [],
        mode: ChatMode = 'default',
        subAgentIds?: string[],
        control?: 'steer' | 'queue' | 'interrupt'
      ): Promise<boolean> => {
        if (sendInFlightRef.current) return false;
        sendInFlightRef.current = true;

        // D10 专业模式：强制项目文件夹
        if (useWorkspaceStore.getState().uiMode === 'pro' && !useWorkspaceStore.getState().root) {
          useWorkspaceStore.getState().setForceProjectOpen(true);
          sendInFlightRef.current = false;
          return false;
        }

        if (mode === 'cluster' && (!subAgentIds || subAgentIds.length === 0)) {
          addToast(t('chat.clusterNeedAgent'), 'error');
          sendInFlightRef.current = false;
          return false;
        }

        let session = currentSession;
        if (!session) {
          setCreatingSession(true);
          try {
            session = await createAndLoadSession();
          } catch (e) {
            console.error(t('page._e1'), e);
            addToast(t('chat.createSessionFailed'), 'error');
            sendInFlightRef.current = false;
            return false;
          } finally {
            setCreatingSession(false);
          }
          if (!session) {
            addToast(t('chat.createSessionFailed2'), 'error');
            sendInFlightRef.current = false;
            return false;
          }
        }

        // 只带可发送附件（排除 blob/上传失败），避免乐观文案与真实 payload 不一致
        const sendableAtts = (attachments || []).filter((a) => {
          const u = String(a.url || '').trim();
          if (!u || u.startsWith('blob:') || u.startsWith('data:')) return false;
          if (a.status === 'error' || a.status === 'uploading') return false;
          return true;
        });
        if ((attachments?.length || 0) > 0 && sendableAtts.length === 0 && !content.trim()) {
          addToast(t('chat.removeFailedAttachments'), 'error');
          sendInFlightRef.current = false;
          return false;
        }

        // 乐观气泡与后端 _build_user_input_with_attachments 对齐，便于 ack reconcile
        let displayContent = content;
        if (sendableAtts.length > 0) {
          const parts = [content];
          sendableAtts.forEach((a, i) => {
            parts.push(`\n\n[附件 ${i + 1}: ${a.filename}]`);
            if (a.text_content) {
              const preview = a.text_content.slice(0, 8000);
              parts.push(
                a.text_content.length > 8000 ? `${preview}\n...（内容已截断）` : preview
              );
            } else if ((a.type || '').startsWith('image/') || /\.(png|jpe?g|gif|webp)$/i.test(a.filename)) {
              parts.push(`[图片文件] ${a.url || ''}`);
            } else {
              parts.push(`[文件类型: ${a.type || 'unknown'}] ${a.url || ''}`);
            }
          });
          displayContent = parts.join('\n');
        }

        // 乐观：临时 id；steer/queue 与后端落库前缀对齐，便于 ack reconcile
        const optId = `optimistic:${generateUUID()}`;
        let optimisticContent = displayContent;
        if (control === 'steer') optimisticContent = `【纠偏】${displayContent}`;
        else if (control === 'queue') optimisticContent = `【排队】${displayContent}`;
        else if (control === 'interrupt') optimisticContent = displayContent; // 与 interrupt 后新 run persist 对齐
        const userMsg: Message = {
          id: optId,
          session_id: session.id,
          role: 'user',
          content: optimisticContent,
          tool_calls: null,
          token_count: null,
          created_at: new Date().toISOString(),
        };
        addMessage(userMsg);
        useSessionStore.getState().touchSessionActivity(session.id);
        locallyStoppedRef.current.delete(session.id);
        setStoppingSid(session.id, false);
        // steer/queue 不打断当前 streaming 状态机
        if (!control || control === 'interrupt') {
          setIsStreaming(true);
          setStreamingContent('');
          setLiveToolCalls([]);
          setStreamStatusDetail(
            control === 'interrupt'
              ? (t('chat.interruptStarting') || '停止当前任务并开始新任务…')
              : t('chat.connectingSend')
          );
        } else if (control === 'steer') {
          setStreamStatusDetail(t('chat.steerApplied') || '纠偏已提交');
        } else if (control === 'queue') {
          setStreamStatusDetail(t('chat.queued') || '已排队，本轮结束后执行');
        }

        const dropGhost = () => {
          useSessionStore.getState().removeMessage(optId);
          // 仅新 run / interrupt 失败时清 streaming；steer/queue 失败不能误关当前 run
          if (!control || control === 'interrupt') {
            if (useSessionStore.getState().currentSession?.id === session!.id) {
              setIsStreaming(false);
              setStreamStatusDetail(null);
            }
            streamSessionApi().markIdle(session!.id);
          }
        };

        try {
          const ready = await waitForConnection(session.id, 15000);
          if (!ready) {
            addToast(
              kickedByPeer
                ? (t('chat.wsKickedInputDisabled') || t('chat.wsKickedByPeer'))
                : t('chat.channelNotConnected'),
              'error',
            );
            dropGhost();
            return false;
          }

          setStreamStatusDetail(mode === 'cluster' ? t('chat.clusterWorking') : t('chat.thinking'));
          const sent = sendMessage(content, sendableAtts, mode, subAgentIds, control ? { control } : undefined);
          if (!sent) {
            addToast(t('chat.sendFailedDisconnected'), 'error');
            dropGhost();
            return false;
          }
          return true;
        } finally {
          // streaming 已 true 时由 MessageInput disabled 挡二次发送；此处放行以便失败后可重发
          window.setTimeout(() => {
            sendInFlightRef.current = false;
          }, 400);
        }
      },
      [
        currentSession,
        addMessage,
        addToast,
        sendMessage,
        createAndLoadSession,
        waitForConnection,
        t,
        setStoppingSid,
        kickedByPeer,
      ]
    );

  // 重新生成（若仍在跑则先 stop 并等到 idle，再发同一条用户内容；不插乐观气泡）
  const handleRegenerate = useCallback(
    async (_message: Message) => {
      if (!currentSession) return;
      const sid = currentSession.id;
      const msgs = useSessionStore.getState().messages;
      const lastUserMsg = [...msgs].reverse().find((m) => m.role === 'user');
      if (!lastUserMsg?.content) return;
      if (isStreaming || isStoppingSid(sid) || streamSessionApi().get(sid).agentRunning) {
        setStoppingSid(sid, true);
        setStreamStatusDetail(t('chat.stopping') || 'Stopping…');
        sendStop();
        // 等到本会话 idle / 停止标志清除，最长 12s（勿固定 800ms）
        const deadline = Date.now() + 12_000;
        while (Date.now() < deadline) {
          const st = streamSessionApi().get(sid);
          if (!isStoppingSid(sid) && !st.agentRunning && !st.isStreaming) break;
          // idle 回调会清 stopping；也接受 cache 已 idle
          if (!st.agentRunning && !st.isStreaming) {
            setStoppingSid(sid, false);
            break;
          }
          await new Promise((r) => setTimeout(r, 150));
        }
        setStoppingSid(sid, false);
      }
      if (useSessionStore.getState().currentSession?.id !== sid) return;
      setIsStreaming(true);
      setStreamingContent('');
      setLiveToolCalls([]);
      setStreamStatusDetail(t('chat.connectingSend'));
      const ready = await waitForConnection(sid, 15000);
      if (!ready) {
        addToast(t('chat.channelNotConnected2'), 'error');
        setIsStreaming(false);
        setStreamStatusDetail(null);
        return;
      }
      setStreamStatusDetail(t('chat.thinking'));
      // regenerate：后端不重复落库用户句
      if (sendMessage(lastUserMsg.content, [], 'default', undefined, { regenerate: true })) {
        setIsStreaming(true);
        setStreamingContent('');
      } else {
        addToast(t('chat.sendFailedDisconnected'), 'error');
        setIsStreaming(false);
        setStreamStatusDetail(null);
      }
    },
    [
      currentSession,
      sendMessage,
      sendStop,
      waitForConnection,
      addToast,
      t,
      isStreaming,
      isStoppingSid,
      setStoppingSid,
    ]
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
      // 保持 streaming UI + stopping 态，等服务端 status:idle 再清（避免假停后 late delta）
      const sid = currentSession?.id || '';
      if (!sid) return;
      setStoppingSid(sid, true);
      setIsStreaming(true);
      setStreamStatusDetail(t('chat.stopping') || 'Stopping…');
      const ok = sendStop();
      if (!ok) {
        // 未连上：本地直接收束，保留已流出正文
        const leftover = streamingContentRef.current || '';
        const leftoverTools = snapshotStoppedTools(liveToolCallsRef.current);
        setStoppingSid(sid, false);
        if (useSessionStore.getState().currentSession?.id === sid) {
          setIsStreaming(false);
          setStreamStatusDetail(null);
          setLiveToolCalls(leftoverTools);
          streamingContentRef.current = '';
          setStreamingContent('');
        }
        streamSessionApi().markIdle(sid);
        keepPartialAssistantOnIdle(
          sid,
          leftover,
          loadMessages,
          addMessage,
          leftoverTools,
        );
        return;
      }
      // 兜底：8s 仍无 idle 则强制收束——仅影响发起 stop 的 sid，且仅当仍在看该会话时改 UI
      window.setTimeout(() => {
        if (!isStoppingSid(sid)) return;
        locallyStoppedRef.current.add(sid);
        const leftover = streamingContentRef.current || '';
        const leftoverTools = snapshotStoppedTools(liveToolCallsRef.current);
        setStoppingSid(sid, false);
        streamSessionApi().markIdle(sid);
        if (useSessionStore.getState().currentSession?.id === sid) {
          setIsStreaming(false);
          setStreamStatusDetail(null);
          setLiveToolCalls(leftoverTools);
          streamingContentRef.current = '';
          setStreamingContent('');
        }
        keepPartialAssistantOnIdle(
          sid,
          leftover,
          loadMessages,
          addMessage,
          leftoverTools,
        );
      }, 8000);
    }, [sendStop, currentSession, loadMessages, addMessage, t, setStoppingSid, isStoppingSid]);

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

  // 全局搜索选择会话 → 直接进入该会话（保留目标会话缓存的 streaming 态，不强制 idle）
  const handleSearchSelect = useCallback(
    async (sessionId: string) => {
      setSearchOpen(false);
      // 先存当前会话流式态，再切；目标会话由 session-change effect 从 cache 恢复
      const cur = useSessionStore.getState().currentSession?.id;
      if (cur) {
        streamSessionApi().save(cur, {
          isStreaming,
          agentRunning: isStreaming,
          content: streamingContentRef.current || streamingContent,
          tools: liveToolCalls,
          statusDetail: streamStatusDetail,
        });
      }
      const cached = streamSessionApi().get(sessionId);
      if (cached.agentRunning || cached.isStreaming || cached.content) {
        setIsStreaming(true);
        setStreamingContent(cached.content || '');
        streamingContentRef.current = cached.content || '';
        setLiveToolCalls(cached.tools || []);
        setStreamStatusDetail(cached.statusDetail || t('chat.resuming'));
      } else {
        // 未知是否在跑：先不清 streaming，等 switch 后 sync_response 校正
        setStreamingContent('');
        streamingContentRef.current = '';
        setLiveToolCalls([]);
      }
      if (switchSession) {
        await switchSession(sessionId);
      }
    },
    [switchSession, isStreaming, streamingContent, liveToolCalls, streamStatusDetail]
  );

  // ====== Keyboard Shortcuts ======
  useKeyboardShortcuts([
    { key: 'k', meta: true, handler: () => setSearchOpen(true) },
    { key: 'Escape', handler: () => setSearchOpen(false), preventDefault: false },
    { key: 'n', meta: true, shift: true, handler: () => createAndLoadSession().catch(console.error) },
    { key: ',', meta: true, handler: () => router.push('/settings') },
    { key: '/', meta: true, handler: () => setSearchOpen(true) },
    { key: 'b', ctrl: true, handler: () => toggleInspectorTab(uiMode === 'pro' ? 'files' : 'run') },
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

  // Depend on the primitive id so React Compiler can preserve this memoization
  // even when the session store replaces the containing object.
  const currentSessionId = currentSession?.id || '';

  // 防御：绝不在 B 会话渲染 A 的正式历史；useMemo 稳定引用避免每 token 全树失效
  const displayMessages = React.useMemo(() => {
    const base = messages.filter(
      (m) =>
        !m.session_id ||
        !currentSessionId ||
        m.session_id === currentSessionId ||
        String(m.id || '').startsWith('optimistic:') ||
        String(m.id || '').startsWith('streaming'),
    );
    if (!(isStreaming || streamingContent || liveToolCalls.length > 0)) {
      return base;
    }
    const liveToolCallsForMsg =
      liveToolCalls.length > 0
        ? liveToolCalls.map((tc, i) => ({
            id: tc.id || `tool-${tc.name}-${i}`,
            name: tc.name,
            arguments: tc.arguments,
            result: tc.result,
            status: tc.status,
          }))
        : null;
    return [
      ...base,
      {
        id: 'streaming',
        session_id: currentSessionId,
        role: 'assistant' as const,
        content: streamingContent || '',
        tool_calls: liveToolCallsForMsg as Message['tool_calls'],
        token_count: null,
        created_at: new Date().toISOString(),
      },
    ];
  }, [
    messages,
    currentSessionId,
    isStreaming,
    streamingContent,
    liveToolCalls,
  ]);

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

      {/* P1：对话 = 联系员工，不是公司主入口 */}
      {!contactIdentity && !projectGroupId && !sessionIdentity ? (
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle/60 bg-card-bg/90 px-4 py-2.5 text-[11.5px] text-foreground-muted">
          <span>
            {t('chat.contactHint')}
          </span>
          <span className="flex shrink-0 gap-3 font-semibold">
            <Link href="/agents" className="text-brand-purple no-underline">
              {t('nav.agents')}
            </Link>
            <Link href="/" className="text-foreground-dim no-underline">
              {t('nav.dashboard')}
            </Link>
          </span>
        </div>
      ) : null}

      {/* 顶部状态栏 —— 企业 IM：联系人 / 项目组 */}
            <header className="flex items-center justify-between border-b border-border-subtle/50 bg-page-bg/80 backdrop-blur-xl px-5 py-2.5 sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <h1 className="text-[0.8125rem] font-semibold tracking-tight text-foreground">
                  {projectGroupId ? (
                    <>📁 {t('chat.projectGroup') === 'chat.projectGroup' ? '项目组' : t('chat.projectGroup')}</>
                  ) : sessionIdentity || contactIdentity ? (
                    <>
                      <span className="text-foreground-dim font-medium">
                        {t('nav.chatContact') === 'nav.chatContact' ? '联系 ' : 'Contact '}
                      </span>
                      {sessionIdentity || contactIdentity}
                    </>
                  ) : (
                    t('nav.chatContact') === 'nav.chatContact' ? '联系员工' : t('nav.chatContact')
                  )}
                </h1>
                {sessionIdentity && !projectGroupId ? (
                  <span className="rounded-full border border-border-subtle bg-card-bg px-2 py-0.5 text-[10px] font-medium text-foreground-dim">
                    1:1
                  </span>
                ) : null}
                {currentSession && (sessionIdentity || contactIdentity) ? (
                  <ContactSessionPicker
                    contactName={sessionIdentity || contactIdentity || ''}
                    currentSessionId={currentSession.id}
                    onSelect={(sid) => {
                      void switchSession(sid).catch(console.error);
                    }}
                  />
                ) : currentSession ? (
                  <span className="chat-meta font-mono text-foreground-dim">
                    {currentSession.id.slice(0, 8)}
                  </span>
                ) : null}
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
                              <div className="flex items-center rounded-lg border border-border-subtle p-0.5">
                              {uiMode === 'pro' && (
                                <button
                                  type="button"
                                  onClick={() => toggleInspectorTab('files')}
                                  className={`relative inline-flex h-7 w-7 items-center justify-center rounded-[3px] ${
                                    inspectorTab === 'files'
                                      ? 'bg-brand-purple/15 text-brand-cyan'
                                      : 'text-foreground-muted hover:bg-card-bg-hover'
                                  }`}
                                  title={t('chat.inspectorFiles')}
                                  aria-label={t('chat.inspectorFiles')}
                                  aria-pressed={inspectorTab === 'files'}
                                >
                                  <FolderOpen className="h-3.5 w-3.5" />
                                  {unreadTerminal && inspectorTab !== 'files' && (
                                    <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-cyan" />
                                  )}
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => toggleInspectorTab('preview')}
                                className={`relative inline-flex h-7 w-7 items-center justify-center rounded-[3px] ${
                                  inspectorTab === 'preview'
                                    ? 'bg-brand-purple/15 text-brand-cyan'
                                    : 'text-foreground-muted hover:bg-card-bg-hover'
                                }`}
                                title={t('chat.inspectorPreview')}
                                aria-label={t('chat.inspectorPreview')}
                                aria-pressed={inspectorTab === 'preview'}
                              >
                                <Eye className="h-3.5 w-3.5" />
                                {previewArtifact && inspectorTab !== 'preview' ? (
                                  <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-cyan" />
                                ) : null}
                              </button>
                              <button
                                type="button"
                                onClick={() => toggleInspectorTab('terminal')}
                                className={`relative inline-flex h-7 w-7 items-center justify-center rounded-[3px] ${
                                  inspectorTab === 'terminal'
                                    ? 'bg-brand-purple/15 text-brand-cyan'
                                    : 'text-foreground-muted hover:bg-card-bg-hover'
                                }`}
                                title={t('terminal.title')}
                                aria-label={t('terminal.toggle')}
                                aria-pressed={inspectorTab === 'terminal'}
                              >
                                <Terminal className="h-3.5 w-3.5" />
                                {termHasEntries && inspectorTab !== 'terminal' ? (
                                  <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-cyan" />
                                ) : null}
                              </button>
                              <button
                                type="button"
                                onClick={() => toggleInspectorTab('run')}
                                className={`relative inline-flex h-7 w-7 items-center justify-center rounded-[3px] ${
                                  inspectorTab === 'run'
                                    ? 'bg-brand-purple/15 text-brand-cyan'
                                    : 'text-foreground-muted hover:bg-card-bg-hover'
                                }`}
                                title={t('chat.inspectorRun')}
                                aria-label={t('chat.inspectorRun')}
                                aria-pressed={inspectorTab === 'run'}
                              >
                                <ListTodo className="h-3.5 w-3.5" />
                                {activeGoal &&
                                  (activeGoal.status === 'active' ||
                                    (activeGoal.todos && activeGoal.todos.length > 0)) &&
                                  inspectorTab !== 'run' && (
                                    <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-cyan" />
                                  )}
                              </button>
                              <button
                                type="button"
                                onClick={() => toggleInspectorTab('trace')}
                                className={`relative inline-flex h-7 w-7 items-center justify-center rounded-[3px] ${
                                  inspectorTab === 'trace'
                                    ? 'bg-brand-purple/15 text-brand-cyan'
                                    : 'text-foreground-muted hover:bg-card-bg-hover'
                                }`}
                                title={t('chat.inspectorTrace')}
                                aria-label={t('chat.inspectorTrace')}
                                aria-pressed={inspectorTab === 'trace'}
                              >
                                <ScanSearch className="h-3.5 w-3.5" />
                              </button>
                              </div>
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
                            </div>
            </header>

            {/* 主内容区：项目组进度 或 1:1 消息 */}
                  <div className="relative flex min-h-0 flex-1 overflow-hidden">
                    <main className="chat-main-column">
                      {projectGroupId ? (
                        <div className="chat-messages-pane min-h-0 flex-1">
                          <ProjectGroupView
                            groupId={projectGroupId}
                            onOpenContact={(name) => {
                              router.push(`/chat?identity=${encodeURIComponent(name)}`);
                              void openContactSession(name);
                            }}
                          />
                        </div>
                      ) : (
                      <div className="chat-messages-pane">
                        {sessionLoadError && currentSession ? (
                          <div className="flex items-center justify-between gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
                            <span>{t('chat.historyLoadFailed')}</span>
                            <button
                              type="button"
                              className="rounded border border-amber-500/40 px-2 py-0.5 hover:bg-amber-500/20"
                              onClick={() => void loadMessages(currentSession.id)}
                            >
                              {t('chat.retryLoad')}
                            </button>
                          </div>
                        ) : null}
                                                <ChatWindow
                          messages={displayMessages}
                          isStreaming={isStreaming}
                          onStopStreaming={handleStopStreaming}
                          onTagClick={handleTagClick}
                          onRegenerate={handleRegenerate}
                          onEdit={handleEdit}
                          onExampleSelect={(text) => setEditingContent(text)}
                          onPreviewArtifact={openPreview}
                          contactName={sessionIdentity || null}
                          sessionId={currentSession?.id || null}
                          onLoadOlder={handleLoadOlder}
                        />
                      </div>
                      )}
                      {!projectGroupId ? (
                      <>
                      {kickedByPeer && !!currentSession && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-100">
                          <span>
                            {t('chat.wsKickedByPeer') ||
                              '此会话已在其它窗口连接 · 本窗已停止自动重连，避免互抢断流'}
                          </span>
                          <button
                            type="button"
                            onClick={() => reclaimConnection()}
                            className="shrink-0 rounded-md border border-amber-400/40 bg-amber-500/20 px-2 py-0.5 text-[11px] font-medium text-amber-50 hover:bg-amber-500/30"
                          >
                            {t('chat.reclaimWs') || '夺取连接'}
                          </button>
                        </div>
                      )}
                      {peerOccupied && !kickedByPeer && !!currentSession && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-200">
                          <span>
                            {t('chat.peerStreaming') ||
                              '另一浏览器窗口也在使用此会话（流式状态提示）'}
                          </span>
                        </div>
                      )}
                      {!isConnected && !isConnecting && !kickedByPeer && !!currentSession && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-card-bg/60 px-3 py-1.5 text-[11px] text-foreground-dim">
                          <span>{t('chat.channelIdle')}</span>
                          <button
                            type="button"
                            onClick={() => connect(currentSession.id, { force: true })}
                            className="shrink-0 rounded-md border border-border-subtle px-2 py-0.5 text-[11px] hover:border-brand-cyan/40"
                          >
                            {t('chat.reconnect') || '重连'}
                          </button>
                        </div>
                      )}
                      <ComposerContextStrip
                        messages={displayMessages}
                        onPreview={openPreview}
                        liveToolCalls={liveToolCalls}
                        streamStatusDetail={streamStatusDetail}
                        isStreaming={isStreaming}
                        goal={activeGoal}
                        planLabel={planPhase}
                        skillLabels={injectedSkills}
                        phaseLabel={
                          codingDelivery?.phase_label ||
                          codingDelivery?.phase ||
                          (streamStatusDetail && /understand|plan|edit|test|review|deliver/i.test(streamStatusDetail)
                            ? streamStatusDetail
                            : null)
                        }
                        sessionId={currentSession?.id}
                        capsCount={runCaps?.caps}
                        toolsCount={runCaps?.tools}
                        softRenew={runCaps?.soft}
                        liveModel={liveModel}
                      />
                      {/* 卡住：优先提示停止，再给恢复卡 */}
                      {streamStuck && isStreaming && !isStopping && (
                        <div className="mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                          <span className="min-w-0 flex-1">
                            {t('chat.streamStuck') || '长时间无响应 — 可先停止本轮，再从断点恢复'}
                          </span>
                          <button
                            type="button"
                            className="flex-shrink-0 rounded-md bg-amber-500/20 px-2 py-1 font-semibold text-amber-100 hover:bg-amber-500/30"
                            onClick={() => handleStopStreaming()}
                          >
                            {t('chat.stop') || '停止'}
                          </button>
                        </div>
                      )}
                      {/* R-02：非流式 / 停止中 / 长时间无 delta 卡住 → 露出恢复入口 */}
                      {currentSession?.id && (!isStreaming || isStopping || streamStuck) ? (
                        <ChatRecoveryCard
                          sessionId={currentSession.id}
                          recovery={recovery}
                          zh
                          onResumed={() => {
                            setIsStreaming(true);
                            setRecovery(null);
                          }}
                        />
                      ) : null}
                      {codingDelivery ? (
                        <div className="px-3 pb-2">
                          <CodingDeliveryCard
                            delivery={codingDelivery}
                            onRollback={async (checkpoint) => {
                              try {
                                const { restoreFileCheckpoint } = await import('@/lib/api');
                                const r = await restoreFileCheckpoint(checkpoint, {
                                  sessionId: currentSession?.id,
                                });
                                if (r?.ok) {
                                  addToast(
                                    (t('chat.checkpointRestored') as string) ||
                                      `已回滚: ${r.restored || checkpoint}`,
                                    'success'
                                  );
                                } else {
                                  addToast(r?.error || '回滚失败', 'error');
                                }
                              } catch (e) {
                                addToast((e as Error)?.message || '回滚失败', 'error');
                              }
                            }}
                          />
                        </div>
                      ) : null}
                      <MessageInput
                                              ref={composerRef}
                                              // audit-fix: key 带 sessionId，切会话强制 remount，配合 per-session 草稿
                                              key={`${currentSession?.id ?? 'no-session'}:${editingContent ?? 'default'}`}
                                              onSend={handleSend}
                                              onGenerateImage={handleGenerateImage}
                                              disabled={
                                                isGeneratingImage ||
                                                creatingSession ||
                                                // 被踢后禁用输入；streaming 时仍可 steer/queue（P0）
                                                kickedByPeer
                                              }
                                              isStreaming={isStreaming}
                                              sessionId={currentSession?.id}
                                              onStopStreaming={handleStopStreaming}
                                              placeholder={
                                                kickedByPeer
                                                  ? (t('chat.wsKickedInputDisabled') ||
                                                    '已在其它窗口连接 — 点「夺取连接」后再发送')
                                                  : creatingSession
                                                  ? t('chat.creating')
                                                  : isStreaming
                                                    ? (t('chat.steerPlaceholder') || '输入纠偏指令，Enter 发送 · 可排队')
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
                      </>
                      ) : null}
                    </main>

                    <ChatInspector
                      uiMode={uiMode}
                      previewArtifact={previewArtifact}
                      onClosePreview={() => setPreviewArtifact(null)}
                      messages={messages}
                      liveToolCalls={liveToolCalls}
                      sessionId={currentSession?.id}
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
                                      </div>

                                      <OpenProjectModal />
                                    </div>
                                  );
                                }
