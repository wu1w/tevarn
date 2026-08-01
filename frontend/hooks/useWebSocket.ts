/**
 * WebSocket Hook (Native WebSocket)
 * 支持 token 认证、断线重连、按 session 连接。
 *
 * 注意：无 session 时不应禁用输入框——应在发送时创建 session 再连 WS。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWsStore } from '@/stores/wsStore';
import { t } from '@/stores/localeStore';
import type {
  WSMessage,
  StreamDeltaMessage,
  StatusUpdateMessage,
  TaskUpdateMessage,
  MemoryUpdatedMessage,
  NotificationMessage,
  GoalUpdateMessage,
  ToolEventMessage,
  RunEventMessage,
  ScreenshotMessage,
  Notification,
} from '@/types';
import {
  isStreamDelta,
  isStatusUpdate,
  isTaskUpdate,
  isMemoryUpdated,
  isGoalUpdate,
  isToolEvent,
  isRunEvent,
  isScreenshot,
  createUserInputMessage,
  createPingMessage,
  createSyncMessage,
  createStopMessage,
} from '@/lib/ws';

/** 运行时下发的 WS 基址缓存（getRuntimeEndpoints） */
let _discoveredWsBase: string | null = null;

export function setDiscoveredWsBase(url: string | null | undefined) {
  const u = (url || '').trim().replace(/\/$/, '');
  _discoveredWsBase = u || null;
  if (typeof window !== 'undefined' && u) {
    try {
      (window as unknown as { __TAKTON_WS_URL__?: string }).__TAKTON_WS_URL__ = u;
    } catch {
      /* ignore */
    }
  }
}

/** 每次连接时解析 WS 基址 */
function resolveWsBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const { hostname, port, protocol } = window.location;
    const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost';
    const hasElectron = Boolean(
      (window as unknown as { electronAPI?: unknown }).electronAPI
    );

    const injected = (window as unknown as { __TAKTON_WS_URL__?: string }).__TAKTON_WS_URL__;
    if (injected) return injected.replace(/\/$/, '');
    if (_discoveredWsBase) return _discoveredWsBase;

    // Electron 桌面：主进程反代 /api → 真实后端，走同源 WS
    if (hasElectron) {
      try {
        const api = (window as unknown as {
          electronAPI?: { getWsUrlSync?: () => string; getBackendUrlSync?: () => string };
        }).electronAPI;
        const ws = api?.getWsUrlSync?.();
        if (ws) return ws.replace(/\/$/, '');
        const http = api?.getBackendUrlSync?.();
        if (http) return http.replace(/^http/, 'ws').replace(/\/$/, '');
      } catch {
        /* ignore */
      }
      const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
      const host = port ? `${hostname}:${port}` : hostname;
      return `${wsProto}//${host}/api`;
    }

    // 浏览器 + next dev：Next rewrites 不支持 WS upgrade
    // 优先 NEXT_PUBLIC_WS_URL / 发现结果，否则 8090（产品 dev 默认）
    if (isLocalHost && (port === '3000' || port === '3001')) {
      if (process.env.NEXT_PUBLIC_WS_URL) {
        return process.env.NEXT_PUBLIC_WS_URL.replace(/\/$/, '');
      }
      return 'ws://127.0.0.1:8090/api';
    }
  }
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname;
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      const port = window.location.port;
      if (port === '3000' || port === '3001') {
        return 'ws://127.0.0.1:8090/api';
      }
      if (port && port !== '8000' && port !== '8090') {
        const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${wsProto}//${hostname}:${port}/api`;
      }
      return 'ws://127.0.0.1:8090/api';
    }
    return `ws://${hostname}/api`;
  }
  return 'ws://127.0.0.1:8090/api';
}

const RECONNECT_DELAY_BASE = 1000;
const MAX_RECONNECT_DELAY = 30000;
/** 快重连次数上限；达到后改为慢重试，不再彻底放弃 */
const MAX_RECONNECT_ATTEMPTS = 15;
const SLOW_RECONNECT_DELAY = 60000;

