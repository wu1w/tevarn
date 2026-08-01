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
  reconcileMessage: (
    serverMsg: Message,
    opts?: { matchContents?: string[] },
  ) => void;
  setMessages: (messages: Message[]) => void;
  loadSession: (sessionId: string) => Promise<void>;
  loadMessages: (sessionId: string) => Promise<void>;
  /** 向上翻页加载更早消息 */
  loadOlderMessages: (
    sessionId: string,
  ) => Promise<{ loaded: number; hasMore: boolean }>;
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

      reconcileMessage: (serverMsg, opts) => {
        set((state) => {
          const haveById = state.messages.some((m) => m.id === serverMsg.id);
          if (haveById) return state;

          const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
          const isOptId = (id: string) =>
            id.startsWith('optimistic:') ||
            id.startsWith('local:') ||
            id.startsWith('streaming');
          // 服务端 created_at 常为 naive（无 Z），浏览器按本地解析；乐观气泡用 toISOString() UTC。
          // 在 UTC+8 下可差 ~8h，旧 120s 窗口会把全部 optimistic 滤掉 → ack 再 append = 双气泡。
          const serverContent = norm(serverMsg.content || '');
          const extraMatch = (opts?.matchContents || []).map(norm).filter(Boolean);
          const matchPool = Array.from(
            new Set([serverContent, ...extraMatch].filter(Boolean)),
          );
          const sameSession = (m: Message) =>
            !serverMsg.session_id ||
            !m.session_id ||
            m.session_id === serverMsg.session_id;

          // 只顶替临时 id，绝不按正文改写已有正式消息（「好的」连发会踩旧气泡）
          const optCandidates = state.messages
            .map((m, i) => ({ m, i }))
            .filter(({ m }) => {
              if (m.role !== serverMsg.role) return false;
              if (!sameSession(m)) return false;
              return isOptId(String(m.id || ''));
            });

          const contentHit = (c: string) =>
            matchPool.some(
              (sc) => sc && (c === sc || c.includes(sc) || sc.includes(c)),
            );

          // 1) 精确 / 模糊（从最新往旧找，避免顶替更早的乐观残留）
          let optIdx: number | undefined;
          for (let k = optCandidates.length - 1; k >= 0; k--) {
            const { m, i } = optCandidates[k];
            const c = norm(m.content || '');
            if (c && contentHit(c)) {
              optIdx = i;
              break;
            }
          }

          // 2) 用户消息：仍无命中 → 最近一条 optimistic 直接顶替（ack 权威）
          //    无时间窗：时区差不能再挡合并
          if (optIdx == null && serverMsg.role === 'user' && optCandidates.length > 0) {
            optIdx = optCandidates[optCandidates.length - 1].i;
          }

          // 3) 助手流式：顶替 streaming 占位
          if (optIdx == null && serverMsg.role === 'assistant' && optCandidates.length > 0) {
            const stream = [...optCandidates]
              .reverse()
              .find(({ m }) => String(m.id || '').startsWith('streaming'));
            if (stream) optIdx = stream.i;
          }

          if (optIdx != null && optIdx >= 0) {
            const next = [...state.messages];
            // 展示优先用较短/原文（乐观），id/时间用服务端；避免附件 enrich 把气泡撑成超长
            const prev = next[optIdx];
            const preferDisplay =
              serverMsg.role === 'user' &&
              prev?.content &&
              serverMsg.content &&
              norm(prev.content).length < norm(serverMsg.content).length &&
              norm(serverMsg.content).includes(norm(prev.content));
            next[optIdx] = {
              ...prev,
              ...serverMsg,
              id: serverMsg.id,
              content: preferDisplay ? prev.content : serverMsg.content,
            };
            // 清掉同会话其它未 ack 乐观用户气泡（双发 / 双路径残留）
            if (serverMsg.role === 'user') {
              return {
                messages: next.filter((m, i) => {
                  if (i === optIdx) return true;
                  if (!isOptId(String(m.id || ''))) return true;
                  if (m.role !== 'user' || !sameSession(m)) return true;
                  const c = norm(m.content || '');
                  if (!c) return false; // 空乐观也丢
                  if (contentHit(c)) return false;
                  // 同会话、仍挂着的用户乐观：ack 已到则一律清（防时区/文案差漏网）
                  return false;
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
        const finishIfMine = () => {
          // 仅当仍是本次 load 的世代时清 isLoading，避免踩掉更新的加载
          if (get()._loadSeq !== seq) return;
          set({ isLoading: false });
        };
        try {
          // 默认拉最近 200 条（后端 offset=0 时为尾部窗口）
          const messages = await api.getMessages(sessionId, 200, 0);
          const st = get();
          // 快速连切：已切到别的会话 / 更新的 load 已发出 → 丢弃（仍清 isLoading）
          if (st._loadSeq !== seq) {
            finishIfMine();
            return;
          }
          if (st.currentSession?.id && st.currentSession.id !== sessionId) {
            finishIfMine();
            return;
          }
          // 仅合并「本会话」尚未 ack 的乐观气泡
          const optimistic = (st.messages || []).filter(
            (m) =>
              String(m.id || '').startsWith('optimistic:') &&
              (!m.session_id || m.session_id === sessionId)
          );
          let merged = Array.isArray(messages) ? [...messages] : [];
          const norm = (s: string) => (s || '').replace(/\s+/g, ' ').trim();
          // 服务端尾部最近几条用户消息（用于覆盖乐观，忽略时区）
          const recentServerUser = merged
            .filter((m) => m.role === 'user')
            .slice(-8);
          for (const o of optimistic) {
            const oc = norm(o.content || '');
            // 服务端已有同 role 精确/包含内容 → 丢弃乐观（避免双气泡）
            const covered = merged.some((m) => {
              if (m.role !== o.role) return false;
              const mc = norm(m.content || '');
              if (!oc || !mc) return false;
              return mc === oc || mc.includes(oc) || oc.includes(mc);
            });
            // 用户乐观：若尾部已有任意正式用户消息且本轮在跑，也倾向丢弃
            // （内容被 enrich 后 includes 可能失败；ack 应已顶替，残留乐观一律危险）
            const userOptStale =
              o.role === 'user' &&
              recentServerUser.length > 0 &&
              recentServerUser.some((m) => {
                const mc = norm(m.content || '');
                if (!oc || !mc) return false;
                return mc === oc || mc.includes(oc) || oc.includes(mc) || oc.slice(0, 40) === mc.slice(0, 40);
              });
            if (!covered && !userOptStale) {
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
          // 防御：同会话尾部连续两条用户、正文相同且 id 不同 → 丢临时 id 那条
          const deduped: Message[] = [];
          for (const m of merged) {
            const prev = deduped[deduped.length - 1];
            if (
              prev &&
              m.role === 'user' &&
              prev.role === 'user' &&
              norm(prev.content || '') &&
              norm(prev.content || '') === norm(m.content || '')
            ) {
              const prevOpt = String(prev.id || '').startsWith('optimistic:');
              const curOpt = String(m.id || '').startsWith('optimistic:');
              if (prevOpt && !curOpt) {
                deduped[deduped.length - 1] = m;
                continue;
              }
              if (!prevOpt && curOpt) {
                continue; // 丢当前乐观
              }
            }
            deduped.push(m);
          }
          merged = deduped;
          // 加载历史后补标题：取首条用户消息
          if (!st.sessionTitles[sessionId] && merged.length) {
            const firstUser = merged.find(
              (m) => m.role === 'user' && (m.content || '').trim()
            );
            if (firstUser?.content) {
              const raw = firstUser.content.trim().replace(/\s+/g, ' ');
              const title = raw.slice(0, 36) + (raw.length > 36 ? '…' : '');
              // 再次校验世代，避免写脏
              if (get()._loadSeq !== seq) {
                finishIfMine();
                return;
              }
              if (get().currentSession?.id && get().currentSession!.id !== sessionId) {
                finishIfMine();
                return;
              }
              set({
                messages: merged,
                isLoading: false,
                sessionTitles: { ...st.sessionTitles, [sessionId]: title },
              });
              return;
            }
          }
          if (get()._loadSeq !== seq) {
            finishIfMine();
            return;
          }
          if (get().currentSession?.id && get().currentSession!.id !== sessionId) {
            finishIfMine();
            return;
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
          // 失败：瞬时网络错误不清空已有本会话历史（P2 长会话空白）
          // 仅当消息明显属于「其它会话」时才清
          if (get()._loadSeq === seq) {
            const stillHere = get().currentSession?.id === sessionId;
            const msgs = get().messages || [];
            const foreign =
              stillHere &&
              msgs.length > 0 &&
              msgs.every(
                (m) =>
                  m.session_id &&
                  m.session_id !== sessionId &&
                  !String(m.id || '').startsWith('optimistic:'),
              );
            set({
              error: (err as Error).message,
              isLoading: false,
              ...(foreign ? { messages: [] } : {}),
            });
          }
        }
      },

      /** 向上翻页：加载更早消息，prepend 到现有列表 */
      loadOlderMessages: async (sessionId: string) => {
        const st = get();
        if (st.currentSession?.id !== sessionId) return { loaded: 0, hasMore: false };
        const msgs = st.messages || [];
        const oldest = msgs.find((m) => !String(m.id || '').startsWith('optimistic:'));
        const before = oldest?.created_at;
        if (!before) return { loaded: 0, hasMore: false };
        try {
          const older = await api.getMessages(sessionId, 100, 0, { before: String(before) });
          if (get().currentSession?.id !== sessionId) return { loaded: 0, hasMore: false };
          if (!older?.length) return { loaded: 0, hasMore: false };
          const have = new Set((get().messages || []).map((m) => m.id));
          const fresh = older.filter((m) => m.id && !have.has(m.id));
          if (fresh.length) {
            set({ messages: [...fresh, ...(get().messages || [])] });
          }
          return { loaded: fresh.length, hasMore: older.length >= 100 };
        } catch {
          return { loaded: 0, hasMore: false };
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
