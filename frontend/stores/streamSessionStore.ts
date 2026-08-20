/**
 * Per-session 流式运行态：用户乱切页面 / 切换会话时本地保留，
 * 切回后可立即恢复；再由 WS sync_response 用服务端快照校正。
 *
 * 有上限：idle 条目优先淘汰，避免长时多会话 bySession 无界涨内存。
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

/** 同时保留的会话流状态上限（含运行中） */
// 运行中优先保留；idle 淘汰。略提高上限降低「切回久未动会话丢缓存」体感（P3-9）
const MAX_SESSION_STREAM_ENTRIES = 64;

const emptyState = (): SessionStreamState => ({
  isStreaming: false,
  agentRunning: false,
  content: '',
  tools: [],
  statusDetail: null,
  streamMessageId: null,
  updatedAt: 0,
});

function pruneBySession(
  map: Record<string, SessionStreamState>
): Record<string, SessionStreamState> {
  const keys = Object.keys(map);
  if (keys.length <= MAX_SESSION_STREAM_ENTRIES) return map;
  const entries = keys.map((k) => ({ k, s: map[k] }));
  // idle 优先丢；同级按 updatedAt 最旧优先
  entries.sort((a, b) => {
    const aActive = a.s.isStreaming || a.s.agentRunning ? 1 : 0;
    const bActive = b.s.isStreaming || b.s.agentRunning ? 1 : 0;
    if (aActive !== bActive) return aActive - bActive;
    return (a.s.updatedAt || 0) - (b.s.updatedAt || 0);
  });
  const next = { ...map };
  const drop = entries.length - MAX_SESSION_STREAM_ENTRIES;
  for (let i = 0; i < drop; i++) {
    delete next[entries[i].k];
  }
  return next;
}

interface StreamSessionStore {
  bySession: Record<string, SessionStreamState>;
  get: (sessionId: string) => SessionStreamState;
  save: (sessionId: string, state: Partial<SessionStreamState> & { isStreaming?: boolean }) => void;
  patch: (sessionId: string, patch: Partial<SessionStreamState>) => void;
  clear: (sessionId: string) => void;
  markRunning: (sessionId: string, detail?: string | null) => void;
  markIdle: (sessionId: string) => void;
}

/** Only the running flags mean the agent is live. Leftover content/tools must not resume thinking. */
export function isActiveStream(s: SessionStreamState | null | undefined): boolean {
  return Boolean(s && (s.agentRunning || s.isStreaming));
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
      const nextMap = {
        ...s.bySession,
        [sessionId]: {
          ...prev,
          ...state,
          tools: state.tools !== undefined ? state.tools : prev.tools,
          updatedAt: Date.now(),
        },
      };
      return { bySession: pruneBySession(nextMap) };
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
    // 保留 key 便于切回瞬间恢复空闲态；靠 prune 上限控制内存
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