interface UseWebSocketOptions {
  sessionId: string;
  token?: string | null;
  onStreamDelta?: (msg: StreamDeltaMessage) => void;
  onStatusUpdate?: (msg: StatusUpdateMessage) => void;
  onTaskUpdate?: (msg: TaskUpdateMessage) => void;
  onMemoryUpdated?: (msg: MemoryUpdatedMessage) => void;
  onGoalUpdate?: (msg: GoalUpdateMessage) => void;
  onToolEvent?: (msg: ToolEventMessage) => void;
  onRunEvent?: (msg: RunEventMessage) => void;
  onScreenshot?: (msg: ScreenshotMessage) => void;
  onNotification?: (msg: NotificationMessage) => void;
  onSettingsChanged?: (keys: string[]) => void;
  onError?: (error: string) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onSyncResponse?: (payload: {
    messages: Array<{ id: string; role: string; content: string; created_at?: string | null }>;
    agent_running?: boolean;
    state?: string;
    /** 服务端 in-flight 快照：跳页/断线后恢复正文与 tools */
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
  }) => void;
  /** 服务端落库用户消息后的 id 回执（替换乐观气泡） */
  onUserMessageAck?: (payload: {
    display_content?: string | null;
    id: string;
    role: string;
    content: string;
    created_at?: string | null;
  }) => void;
  /** 连接成功后自动 sync 时使用的 last message id */
  getLastMessageId?: () => string | undefined;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    sessionId,
    token,
    onError,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  /** 被其它 Tab 以 1001 踢下线：禁止自动重连抢主 */
  const [kickedByPeer, setKickedByPeer] = useState(false);
  const kickedByPeerRef = useRef(false);
  const wsRef = useRef<WebSocket | null>(null);
  const activeSessionRef = useRef<string>('');
  const reconnectAttempts = useRef(0);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const optionsRef = useRef(options);
  const connectingRef = useRef(false);
  const intentionalCloseRef = useRef(false);
  /** 已发 auth，等 auth_ok 再 sync（有 token 时） */
  const authOkRef = useRef(false);
  const pendingSyncAfterAuthRef = useRef(false);
  const tokenRef = useRef(token);
  const sessionIdRef = useRef(sessionId);

