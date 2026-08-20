'use client';

/**
 * 挂在 AppShell：按 sessionStore.currentSession 维持 WS，不随 /chat 卸载而断。
 */
import { useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { chatWsHandlers, useChatWsBridge } from '@/stores/chatWsBridge';
import { streamSessionApi } from '@/stores/streamSessionStore';
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
      // AppShell 常驻：即使 /chat 正在换 handler / 已卸载，也要把 idle 写入 store，
      // 否则切回会话会把残留 tools/content 当成「还在思考」。
      const sid = String(msg.session_id || sessionId || '');
      if (sid) {
        if (msg.state === 'idle' || msg.state === 'error') {
          streamSessionApi().markIdle(sid);
        } else if (
          msg.state === 'thinking' ||
          msg.state === 'tool_executing' ||
          msg.state === 'optimizing'
        ) {
          streamSessionApi().markRunning(sid, msg.detail || null);
        }
      }
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
      const ev = String(msg.event || msg.topic || '');
      const sid = String(msg.session_id || sessionId || '');
      if (
        sid &&
        (ev === 'run.completed' || ev === 'run.cancelled' || ev === 'run.failed')
      ) {
        streamSessionApi().markIdle(sid);
      }
      chatWsHandlers().onRunEvent?.(msg);
    },
    onGoalUpdate: (msg: GoalUpdateMessage) => {
      chatWsHandlers().onGoalUpdate?.(msg);
    },
    onError: (err: string) => {
      chatWsHandlers().onError?.(err);
    },
    onSessionDeleted: (sid: string) => {
      chatWsHandlers().onSessionDeleted?.(sid);
      try {
        window.dispatchEvent(
          new CustomEvent('tevarn:session-invalid', { detail: { sessionId: sid } }),
        );
      } catch {
        /* ignore */
      }
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
