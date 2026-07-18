/**
 * WebSocket Hook (Native WebSocket)
 * 支持 token 认证、断线重连、按 session 连接。
 *
 * 注意：无 session 时不应禁用输入框——应在发送时创建 session 再连 WS。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWsStore } from '@/stores/wsStore';
import type {
  WSMessage,
  StreamDeltaMessage,
  StatusUpdateMessage,
  TaskUpdateMessage,
  MemoryUpdatedMessage,
  NotificationMessage,
  GoalUpdateMessage,
  ToolEventMessage,
  Notification,
} from '@/types';
import {
  isStreamDelta,
  isStatusUpdate,
  isTaskUpdate,
  isMemoryUpdated,
  isGoalUpdate,
  isToolEvent,
  createUserInputMessage,
  createPingMessage,
  createSyncMessage,
  createStopMessage,
} from '@/lib/ws';

/** 每次连接时解析 WS 基址 */
function resolveWsBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const { hostname, port, protocol } = window.location;
    const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost';

    // 桌面端 / 本地静态服：优先同源 WS（主进程会把 /api 反代到真实后端端口）
    // 这样不会死连默认 8000，也不会因为后端换到 8001 而一直「正在连接」
    if (isLocalHost && (port === '3000' || port === '3001' || port === '')) {
      const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
      const host = port ? `${hostname}:${port}` : hostname;
      return `${wsProto}//${host}/api`;
    }

    const injected = (window as unknown as { __TAKTON_WS_URL__?: string }).__TAKTON_WS_URL__;
    if (injected) return injected.replace(/\/$/, '');

    if ((window as unknown as { electronAPI?: unknown }).electronAPI) {
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
      // 桌面回退仍走同源（由主进程反代），不要写死 8000
      return 'ws://127.0.0.1:3000/api';
    }
  }
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname;
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      // 浏览器开发模式：Next rewrites 不支持 WS upgrade 代理，
      // 直连后端 8090（同源 :3000 会因 upgrade 失败而断开）。
      const port = window.location.port;
      if (port === '3000') {
        return 'ws://127.0.0.1:8090/api';
      }
      if (port && port !== '8000') {
        const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${wsProto}//${hostname}:${port}/api`;
      }
      return 'ws://localhost:8000/api';
    }
    return `ws://${hostname}:8000/api`;
  }
  return 'ws://localhost:8000/api';
}

const RECONNECT_DELAY_BASE = 1000;
const MAX_RECONNECT_DELAY = 30000;
const MAX_RECONNECT_ATTEMPTS = 15;

interface UseWebSocketOptions {
  sessionId: string;
  token?: string | null;
  onStreamDelta?: (msg: StreamDeltaMessage) => void;
  onStatusUpdate?: (msg: StatusUpdateMessage) => void;
  onTaskUpdate?: (msg: TaskUpdateMessage) => void;
  onMemoryUpdated?: (msg: MemoryUpdatedMessage) => void;
  onGoalUpdate?: (msg: GoalUpdateMessage) => void;
  onToolEvent?: (msg: ToolEventMessage) => void;
  onNotification?: (msg: NotificationMessage) => void;
  onSettingsChanged?: (keys: string[]) => void;
  onError?: (error: string) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    sessionId,
    token,
    onStreamDelta,
    onStatusUpdate,
    onTaskUpdate,
    onMemoryUpdated,
    onGoalUpdate,
    onToolEvent,
    onNotification,
    onSettingsChanged,
    onError,
    onConnect,
    onDisconnect,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const activeSessionRef = useRef<string>('');
  const reconnectAttempts = useRef(0);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const optionsRef = useRef(options);
  const connectingRef = useRef(false);
  const intentionalCloseRef = useRef(false);
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

  const connect = useCallback((overrideSessionId?: string) => {
    const sid = (overrideSessionId || sessionIdRef.current || '').trim();
    if (!sid) return;

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

    if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
      optionsRef.current.onError?.('WebSocket 重连次数已达上限，请刷新页面或点击重连');
      return;
    }

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
      optionsRef.current.onError?.('无效的 WebSocket 地址');
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      connectingRef.current = false;
      setIsConnecting(false);
      optionsRef.current.onError?.(`WebSocket 创建失败: ${e}`);
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
      try { useWsStore.getState().setConnected(true); } catch (e) { console.error(e); }
      optionsRef.current.onConnect?.();

      const t = tokenRef.current;
      if (t) {
        try {
          ws.send(JSON.stringify({ type: 'auth', token: t }));
        } catch {
          /* ignore */
        }
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

    ws.onclose = () => {
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
      if (activeSessionRef.current === sid) {
        setIsConnected(false);
        connectingRef.current = false;
        setIsConnecting(false);
        try { useWsStore.getState().setConnected(false); } catch (e) { console.error(e); }
        if (!intentionalCloseRef.current) {
          optionsRef.current.onDisconnect?.();
        }
      }
      clearPing();
    };

    ws.onerror = () => {
      connectingRef.current = false;
      setIsConnecting(false);
      optionsRef.current.onError?.('WebSocket 连接错误');
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);

        if (isStreamDelta(msg)) {
          optionsRef.current.onStreamDelta?.(msg);
        } else if (isStatusUpdate(msg)) {
          optionsRef.current.onStatusUpdate?.(msg);
        } else if (isTaskUpdate(msg)) {
          optionsRef.current.onTaskUpdate?.(msg);
        } else if (isToolEvent(msg)) {
          optionsRef.current.onToolEvent?.(msg);
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
                title: notif.title || '通知',
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
          } catch (e) { console.error(e); }
          optionsRef.current.onNotification?.(notif);
        } else if (msg.type === 'settings_changed') {
          const keys = (msg as unknown as { keys?: string[] }).keys || [];
          optionsRef.current.onSettingsChanged?.(keys);
        } else if (msg.type === 'error') {
          optionsRef.current.onError?.(
            (msg as unknown as { detail: string }).detail || 'Unknown error'
          );
        }
      } catch (err) {
        console.error('WebSocket message parse error:', err);
      }
    };

    wsRef.current = ws;
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

      // 重置重连上限，给发送一次机会
      reconnectAttempts.current = 0;
      connect(sid);

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
            connect(sid);
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
      subAgentIds?: string[]
    ) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        onError?.('WebSocket not connected');
        return false;
      }
      wsRef.current.send(
        JSON.stringify(createUserInputMessage(content, attachments, mode, subAgentIds))
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
      reconnectAttempts.current = 0;
      connect(sessionId);
    }, 0);
    return () => {
      clearTimeout(timer);
      // 仅在 session 真正卸载时不断开过早——由下一次 effect 处理
    };
  }, [sessionId, connect, disconnect]);

  // 自动重连（仅在有 session 且意外断开时）
  useEffect(() => {
    if (!sessionId) return;
    if (isConnected || isConnecting) return;

    const delay = Math.min(
      RECONNECT_DELAY_BASE * Math.pow(2, Math.min(reconnectAttempts.current, 5)),
      MAX_RECONNECT_DELAY
    );

    const timer = setTimeout(() => {
      if (!sessionIdRef.current) return;
      if (wsRef.current?.readyState === WebSocket.OPEN) return;
      reconnectAttempts.current += 1;
      connect(sessionIdRef.current);
    }, delay);

    return () => clearTimeout(timer);
  }, [isConnected, isConnecting, sessionId, connect]);

  // 组件卸载清理
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return {
    isConnected,
    isConnecting,
    connect,
    disconnect,
    waitForConnection,
    sendMessage,
    sendSync,
    sendStop,
  };
}
