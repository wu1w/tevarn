/**
 * Session Hook
 * 封装 Session 相关的业务逻辑
 *
 * - 空白会话（无消息）在切走 / 新建时自动删除
 * - 历史列表只应保留有内容的会话
 */

import { useCallback } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import * as api from '@/lib/api';
import type { SessionConfig } from '@/types';

/** 判断会话是否无有效内容（无用户/助手消息视为空白） */
function hasChatContent(
  messages: { role: string; content?: string | null }[]
): boolean {
  return messages.some(
    (m) =>
      (m.role === 'user' || m.role === 'assistant') &&
      Boolean((m.content || '').trim())
  );
}

export function useSession() {
  const currentSession = useSessionStore((state) => state.currentSession);
  const messages = useSessionStore((state) => state.messages);
  const isLoading = useSessionStore((state) => state.isLoading);
  const error = useSessionStore((state) => state.error);
  const setCurrentSession = useSessionStore((state) => state.setCurrentSession);
  const setMessages = useSessionStore((state) => state.setMessages);
  const clearMessages = useSessionStore((state) => state.clearMessages);
  const setError = useSessionStore((state) => state.setError);
  const addMessage = useSessionStore((state) => state.addMessage);
  const updateMessage = useSessionStore((state) => state.updateMessage);
  const loadSession = useSessionStore((state) => state.loadSession);
  const loadMessages = useSessionStore((state) => state.loadMessages);

  /**
   * 若会话无内容则删除。
   * @param knownEmpty 若已知本地消息为空可跳过请求
   * @returns 是否已删除
   */
  const discardEmptySession = useCallback(
    async (
      sessionId: string | null | undefined,
      options?: { knownEmpty?: boolean }
    ): Promise<boolean> => {
      if (!sessionId) return false;
      try {
        try {
          const active = await api.getActiveSessionIds();
          if (active?.includes(sessionId)) return false;
        } catch {
          /* ignore */
        }
        let empty = options?.knownEmpty;
        if (empty === undefined) {
          const msgs = await api.getMessages(sessionId, 5, 0);
          empty = !hasChatContent(msgs || []);
        }
        if (!empty) return false;
        await api.deleteSession(sessionId);
        // 清理本地标题 / 星标
        const st = useSessionStore.getState();
        const { [sessionId]: _removed, ...restTitles } = st.sessionTitles;
        useSessionStore.setState({
          sessionTitles: restTitles,
          starredSessionIds: st.starredSessionIds.filter((id) => id !== sessionId),
        });
        return true;
      } catch {
        return false;
      }
    },
    []
  );

  /** 离开当前会话前：空白则删除 */
  const discardCurrentIfEmpty = useCallback(async (): Promise<string | null> => {
    const st = useSessionStore.getState();
    const prevId = st.currentSession?.id || null;
    if (!prevId) return null;
    const knownEmpty = !hasChatContent(st.messages);
    const deleted = await discardEmptySession(prevId, { knownEmpty });
    return deleted ? prevId : null;
  }, [discardEmptySession]);

  const createAndLoadSession = useCallback(
    async (userId?: string, config?: Partial<SessionConfig>) => {
      // 切走空白会话
      await discardCurrentIfEmpty();
      // AIOS：从 Agent Profile「联系 TA」写入 contact_agent + 人设文案
      const session = await api.createSession(
        userId,
        config ? (config as SessionConfig) : undefined,
      );
      setCurrentSession(session);
      clearMessages();
      setError(null);
      const titleFrom = config?.contact_agent || null;
      if (titleFrom) {
        useSessionStore.getState().setSessionTitle(session.id, `→ ${titleFrom}`);
      }
      return session;
    },
    [setCurrentSession, clearMessages, setError, discardCurrentIfEmpty]
  );

  /**
   * 企业 IM：点联系人 → 一人一会话（服务端 find-or-create）。
   * 连点同一人不会堆出多个 session。
   */
  const openContactSession = useCallback(
    async (name: string) => {
      const n = (name || '').trim();
      if (!n) throw new Error('contact name required');
      const st = useSessionStore.getState();
      const cur = st.currentSession;
      const curContact = (cur?.config as { contact_agent?: string } | undefined)?.contact_agent;
      // 已在该联系人会话：只确保消息在
      if (cur && curContact === n) {
        await loadMessages(cur.id);
        return cur;
      }
      await discardCurrentIfEmpty();
      const session = await api.openContactSession(n);
      setCurrentSession(session);
      clearMessages();
      setError(null);
      useSessionStore.getState().setSessionTitle(session.id, `→ ${n}`);
      await loadMessages(session.id);
      return session;
    },
    [discardCurrentIfEmpty, setCurrentSession, clearMessages, setError, loadMessages]
  );

  const switchSession = useCallback(
    async (sessionId: string) => {
      const st = useSessionStore.getState();
      const prevId = st.currentSession?.id;
      if (prevId && prevId !== sessionId) {
        const knownEmpty = !hasChatContent(st.messages);
        await discardEmptySession(prevId, { knownEmpty });
      }
      // 立即清空旧消息，避免短暂显示上一个会话
      clearMessages();
      setError(null);
      await loadSession(sessionId);
      await loadMessages(sessionId);
    },
    [loadSession, loadMessages, clearMessages, setError, discardEmptySession]
  );

  return {
    currentSession,
    messages,
    isLoading,
    error,
    createAndLoadSession,
    openContactSession,
    switchSession,
    discardEmptySession,
    discardCurrentIfEmpty,
    loadMessages,
    addMessage,
    updateMessage,
    setMessages,
    setCurrentSession,
    clearMessages,
    setError,
  };
}