  useEffect(() => {
    optionsRef.current = options;
  });
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  const clearPing = () => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
  };

  const connect = useCallback((overrideSessionId?: string, opts?: { force?: boolean }) => {
    const sid = (overrideSessionId || sessionIdRef.current || '').trim();
    if (!sid) return;

    // 被踢后禁止自动抢主；仅 force（用户点「夺取连接」/切会话）才清标志并连接
    if (kickedByPeerRef.current && !opts?.force) {
      return;
    }
    if (opts?.force) {
      kickedByPeerRef.current = false;
      setKickedByPeer(false);
    }

    // 已连上同一 session
    if (
      wsRef.current?.readyState === WebSocket.OPEN &&
      activeSessionRef.current === sid
    ) {
      setIsConnected(true);
      setIsConnecting(false);
      return;
    }

    if (connectingRef.current && activeSessionRef.current === sid) {
      return;
    }

    // 手动 connect(override) 时重置计数（用户点「重连」）
    if (overrideSessionId || opts?.force) {
      reconnectAttempts.current = 0;
    }
    // 达到快重连上限后不再 return：由自动重连 effect 走慢间隔

    // 清理旧连接
    intentionalCloseRef.current = true;
    if (wsRef.current) {
      const oldWs = wsRef.current;
      wsRef.current = null;
      oldWs.onopen = null;
      oldWs.onclose = null;
      oldWs.onerror = null;
      oldWs.onmessage = null;
      try {
        if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
          oldWs.close();
        }
      } catch {
        /* ignore */
      }
    }
    clearPing();
    intentionalCloseRef.current = false;

    connectingRef.current = true;
    activeSessionRef.current = sid;
    setIsConnecting(true);
    setIsConnected(false);

    let url: string;
    try {
      url = `${resolveWsBaseUrl()}/ws/${sid}`;
    } catch {
      connectingRef.current = false;
      setIsConnecting(false);
      optionsRef.current.onError?.('Invalid WebSocket address');
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      connectingRef.current = false;
      setIsConnecting(false);
      optionsRef.current.onError?.(`WebSocket creation failed: ${e}`);
      return;
    }

    ws.onopen = () => {
      // 若期间已切换 session，丢弃
      if (activeSessionRef.current !== sid) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        return;
      }
      setIsConnected(true);
      connectingRef.current = false;
      setIsConnecting(false);
      reconnectAttempts.current = 0;
      authOkRef.current = false;
      pendingSyncAfterAuthRef.current = false;
      try { useWsStore.getState().setConnected(true); } catch (e) { console.error(e); }
      optionsRef.current.onConnect?.();

      const doSync = () => {
        try {
          const lastId = optionsRef.current.getLastMessageId?.();
          ws.send(JSON.stringify({ type: 'sync', last_message_id: lastId || undefined }));
        } catch {
          /* ignore */
        }
      };

      const tok = tokenRef.current;
      if (tok) {
        // 有 token：等 auth_ok 再 sync，避免未鉴权 sync 被丢
        pendingSyncAfterAuthRef.current = true;
        try {
          ws.send(JSON.stringify({ type: 'auth', token: tok }));
        } catch {
          /* ignore */
        }
        // 兜底：1.5s 无 auth_ok 仍 sync（兼容旧后端）
        window.setTimeout(() => {
          if (ws.readyState === WebSocket.OPEN && pendingSyncAfterAuthRef.current) {
            pendingSyncAfterAuthRef.current = false;
            doSync();
          }
        }, 1500);
      } else {
        // 无 token（loopback 单用户）：直接 sync
        doSync();
      }

      clearPing();
      pingIntervalRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try {
            ws.send(JSON.stringify(createPingMessage()));
          } catch {
            /* ignore */
          }
        }
      }, 30000);
    };

    ws.onclose = (ev: CloseEvent) => {
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
      if (activeSessionRef.current === sid) {
        setIsConnected(false);
        connectingRef.current = false;
        setIsConnecting(false);
        try { useWsStore.getState().setConnected(false); } catch (e) { console.error(e); }
        // 1001 = 其它连接顶替本 session（后端单连接踢旧）
        // 不自动重连，避免双 Tab 互抢；用户可点「夺取连接」
        const kicked =
          !intentionalCloseRef.current &&
          (ev?.code === 1001 ||
            /new connection|replaced|another/i.test(String(ev?.reason || '')));
        if (kicked) {
          kickedByPeerRef.current = true;
          setKickedByPeer(true);
          optionsRef.current.onError?.(
            t('chat.wsKickedByPeer') ||
              '此会话已在其它窗口连接（已停止自动重连，避免互抢）',
          );
        } else if (!intentionalCloseRef.current) {
          optionsRef.current.onDisconnect?.();
        }
      }
      clearPing();
    };

    ws.onerror = () => {
      connectingRef.current = false;
      setIsConnecting(false);
      optionsRef.current.onError?.('WebSocket connection error');
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        // 关键：旧 socket 晚到消息不得写入当前会话 UI / stream 缓存
        if (activeSessionRef.current !== sid || wsRef.current !== ws) {
          return;
        }
        const msg: WSMessage = JSON.parse(event.data);
        // 部分消息体可能带 session_id，二次校验
        const msgSid = (msg as { session_id?: string }).session_id;
        if (msgSid && String(msgSid) !== sid) {
          return;
        }

        if (isStreamDelta(msg)) {
          optionsRef.current.onStreamDelta?.(msg);
        } else if (isStatusUpdate(msg)) {
          optionsRef.current.onStatusUpdate?.(msg);
        } else if (isTaskUpdate(msg)) {
          optionsRef.current.onTaskUpdate?.(msg);
        } else if (isToolEvent(msg)) {
          optionsRef.current.onToolEvent?.(msg);
        } else if (isRunEvent(msg)) {
          optionsRef.current.onRunEvent?.(msg);
        } else if (isScreenshot(msg)) {
          optionsRef.current.onScreenshot?.(msg);
        } else if (isMemoryUpdated(msg)) {
          optionsRef.current.onMemoryUpdated?.(msg);
        } else if (isGoalUpdate(msg)) {
          optionsRef.current.onGoalUpdate?.(msg);
        } else if (msg.type === 'notification') {
          const notif = msg as NotificationMessage;
          try {
            import('@/stores/notificationStore').then((mod) => {
              mod.useNotificationStore.getState().addNotification({
                id: notif.id || crypto.randomUUID(),
                user_id: notif.user_id || '',
                type: notif.notification_type || 'info',
                title: notif.title || t('nav.notifications'),
                content: notif.message || notif.content || '',
                is_read: false,
                read_at: null,
                source_id: null,
                created_at: notif.created_at || new Date().toISOString(),
                updated_at: notif.created_at || new Date().toISOString(),
                link: notif.link ?? null,
                data: notif.data ?? null,
              } as Notification);
            });
            // 编制完成等：domain 可能已 toast；WS 路径再补一条（标题短，防静默）
            // workforce 由 DomainEventBridge job.* 负责；其它类型这里 toast
            const src = String((notif.data as { source?: string } | undefined)?.source || '');
            if (src !== 'workforce_dispatcher') {
              import('@/stores/toastStore').then((mod) => {
                mod.useToastStore.getState().addToast(
                  notif.title || notif.message || t('nav.notifications'),
                  notif.notification_type === 'task_failed' ? 'error' : 'info',
                );
              }).catch(() => {});
            }
          } catch (e) { console.error(e); }
          optionsRef.current.onNotification?.(notif);
        } else if (msg.type === 'settings_changed') {
          const keys = (msg as unknown as { keys?: string[] }).keys || [];
          optionsRef.current.onSettingsChanged?.(keys);
        } else if (msg.type === 'auth_ok') {
          authOkRef.current = true;
          if (pendingSyncAfterAuthRef.current) {
            pendingSyncAfterAuthRef.current = false;
            try {
              const lastId = optionsRef.current.getLastMessageId?.();
              wsRef.current?.send(
                JSON.stringify({ type: 'sync', last_message_id: lastId || undefined })
              );
            } catch {
              /* ignore */
            }
          }
        } else if (msg.type === 'user_message_ack') {
          const m = msg as unknown as {
            id?: string;
            role?: string;
            content?: string;
            display_content?: string | null;
            created_at?: string | null;
          };
          if (m.id) {
            optionsRef.current.onUserMessageAck?.({
              id: m.id,
              role: m.role || 'user',
              content: m.content || '',
              display_content: m.display_content,
              created_at: m.created_at,
            });
          }
        } else if (msg.type === 'sync_response') {
          const m = msg as unknown as {
            messages?: Array<{ id: string; role: string; content: string; created_at?: string | null }>;
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
          };
          optionsRef.current.onSyncResponse?.({
            messages: m.messages || [],
            agent_running: Boolean(m.agent_running),
            state: m.state,
            partial_content: m.partial_content,
            stream_status: m.stream_status,
            stream_message_id: m.stream_message_id,
            live_tools: m.live_tools,
          });
        } else if (msg.type === 'error') {
          optionsRef.current.onError?.(
            (msg as unknown as { detail: string }).detail || 'Unknown error'
          );
        } else if (msg.type === 'confirm_request') {
          // 危险操作确认请求 → 写入 store，触发前端弹窗（支持 once/session/agent）
          const m = msg as import('@/types').ConfirmRequestMessage;
          import('@/stores/confirmStore').then((mod) => {
            mod.useConfirmStore.getState().showConfirm({
              confirmId: m.confirm_id,
              title: m.title || t('useWebSocket._e2'),
              command: m.command || '',
              reason: m.reason || '',
              tool: m.tool,
              agentId: m.agent_id || undefined,
              agentName: m.agent_name || undefined,
              timeout: m.timeout ?? 120,
              sessionId: m.session_id || sid,
            });
          });
        } else if (msg.type === 'confirm_expired') {
          const m = msg as unknown as { confirm_id?: string; reason?: string };
          if (m.confirm_id) {
            import('@/stores/confirmStore').then((mod) => {
              mod.useConfirmStore
                .getState()
                .expireConfirm(m.confirm_id!, m.reason || 'timeout');
            });
          }
        }
      } catch (err) {
        console.error('WebSocket message parse error:', err);
      }
    };

    wsRef.current = ws;

    // 注册危险操作确认的发送函数：弹窗组件经 store 调用（含 scope）
    // 返回 false → store 走 HTTP 兜底，避免 WS 断开时确认被吞
    import('@/stores/confirmStore').then((mod) => {
      mod.useConfirmStore.getState().registerSender((confirmId, approved, scope) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          try {
            wsRef.current.send(
              JSON.stringify({
                type: 'confirm_response',
                confirm_id: confirmId,
                approved,
                scope: approved ? scope : 'deny',
              }),
            );
            return true;
          } catch {
            return false;
          }
        }
        return false;
      });
    });
  }, []);

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    clearPing();
    if (wsRef.current) {
      const ws = wsRef.current;
      ws.onopen = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.onmessage = null;
      try {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    connectingRef.current = false;
    activeSessionRef.current = '';
    setIsConnected(false);
    setIsConnecting(false);
    intentionalCloseRef.current = false;
  }, []);

  /** 等待指定 session 的 WS 就绪（创建会话后发送前调用） */
  const waitForConnection = useCallback(
    (targetSessionId?: string, timeoutMs = 15000): Promise<boolean> => {
      const sid = (targetSessionId || sessionIdRef.current || '').trim();
      if (!sid) return Promise.resolve(false);

      if (
        wsRef.current?.readyState === WebSocket.OPEN &&
        activeSessionRef.current === sid
      ) {
        return Promise.resolve(true);
      }

      // 用户主动发送：允许夺取连接（区别于后台自动重连抢主）
      reconnectAttempts.current = 0;
      connect(sid, { force: true });

      return new Promise((resolve) => {
        const start = Date.now();
        const tick = () => {
          if (
            wsRef.current?.readyState === WebSocket.OPEN &&
            activeSessionRef.current === sid
          ) {
            resolve(true);
            return;
          }
          if (Date.now() - start >= timeoutMs) {
            resolve(false);
            return;
          }
          // 若连接失败卡住，周期性再试
          if (
            !connectingRef.current &&
            wsRef.current?.readyState !== WebSocket.CONNECTING &&
            wsRef.current?.readyState !== WebSocket.OPEN
          ) {
            reconnectAttempts.current = Math.min(
              reconnectAttempts.current + 1,
              MAX_RECONNECT_ATTEMPTS - 1
            );
            connect(sid, { force: true });
          }
          setTimeout(tick, 120);
        };
        setTimeout(tick, 50);
      });
    },
    [connect]
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
      opts?: { regenerate?: boolean }
    ) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        onError?.('WebSocket not connected');
        return false;
      }
      wsRef.current.send(
        JSON.stringify(
          createUserInputMessage(content, attachments, mode, subAgentIds, opts)
        )
      );
      return true;
    },
    [onError]
  );

  const sendSync = useCallback((lastMessageId?: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify(createSyncMessage(lastMessageId)));
    return true;
  }, []);

  const sendStop = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify(createStopMessage()));
    return true;
  }, []);

  // sessionId 变化时连接 / 断开
  useEffect(() => {
    // 连接/断开均推迟到当前渲染周期之后（setTimeout 0），
    // 避免 effect 同步路径 setState 触发跨组件级联更新告警。
    const timer = setTimeout(() => {
      if (!sessionId) {
        disconnect();
        return;
      }
      // 切会话：清被踢状态，允许新会话连接
      kickedByPeerRef.current = false;
      setKickedByPeer(false);
      reconnectAttempts.current = 0;
      connect(sessionId, { force: true });
    }, 0);
    return () => {
      clearTimeout(timer);
      // 仅在 session 真正卸载时不断开过早——由下一次 effect 处理
    };
  }, [sessionId, connect, disconnect]);

  // 自动重连（仅在有 session 且意外断开时；被踢不抢主）
  useEffect(() => {
    if (!sessionId) return;
    if (isConnected || isConnecting) return;
    if (kickedByPeer || kickedByPeerRef.current) return;

    const fastExhausted = reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS;
    const delay = fastExhausted
      ? SLOW_RECONNECT_DELAY
      : Math.min(
          RECONNECT_DELAY_BASE * Math.pow(2, Math.min(reconnectAttempts.current, 5)),
          MAX_RECONNECT_DELAY
        );

    if (fastExhausted && reconnectAttempts.current === MAX_RECONNECT_ATTEMPTS) {
      // 仅提示一次，之后慢重试
      optionsRef.current.onError?.(
        'WebSocket fast reconnect paused — slow retry every 60s, or click reconnect'
      );
    }

    const timer = setTimeout(() => {
      if (!sessionIdRef.current) return;
      if (kickedByPeerRef.current) return;
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      reconnectAttempts.current += 1;
      connect(sessionIdRef.current);
    }, delay);

    return () => clearTimeout(timer);
  }, [isConnected, isConnecting, sessionId, connect, kickedByPeer]);

  const reclaimConnection = useCallback(() => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    kickedByPeerRef.current = false;
    setKickedByPeer(false);
    reconnectAttempts.current = 0;
    connect(sid, { force: true });
  }, [connect]);

  // 登出 / token 清空：强制断连
  useEffect(() => {
    if (!token) {
      disconnect();
    }
  }, [token, disconnect]);

  // 组件卸载：仅在无 session 时断连（AppShell 常驻时切页不断）
  useEffect(() => {
    return () => {
      if (sessionIdRef.current) return;
      disconnect();
    };
  }, [disconnect]);

  return {
    isConnected,
    isConnecting,
    kickedByPeer,
    reclaimConnection,
    connect,
    disconnect,
    waitForConnection,
    sendMessage,
    sendSync,
    sendStop,
  };
}
