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
  /** 移除消息（发送失败清幽灵乐观气泡） */
  removeMessage: (id: string) => void;
  /** 用服务端 id 替换乐观用户消息（同 role+content 合并，避免双气泡） */
  reconcileMessage: (serverMsg: Message) => void;
  setMessages: (messages: Message[]) => void;
  loadSession: (sessionId: string) => Promise<void>;
  loadMessages: (sessionId: string) => Promise<void>;
  /** 递增：快速连切会话时丢弃过期 loadMessages 结果 */
  _loadSeq: number;
  /** 最近一次用户发送/乐观气泡时间（防空白会话误删） */
  recentActivityBySession: Record<string, number>;
  touchSessionActivity: (sessionId: string) => void;
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
      _loadSeq: 0,
      recentActivityBySession: {},

      setCurrentSession: (session) => set({ currentSession: session }),

      touchSessionActivity: (sessionId) => {
        if (!sessionId) return;
        set((st) => ({
          recentActivityBySession: {
            ...st.recentActivityBySession,
            [sessionId]: Date.now(),
          },
        }));
      },

      addMessage: (message) => {
        const state = get();
        const sessionId = message.session_id;
        const content = message.content || '';
        const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();

        // 防双插：同会话同 role 同内容在 2.5s 内只保留一条（Enter 连发 / 双击发送）
        if (message.role === 'user' && content && sessionId) {
          const now = Date.parse(String(message.created_at || '')) || Date.now();
          const dup = [...state.messages].reverse().find((m) => {
            if (m.session_id !== sessionId || m.role !== 'user') return false;
            if (norm(m.content || '') !== norm(content)) return false;
            const ts = Date.parse(String(m.created_at || '')) || 0;
            return !ts || Math.abs(now - ts) < 2500;
          });
          if (dup) return;
          // 用户发言 → 标记活跃，空白会话回收跳过
          get().touchSessionActivity(sessionId);
        }

        // 自动命名：用户的第一条消息自动生成 session 标题
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

      removeMessage: (id) =>
        set((state) => ({
          messages: state.messages.filter((m) => m.id !== id),
        })),

      reconcileMessage: (serverMsg) => {
        set((state) => {
          const haveById = state.messages.some((m) => m.id === serverMsg.id);
          if (haveById) return state;

          const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
          const isOptId = (id: string) =>
            id.startsWith('optimistic:') ||
            id.startsWith('local:') ||
            id.startsWith('streaming');
          const serverContent = norm(serverMsg.content || '');
          const serverTs = Date.parse(String(serverMsg.created_at || '')) || Date.now();
          const WINDOW_MS = 120_000;
          const sameSession = (m: Message) =>
            !serverMsg.session_id ||
            !m.session_id ||
            m.session_id === serverMsg.session_id;

          const candidates = state.messages
            .map((m, i) => ({ m, i }))
            .filter(({ m }) => {
              if (m.role !== serverMsg.role) return false;
              if (!sameSession(m)) return false;
              const id = String(m.id || '');
              if (!isOptId(id) && m.role !== 'user') return false;
              const localTs = Date.parse(String(m.created_at || '')) || 0;
              if (localTs && Math.abs(localTs - serverTs) > WINDOW_MS) return false;
              return true;
            });

          // 1) 精确内容
          let optIdx = candidates.find(({ m }) => {
            const c = norm(m.content || '');
            return Boolean(serverContent) && c === serverContent;
          })?.i;

          // 2) 模糊：一端包含另一端（附件前缀 / enrich 差异）
          if (optIdx == null && serverContent) {
            optIdx = candidates.find(({ m }) => {
              const c = norm(m.content || '');
              if (!c) return false;
              return c.includes(serverContent) || serverContent.includes(c);
            })?.i;
          }

          // 3) 用户消息：最近一条 optimistic 直接顶替（ack 权威，防双气泡）
          if (optIdx == null && serverMsg.role === 'user') {
            const optsOnly = candidates.filter(({ m }) => isOptId(String(m.id || '')));
            if (optsOnly.length > 0) {
              optIdx = optsOnly[optsOnly.length - 1].i;
            }
          }

          if (optIdx != null && optIdx >= 0) {
            const next = [...state.messages];
            next[optIdx] = { ...next[optIdx], ...serverMsg, id: serverMsg.id };
            // 清掉同会话其它未 ack 的同文案乐观气泡（双发残留）
            if (serverMsg.role === 'user' && serverContent) {
              return {
                messages: next.filter((m, i) => {
                  if (i === optIdx) return true;
                  if (!isOptId(String(m.id || ''))) return true;
                  if (m.role !== 'user' || !sameSession(m)) return true;
                  const c = norm(m.content || '');
                  if (c === serverContent) return false;
                  if (c && serverContent && (c.includes(serverContent) || serverContent.includes(c)))
                    return false;
                  return true;
                }),
              };
            }
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
        const seq = (get()._loadSeq || 0) + 1;
        set({ isLoading: true, error: null, _loadSeq: seq });
        try {
          // 默认拉最近 200 条（后端 offset=0 时为尾部窗口）
          const messages = await api.getMessages(sessionId, 200, 0);
          const st = get();
          // 快速连切：已切到别的会话 / 更新的 load 已发出 → 丢弃
          if (st._loadSeq !== seq) return;
          if (st.currentSession?.id && st.currentSession.id !== sessionId) return;
          // 仅合并「本会话」尚未 ack 的乐观气泡
          const optimistic = (st.messages || []).filter(
            (m) =>
              String(m.id || '').startsWith('optimistic:') &&
              (!m.session_id || m.session_id === sessionId)
          );
          let merged = Array.isArray(messages) ? [...messages] : [];
          const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
          for (const o of optimistic) {
            const oc = norm(o.content || '');
            // 服务端已有同 role 精确/包含内容 → 丢弃乐观（避免双气泡）
            const covered = merged.some((m) => {
              if (m.role !== o.role) return false;
              const mc = norm(m.content || '');
              if (!oc || !mc) return false;
              return mc === oc || mc.includes(oc) || oc.includes(mc);
            });
            if (!covered) {
              merged = [...merged, o];
            }
          }
          // 只对 optimistic 去重，正式历史允许连发相同「继续」「好的」
          const seenOpt = new Set<string>();
          merged = merged.filter((m) => {
            const id = String(m.id || '');
            if (!id.startsWith('optimistic:')) return true;
            const key = `${m.role}|${norm(m.content || '')}`;
            if (!norm(m.content || '')) return true;
            if (seenOpt.has(key)) return false;
            seenOpt.add(key);
            return true;
          });
          // 加载历史后补标题：取首条用户消息
          if (!st.sessionTitles[sessionId] && merged.length) {
            const firstUser = merged.find(
              (m) => m.role === 'user' && (m.content || '').trim()
            );
            if (firstUser?.content) {
              const raw = firstUser.content.trim().replace(/\s+/g, ' ');
              const title = raw.slice(0, 36) + (raw.length > 36 ? '…' : '');
              // 再次校验世代，避免写脏
              if (get()._loadSeq !== seq) return;
              if (get().currentSession?.id && get().currentSession!.id !== sessionId) return;
              set({
                messages: merged,
                isLoading: false,
                sessionTitles: { ...st.sessionTitles, [sessionId]: title },
              });
              return;
            }
          }
          if (get()._loadSeq !== seq) return;
          if (get().currentSession?.id && get().currentSession!.id !== sessionId) return;
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
