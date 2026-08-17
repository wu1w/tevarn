'use client';

/**
 * 挂在 AppShell：按 sessionStore.currentSession 维持 WS，不随 /chat 卸载而断。
 */
import { useEffect } from 'react';
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
    sendSync,
    waitForConnection,
    connect,
    reclaimConnection,
  } = useWebSocket({
    sessionId,
    token,
    onStreamDelta: (msg: StreamDeltaMessage) => {
      chatWsHandlers().onStreamDelta?.(msg);
    },
    onContentReset: (msg) => {
      chatWsHandlers().onContentReset?.(msg);
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
    onUserInputIgnored: (payload) => {
      chatWsHandlers().onUserInputIgnored?.(payload);
    },
    onSlashResult: (payload) => {
      chatWsHandlers().onSlashResult?.(payload);
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
        window.dispatchEvent(new CustomEvent('tevarn:settings-changed', { detail: keys }));
      }
    },
  });

  // 发布 API 给 chat 页；卸载时清空
  useEffect(() => {
    setApi({
      isConnected,
      isConnecting,
      kickedByPeer,
      sendMessage: sendMessage as never,
      sendStop,
      sendSync,
      waitForConnection,
      connect,
      reclaimConnection,
    });
    return () => setApi(null);
  }, [
    isConnected,
    isConnecting,
    kickedByPeer,
    sendMessage,
    sendStop,
    sendSync,
    waitForConnection,
    connect,
    reclaimConnection,
    setApi,
  ]);

  return null;
}
