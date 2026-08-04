'use client';

import React, { Suspense, useState, useCallback, useRef } from 'react';
import Link from 'next/link';
import { ChatWindow } from '@/components/chat/ChatWindow';
import { ProjectGroupView } from '@/components/chat/ProjectGroupView';
import { MessageInput, Attachment, ChatMode, type MessageInputHandle } from '@/components/chat/MessageInput';
import { FilePreviewHost } from '@/components/chat/FilePreviewHost';
import { SessionArtifactsBar } from '@/components/chat/SessionArtifactsBar';
import type { ChatArtifact } from '@/lib/artifacts';
import { TerminalPanel, formatArgsText, formatResultText } from '@/components/chat/TerminalPanel';
import { ActivityPanel } from '@/components/chat/ActivityPanel';
import { ChatStatusStrip } from '@/components/chat/ChatStatusStrip';
import { TaskPanel } from '@/components/tasks/TaskPanel';
import { TransparencyPanel } from '@/components/chat/TransparencyPanel';
import { GlobalSearch } from '@/components/search/GlobalSearch';
import { useSession } from '@/hooks/useSession';
import { useChatWsBridge } from '@/stores/chatWsBridge';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useSessionStore } from '@/stores/sessionStore';
import { Message, StatusUpdateMessage, StreamDeltaMessage, GoalUpdateMessage, GoalState, ToolEventMessage, RunEventMessage } from '@/types';
import { useTerminalStore } from '@/stores/terminalStore';
import { generateImage, type SessionRecoveryPayload } from '@/lib/api';
import { ChatRecoveryCard } from '@/components/chat/ChatRecoveryCard';
import { generateUUID } from '@/lib/uuid';
import { useRouter, useSearchParams } from 'next/navigation';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { WorkspaceDock } from '@/components/workspace/WorkspaceDock';
import { OpenProjectModal } from '@/components/workspace/OpenProjectModal';
import { DangerConfirmDialog } from '@/components/chat/DangerConfirmDialog';
import { ContactSessionPicker } from '@/components/chat/ContactSessionPicker';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';
import { streamSessionApi } from '@/stores/streamSessionStore';
import { openSessionTabChannel } from '@/lib/sessionTabChannel';


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

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const contactIdentity = (searchParams.get('identity') || '').trim();
  const projectGroupId = (searchParams.get('group') || '').trim();
  const { currentSession, messages, addMessage, createAndLoadSession, openContactSession, loadMessages, switchSession } = useSession();
  const reconcileMessage = useSessionStore((s) => s.reconcileMessage);
    // token 由 AppShell GlobalChatWs 使用；chat 页不再直接连 WS
    const {
      uiMode,
      setUiMode,
      dockOpen,
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
        /** 流式活动时间戳：长时间无 delta 则视为卡住，露出恢复入口 */
        const lastStreamActivityRef = useRef<number>(Date.now());
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
            setStreamStatusDetail(cached.statusDetail || 'Resuming…');
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
                new CustomEvent('takton:session-invalid', { detail: { sessionId: sid } })
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
                new CustomEvent('takton:session-invalid', { detail: { sessionId: sid } })
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
            if (
              cp.goal.status === 'active' ||
              (cp.goal.todos && cp.goal.todos.length > 0)
            ) {
              setIsTaskPanelOpen(true);
            }
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
      setStreamStuck(false);
      return;
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
      const cur = useSessionStore.getState().currentSession?.id;
      if (id && cur === id) {
        useSessionStore.getState().setCurrentSession(null);
        useSessionStore.getState().clearMessages();
        setIsStreaming(false);
        setStreamStatusDetail(null);
      }
    };
    window.addEventListener('takton:session-invalid', onInvalid);
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
    return () => window.removeEventListener('takton:session-invalid', onInvalid);
  }, []);

  const handleStreamDelta = useCallback((msg: StreamDeltaMessage) => {
      const sid = currentSession?.id || '';
      if (isStoppingSid(sid)) return; // 仅丢弃「本会话」停止中的 late delta
      setIsStreaming(true);
      lastStreamActivityRef.current = Date.now();
      setStreamStuck(false);
      setStreamingContent((prev) => {
        const mid = msg.message_id || '';
        const store = streamSessionApi();
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
          appendAgentOutput(
            String(msg.result).slice(0, 12000),
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
        // 软断线：状态行持续提示，toast 放宽到 12s 节流（原 4s 几乎无反馈）
        if (soft && !opts?.force) {
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
            sessionStorage.setItem('takton:last_host_epoch', String(epoch));
          } catch {
            /* ignore */
          }
        }
      };
      window.addEventListener('takton:host-epoch', onEpoch);
      return () => window.removeEventListener('takton:host-epoch', onEpoch);
    }, [t]);

    const handleStatusUpdate = useCallback((msg: StatusUpdateMessage) => {
      const sid = currentSession?.id || '';
      if (msg.state === 'thinking' || msg.state === 'tool_executing' || msg.state === 'optimizing') {
        // 用户已点停止：忽略迟到的 running 态，避免假停被冲掉
        if (isStoppingSid(sid)) {
          setStreamStatusDetail(msg.detail || t('chat.stopping') || 'Stopping…');
          return;
        }
        setIsStreaming(true);
        lastStreamActivityRef.current = Date.now();
        if (msg.detail) {
          setStreamStatusDetail(msg.detail);
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
              const wasStopping = isStoppingSid(sid);
              setStoppingSid(sid, false);
              setStreamStuck(false);
              setIsStreaming(false);
              setStreamStatusDetail(null);
              // keep last runCaps for this session (switch-back + post-run)
              if (sid && runCapsCacheRef.current[sid] == null) {
                /* already cached on status updates */
              }
              const leftover = streamingContentRef.current;
              streamingContentRef.current = '';
              setStreamingContent('');
              // 先把残留 running 标 completed，再清空，避免 idle 瞬间 UI 仍显示「运行中」
              setLiveToolCalls((prev) =>
                prev.map((t) =>
                  t.status === 'failed'
                    ? t
                    : { ...t, status: 'completed' as const },
                ),
              );
              // 下一帧清空 live 列表（历史消息已 load）
              window.setTimeout(() => setLiveToolCalls([]), 0);
              if (sid) streamSessionApi().markIdle(sid);
              // 停止路径：不把 partial 当最终消息二次插入（loadMessages 会拉权威历史）
              if (leftover || sid) {
                setTimeout(() => {
                  if (leftover && !wasStopping) {
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
          }, [addMessage, addToast, currentSession, loadMessages, t, isStoppingSid, setStoppingSid]);

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
              statusDetail: payload.stream_status || 'Resuming…',
              streamMessageId: payload.stream_message_id || null,
            });
          }
        } else {
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
      }, [reconcileMessage, currentSession?.id, loadMessages]);

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
      onStatusUpdate: handleStatusUpdate,
      onSyncResponse: handleSyncResponse,
      onUserMessageAck: handleUserMessageAck,
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
    handleStatusUpdate,
    handleSyncResponse,
    handleUserMessageAck,
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
  const reclaimConnection = () => bridgeApi?.reclaimConnection();
  const sendMessage = (
    content: string,
    attachments?: Array<{
      filename: string;
      url: string;
      type: string;
      text_content?: string;
    }>,
    mode?: string,
    subAgentIds?: string[],
    opts?: { regenerate?: boolean },
  ) =>
    bridgeApi?.sendMessage(content, attachments, mode, subAgentIds, opts) ?? false;
  const sendStop = () => bridgeApi?.sendStop() ?? false;
  const waitForConnection = (sid?: string, ms?: number) =>
    bridgeApi?.waitForConnection(sid, ms) ?? Promise.resolve(false);
  const connect = (sid?: string, opts?: { force?: boolean }) =>
    bridgeApi?.connect(sid, opts);

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
    setPeerOccupied(false);
    if (!sid) return;
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
        subAgentIds?: string[]
      ) => {
        // 防连点/连 Enter：父级 isStreaming 尚未置位时的竞态；停止态按会话
        const stopCheckSid = currentSession?.id || '';
        if (sendInFlightRef.current || isStoppingSid(stopCheckSid)) return;
        sendInFlightRef.current = true;

        // D10 专业模式：强制项目文件夹
        if (useWorkspaceStore.getState().uiMode === 'pro' && !useWorkspaceStore.getState().root) {
          useWorkspaceStore.getState().setForceProjectOpen(true);
          sendInFlightRef.current = false;
          return;
        }

        if (mode === 'cluster' && (!subAgentIds || subAgentIds.length === 0)) {
          addToast(t('chat.clusterNeedAgent'), 'error');
          sendInFlightRef.current = false;
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
            sendInFlightRef.current = false;
            return;
          } finally {
            setCreatingSession(false);
          }
          if (!session) {
            addToast(t('chat.createSessionFailed2'), 'error');
            sendInFlightRef.current = false;
            return;
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
          return;
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

        // 乐观：临时 id，sync/load 后用服务端 id reconcile，避免双气泡
        const optId = `optimistic:${generateUUID()}`;
        const userMsg: Message = {
          id: optId,
          session_id: session.id,
          role: 'user',
          content: displayContent,
          tool_calls: null,
          token_count: null,
          created_at: new Date().toISOString(),
        };
        addMessage(userMsg);
        useSessionStore.getState().touchSessionActivity(session.id);
        setStoppingSid(session.id, false);
        setIsStreaming(true);
        setStreamingContent('');
        setLiveToolCalls([]);
        setStreamStatusDetail(t('chat.connectingSend'));

        const dropGhost = () => {
          useSessionStore.getState().removeMessage(optId);
          if (useSessionStore.getState().currentSession?.id === session!.id) {
            setIsStreaming(false);
            setStreamStatusDetail(null);
          }
          streamSessionApi().markIdle(session!.id);
        };

        try {
          const ready = await waitForConnection(session.id, 15000);
          if (!ready) {
            addToast(t('chat.channelNotConnected'), 'error');
            dropGhost();
            return;
          }

          setStreamStatusDetail(mode === 'cluster' ? t('chat.clusterWorking') : t('chat.thinking'));
          // 只发可发送附件，避免把失败 chip 带进 WS
          const sent = sendMessage(content, sendableAtts, mode, subAgentIds);
          if (!sent) {
            addToast(t('chat.sendFailedDisconnected'), 'error');
            dropGhost();
          }
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
        isStoppingSid,
        setStoppingSid,
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
        // 未连上：本地直接收束
        setStoppingSid(sid, false);
        if (useSessionStore.getState().currentSession?.id === sid) {
          setIsStreaming(false);
          setStreamStatusDetail(null);
          setLiveToolCalls([]);
          streamingContentRef.current = '';
          setStreamingContent('');
        }
        streamSessionApi().markIdle(sid);
        loadMessages(sid).catch(console.error);
        return;
      }
      // 兜底：8s 仍无 idle 则强制收束——仅影响发起 stop 的 sid，且仅当仍在看该会话时改 UI
      window.setTimeout(() => {
        if (!isStoppingSid(sid)) return;
        setStoppingSid(sid, false);
        streamSessionApi().markIdle(sid);
        if (useSessionStore.getState().currentSession?.id === sid) {
          setIsStreaming(false);
          setStreamStatusDetail(null);
          setLiveToolCalls([]);
          streamingContentRef.current = '';
          setStreamingContent('');
          loadMessages(sid).catch(console.error);
        }
      }, 8000);
    }, [sendStop, currentSession, loadMessages, t, setStoppingSid, isStoppingSid]);

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
        setStreamStatusDetail(cached.statusDetail || 'Resuming…');
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
    let liveContent = streamingContent;
    if (!liveContent && streamStatusDetail && liveToolCalls.length === 0) {
      liveContent = '';
    }
    return [
      ...base,
      {
        id: 'streaming',
        session_id: currentSessionId,
        role: 'assistant' as const,
        content:
          liveContent ||
          (liveToolCalls.length
            ? ''
            : streamStatusDetail
              ? `_${streamStatusDetail}_`
              : ''),
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
    streamStatusDetail,
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
                                                <ChatWindow
                          messages={displayMessages}
                          isStreaming={isStreaming}
                          onStopStreaming={handleStopStreaming}
                          onTagClick={handleTagClick}
                          onRegenerate={handleRegenerate}
                          onEdit={handleEdit}
                          onExampleSelect={(text) => setEditingContent(text)}
                          onPreviewArtifact={setPreviewArtifact}
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
                      <SessionArtifactsBar
                        messages={displayMessages}
                        onPreview={setPreviewArtifact}
                      />
                      {/* 活动流：仅流式时出现，单行 */}
                      <ActivityPanel
                        liveToolCalls={liveToolCalls}
                        streamStatusDetail={streamStatusDetail}
                        isStreaming={isStreaming}
                      />
                      {/* 统一状态条：健康 / 沙箱 / 能力 / 记录 / 工单 */}
                      <div className="relative">
                        <ChatStatusStrip
                          sessionId={currentSession?.id}
                          capsCount={runCaps?.caps}
                          toolsCount={runCaps?.tools}
                          softRenew={runCaps?.soft}
                          liveModel={liveModel}
                          zh
                        />
                      </div>
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
                      <MessageInput
                                              ref={composerRef}
                                              // audit-fix: key 带 sessionId，切会话强制 remount，配合 per-session 草稿
                                              key={`${currentSession?.id ?? 'no-session'}:${editingContent ?? 'default'}`}
                                              onSend={handleSend}
                                              onGenerateImage={handleGenerateImage}
                                              disabled={
                                                isStreaming ||
                                                isGeneratingImage ||
                                                creatingSession ||
                                                // 被踢后禁用输入，只保留横幅「夺取连接」，避免发送=无确认抢主
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
                      </>
                      ) : null}
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
