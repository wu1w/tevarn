'use client';

/**
 * 挂在 AppShell：按 sessionStore.currentSession 维持 WS，不随 /chat 卸载而断。
 */
import { useEffect, useRef } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { chatWsHandlers, useChatWsBridge } from '@/stores/chatWsBridge';
import type {
  StreamDeltaMessage,
  StatusUpdateMessage,
  GoalUpdateMessage,
  ToolEventMessage,
  RunEventMessage,
} from '@/types';

export function GlobalChatWs() {
  const token = useAuthStore((s) => s.token);
  const sessionId = useSessionStore((s) => s.currentSession?.id || '');
  const setApi = useChatWsBridge((s) => s.setApi);

  const {
    isConnected,
    isConnecting,
    kickedByPeer,
    sendMessage,
    sendStop,
    waitForConnection,
    connect,
    reclaimConnection,
  } = useWebSocket({
    sessionId,
    token,
    onStreamDelta: (msg: StreamDeltaMessage) => {
      chatWsHandlers().onStreamDelta?.(msg);
    },
    onStatusUpdate: (msg: StatusUpdateMessage) => {
      chatWsHandlers().onStatusUpdate?.(msg);
    },
    onSyncResponse: (payload) => {
      chatWsHandlers().onSyncResponse?.(payload);
    },
    onUserMessageAck: (payload) => {
      chatWsHandlers().onUserMessageAck?.(payload);
    },
    onToolEvent: (msg: ToolEventMessage) => {
      chatWsHandlers().onToolEvent?.(msg);
    },
    onRunEvent: (msg: RunEventMessage) => {
      chatWsHandlers().onRunEvent?.(msg);
    },
    onGoalUpdate: (msg: GoalUpdateMessage) => {
      chatWsHandlers().onGoalUpdate?.(msg);
    },
    onError: (err: string) => {
      chatWsHandlers().onError?.(err);
    },
    getLastMessageId: () => chatWsHandlers().getLastMessageId?.(),
    onSettingsChanged: (keys) => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('takton:settings-changed', { detail: keys }));
      }
    },
  });

  // 发布 API 给 chat 页；卸载时清空
  const apiRef = useRef({
    isConnected,
    isConnecting,
    kickedByPeer,
    sendMessage,
    sendStop,
    waitForConnection,
    connect,
    reclaimConnection,
  });
  apiRef.current = {
    isConnected,
    isConnecting,
    kickedByPeer,
    sendMessage,
    sendStop,
    waitForConnection,
    connect,
    reclaimConnection,
  };

  useEffect(() => {
    setApi({
      get isConnected() {
        return apiRef.current.isConnected;
      },
      get isConnecting() {
        return apiRef.current.isConnecting;
      },
      get kickedByPeer() {
        return apiRef.current.kickedByPeer;
      },
      sendMessage: (content, attachments, mode, subAgentIds, opts) =>
        apiRef.current.sendMessage(content, attachments as never, mode, subAgentIds, opts as never),
      sendStop: () => apiRef.current.sendStop(),
      waitForConnection: (sessionId, timeoutMs) =>
        apiRef.current.waitForConnection(sessionId, timeoutMs),
      connect: (sessionId, opts) => apiRef.current.connect(sessionId, opts),
      reclaimConnection: () => apiRef.current.reclaimConnection(),
    });
    return () => setApi(null);
  }, [setApi]);

  // 同步连接态到 bridge（触发订阅方重渲染）
  useEffect(() => {
    setApi({
      isConnected,
      isConnecting,
      kickedByPeer,
      sendMessage: sendMessage as never,
      sendStop,
      waitForConnection,
      connect,
      reclaimConnection,
    });
  }, [
    isConnected,
    isConnecting,
    kickedByPeer,
    sendMessage,
    sendStop,
    waitForConnection,
    connect,
    reclaimConnection,
    setApi,
  ]);

  return null;
}
