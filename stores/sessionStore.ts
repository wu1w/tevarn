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
          const title = content.slice(0, 30).replace(/\n/g, ' ');
          set((st) => ({
            messages: [...st.messages, message],
            sessionTitles: {
              ...st.sessionTitles,
              [sessionId]: title + (content.length > 30 ? '...' : ''),
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

      setMessages: (messages) => set({ messages }),

      loadSession: async (sessionId) => {
        set({ isLoading: true, error: null });
        try {
          const session = await api.getSession(sessionId);
          set({ currentSession: session, isLoading: false });
        } catch (err) {
          set({ error: (err as Error).message, isLoading: false });
        }
      },

      loadMessages: async (sessionId) => {
        set({ isLoading: true, error: null });
        try {
          const messages = await api.getMessages(sessionId);
          set({ messages, isLoading: false });
        } catch (err) {
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
        return get().sessionTitles[sessionId] || sessionId.slice(0, 8);
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
      onRehydrateStorage: () => (state) => {
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
