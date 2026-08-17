/**
 * AppShell 级 chat WS 桥：连接常驻，页面只注册/注销 handlers。
 * 切到 settings/agents 等不卸载连接，避免 stop/confirm/sync 丢失。
 */
import { create } from 'zustand';
import type {
  StreamDeltaMessage,
  StatusUpdateMessage,
  GoalUpdateMessage,
  ToolEventMessage,
  RunEventMessage,
} from '@/types';
// ContentReset handled via handlers

export type ChatWsHandlers = {
  onStreamDelta?: (msg: StreamDeltaMessage) => void;
  onContentReset?: (msg: import('@/types').ContentResetMessage) => void;
  onStatusUpdate?: (msg: StatusUpdateMessage) => void;
  onSyncResponse?: (payload: {
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
  }) => void;
  onUserMessageAck?: (payload: {
    id: string;
    role: string;
    content: string;
    created_at?: string | null;
    display_content?: string | null;
  }) => void;
  onUserInputIgnored?: (payload: {
    reason?: string;
    detail?: string;
    agent_running?: boolean;
  }) => void;
  onSlashResult?: (payload: {
    command?: string;
    reply?: string;
    message_id?: string;
    user_message_id?: string;
    new_session_id?: string;
  }) => void;
  onToolEvent?: (msg: ToolEventMessage) => void;
  onRunEvent?: (msg: RunEventMessage) => void;
  onGoalUpdate?: (msg: GoalUpdateMessage) => void;
  onError?: (err: string) => void;
  getLastMessageId?: () => string | undefined;
};

type Api = {
  isConnected: boolean;
  isConnecting: boolean;
  kickedByPeer: boolean;
  sendMessage: (
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
  ) => boolean;
  sendStop: () => boolean;
  sendSync: (lastMessageId?: string) => boolean;
  waitForConnection: (sessionId?: string, timeoutMs?: number) => Promise<boolean>;
  connect: (sessionId?: string, opts?: { force?: boolean }) => void;
  reclaimConnection: () => void;
};

type State = {
  handlers: ChatWsHandlers;
  setHandlers: (h: ChatWsHandlers | null) => void;
  api: Api | null;
  setApi: (api: Api | null) => void;
};

export const useChatWsBridge = create<State>((set) => ({
  handlers: {},
  setHandlers: (h) => set({ handlers: h || {} }),
  api: null,
  setApi: (api) => set({ api }),
}));

/** 非 hook：从任意回调读当前 handlers */
export function chatWsHandlers(): ChatWsHandlers {
  return useChatWsBridge.getState().handlers;
}

export function chatWsApi(): Api | null {
  return useChatWsBridge.getState().api;
}
