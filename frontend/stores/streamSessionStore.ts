/**
 * Per-session 流式运行态：用户乱切页面 / 切换会话时本地保留，
 * 切回后可立即恢复；再由 WS sync_response 用服务端快照校正。
 */

import { create } from 'zustand';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';

export type SessionStreamState = {
  isStreaming: boolean;
  agentRunning: boolean;
  content: string;
  tools: ToolCallData[];
  statusDetail: string | null;
  streamMessageId: string | null;
  updatedAt: number;
};

const emptyState = (): SessionStreamState => ({
  isStreaming: false,
  agentRunning: false,
  content: '',
  tools: [],
  statusDetail: null,
  streamMessageId: null,
  updatedAt: 0,
});

interface StreamSessionStore {
  bySession: Record<string, SessionStreamState>;
  get: (sessionId: string) => SessionStreamState;
  save: (sessionId: string, state: Partial<SessionStreamState> & { isStreaming?: boolean }) => void;
  patch: (sessionId: string, patch: Partial<SessionStreamState>) => void;
  clear: (sessionId: string) => void;
  markRunning: (sessionId: string, detail?: string | null) => void;
  markIdle: (sessionId: string) => void;
}

export const useStreamSessionStore = create<StreamSessionStore>((set, get) => ({
  bySession: {},

  get: (sessionId) => {
    if (!sessionId) return emptyState();
    return get().bySession[sessionId] || emptyState();
  },

  save: (sessionId, state) => {
    if (!sessionId) return;
    set((s) => {
      const prev = s.bySession[sessionId] || emptyState();
      return {
        bySession: {
          ...s.bySession,
          [sessionId]: {
            ...prev,
            ...state,
            tools: state.tools !== undefined ? state.tools : prev.tools,
            updatedAt: Date.now(),
          },
        },
      };
    });
  },

  patch: (sessionId, patch) => {
    if (!sessionId) return;
    get().save(sessionId, patch);
  },

  clear: (sessionId) => {
    if (!sessionId) return;
    set((s) => {
      const next = { ...s.bySession };
      delete next[sessionId];
      return { bySession: next };
    });
  },

  markRunning: (sessionId, detail) => {
    if (!sessionId) return;
    get().save(sessionId, {
      isStreaming: true,
      agentRunning: true,
      statusDetail: detail ?? get().get(sessionId).statusDetail,
    });
  },

  markIdle: (sessionId) => {
    if (!sessionId) return;
    get().save(sessionId, {
      isStreaming: false,
      agentRunning: false,
      content: '',
      tools: [],
      statusDetail: null,
      streamMessageId: null,
    });
  },
}));

/** 非 hook：从任意回调同步读写 */
export function streamSessionApi() {
  return useStreamSessionStore.getState();
}
