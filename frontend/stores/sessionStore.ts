/**
 * Session 状态管理 (Zustand)
 * 持久化 currentSession + sessionTitles + starredSessionIds
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { Message, Session, SessionConfig } from '@/types';
import * as api from '@/lib/api';

interface SessionState {
  currentSession: Session | null;
  messages: Message[];
  isLoading: boolean;
  error: string | null;
  // 客户端自管理的 session 标题（不依赖后端）
  sessionTitles: Record<string, string>;
  // 星标会话 ID 列表
  starredSessionIds: string[];

  // Actions
  setCurrentSession: (session: Session | null) => void;
  addMessage: (message: Message) => void;
  updateMessage: (id: string, updates: Partial<Message>) => void;
  /** 用服务端 id 替换乐观用户消息（同 role+content 合并，避免双气泡） */
  reconcileMessage: (serverMsg: Message) => void;
  setMessages: (messages: Message[]) => void;
  loadSession: (sessionId: string) => Promise<void>;
  loadMessages: (sessionId: string) => Promise<void>;
  updateConfig: (sessionId: string, config: SessionConfig) => Promise<void>;
  clearMessages: () => void;
  setError: (error: string | null) => void;

  // Session 标题
  setSessionTitle: (sessionId: string, title: string) => void;
  getSessionTitle: (sessionId: string) => string;

  // 星标
  toggleStarredSession: (sessionId: string) => void;
  isSessionStarred: (sessionId: string) => boolean;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      currentSession: null,
      messages: [],
      isLoading: false,
      error: null,
      sessionTitles: {},
      starredSessionIds: [],

      setCurrentSession: (session) => set({ currentSession: session }),

      addMessage: (message) => {
        const state = get();
        const sessionId = message.session_id;

        // 自动命名：用户的第一条消息自动生成 session 标题
        const content = message.content || '';
        if (
          message.role === 'user' &&
          content &&
          sessionId &&
          !state.sessionTitles[sessionId]
        ) {
          const title = content.slice(0, 36).replace(/\n/g, ' ').replace(/\s+/g, ' ').trim();
          set((st) => ({
            messages: [...st.messages, message],
            sessionTitles: {
              ...st.sessionTitles,
              [sessionId]: title + (content.trim().length > 36 ? '…' : ''),
            },
          }));
        } else {
          set((st) => ({ messages: [...st.messages, message] }));
        }
      },

      updateMessage: (id, updates) =>
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, ...updates } : m
          ),
        })),

      reconcileMessage: (serverMsg) => {
        set((state) => {
          const haveById = state.messages.some((m) => m.id === serverMsg.id);
          if (haveById) return state;

          const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
          const serverContent = norm(serverMsg.content || '');
          const serverTs = Date.parse(String(serverMsg.created_at || '')) || Date.now();
          // 时间窗：仅合并 ±2 分钟内的乐观气泡，降低连发相同文案错合
          const WINDOW_MS = 120_000;
          const optIdx = state.messages.findIndex((m) => {
            if (m.role !== serverMsg.role) return false;
            const id = String(m.id || '');
            const isOptimistic =
              id.startsWith('optimistic:') ||
              id.startsWith('local:') ||
              id.startsWith('streaming');
            if (!isOptimistic && m.role !== 'user') return false;
            if (norm(m.content || '') !== serverContent || !serverContent) return false;
            const localTs = Date.parse(String(m.created_at || '')) || 0;
            if (localTs && Math.abs(localTs - serverTs) > WINDOW_MS) return false;
            return true;
          });

          if (optIdx >= 0) {
            const next = [...state.messages];
            next[optIdx] = { ...next[optIdx], ...serverMsg, id: serverMsg.id };
            return { messages: next };
          }
          return { messages: [...state.messages, serverMsg] };
        });
      },

      setMessages: (messages) => set({ messages }),

      loadSession: async (sessionId) => {
        set({ isLoading: true, error: null });
        try {
          const session = await api.getSession(sessionId);
          set({ currentSession: session, isLoading: false });
        } catch (err) {
          const status = (err as { response?: { status?: number } })?.response?.status;
          // 本地持久化了已删/换库的 session id → 清掉，别反复 404
          if (status === 404 && get().currentSession?.id === sessionId) {
            set({ currentSession: null, messages: [], isLoading: false, error: null });
            try {
              window.dispatchEvent(
                new CustomEvent('takton:session-invalid', {
                  detail: { sessionId },
                })
              );
            } catch {
              /* ignore */
            }
            return;
          }
          set({ error: (err as Error).message, isLoading: false });
        }
      },

      loadMessages: async (sessionId) => {
        set({ isLoading: true, error: null });
        try {
          // 默认拉最近 200 条（后端 offset=0 时为尾部窗口）
          const messages = await api.getMessages(sessionId, 200, 0);
          const st = get();
          // 保留尚未 ack 的乐观用户气泡（服务端尚未返回时）
          const optimistic = (st.messages || []).filter((m) =>
            String(m.id || '').startsWith('optimistic:')
          );
          let merged = Array.isArray(messages) ? [...messages] : [];
          for (const o of optimistic) {
            const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
            const oc = norm(o.content || '');
            if (!merged.some((m) => m.role === o.role && norm(m.content || '') === oc)) {
              merged = [...merged, o];
            }
          }
          // 加载历史后补标题：取首条用户消息
          if (!st.sessionTitles[sessionId] && merged.length) {
            const firstUser = merged.find(
              (m) => m.role === 'user' && (m.content || '').trim()
            );
            if (firstUser?.content) {
              const raw = firstUser.content.trim().replace(/\s+/g, ' ');
              const title = raw.slice(0, 36) + (raw.length > 36 ? '…' : '');
              set({
                messages: merged,
                isLoading: false,
                sessionTitles: { ...st.sessionTitles, [sessionId]: title },
              });
              return;
            }
          }
          set({ messages: merged, isLoading: false });
        } catch (err) {
          const status = (err as { response?: { status?: number } })?.response?.status;
          if (status === 404 && get().currentSession?.id === sessionId) {
            set({ currentSession: null, messages: [], isLoading: false, error: null });
            try {
              window.dispatchEvent(
                new CustomEvent('takton:session-invalid', {
                  detail: { sessionId },
                })
              );
            } catch {
              /* ignore */
            }
            return;
          }
          set({ error: (err as Error).message, isLoading: false });
        }
      },

      updateConfig: async (sessionId, config) => {
        set({ isLoading: true, error: null });
        try {
          const session = await api.updateSessionConfig(sessionId, config);
          set({ currentSession: session, isLoading: false });
        } catch (err) {
          set({ error: (err as Error).message, isLoading: false });
        }
      },

      clearMessages: () => set({ messages: [] }),

      setError: (error) => set({ error }),

      // Session 标题
      setSessionTitle: (sessionId, title) =>
        set((state) => ({
          sessionTitles: { ...state.sessionTitles, [sessionId]: title },
        })),

      getSessionTitle: (sessionId) => {
        const t = get().sessionTitles[sessionId];
        if (t && t.trim()) return t;
        // 无首条用户消息时不返回硬编码英文——调用方用日期 fallback 更可读
        return '';
      },

      // 星标
      toggleStarredSession: (sessionId) =>
        set((state) => {
          const exists = state.starredSessionIds.includes(sessionId);
          return {
            starredSessionIds: exists
              ? state.starredSessionIds.filter((id) => id !== sessionId)
              : [...state.starredSessionIds, sessionId],
          };
        }),

      isSessionStarred: (sessionId) => {
        return get().starredSessionIds.includes(sessionId);
      },
    }),
    {
      name: 'takton-session',
      partialize: (state) => ({
        currentSession: state.currentSession,
        sessionTitles: state.sessionTitles,
        starredSessionIds: state.starredSessionIds,
      }),
      onRehydrateStorage: () => () => {
        if (typeof window === 'undefined') return;
        window.addEventListener('storage', (e) => {
          if (e.key === 'takton-session' && e.newValue) {
            try {
              const parsed = JSON.parse(e.newValue);
              if (parsed?.state) {
                window.dispatchEvent(new CustomEvent('takton:session-sync', { detail: parsed.state }));
              }
            } catch (err) {
              console.error('session sync parse failed:', err);
            }
          }
        });
      },
    }
  )
);
