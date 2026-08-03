/**
 * Session Hook
 * 封装 Session 相关的业务逻辑
 *
 * - 空白「普通」会话在切走 / 新建时自动删除
 * - 联系人 IM 会话（contact_agent / human_dm / 标题「→ 」）永不自动删
 *   （一人一会话，连切空白同事时也不能删，否则 messages 404 / delete 409）
 * - 历史列表只应保留有内容的会话（联系人会话除外）
 */

import { useCallback } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import * as api from '@/lib/api';
import type { SessionConfig } from '@/types';

/** 判断会话是否有有效内容（无用户/助手消息视为空白） */
function hasChatContent(
  messages: { role: string; content?: string | null; id?: string }[]
): boolean {
  return messages.some(
    (m) =>
      (m.role === 'user' || m.role === 'assistant') &&
      Boolean((m.content || '').trim())
  );
}

/** 企业 IM 联系人会话：本地可判定则直接保护，避免误删 */
function isContactSessionLocal(sessionId: string): boolean {
  const st = useSessionStore.getState();
  const title = (st.sessionTitles?.[sessionId] || '').trim();
  if (title.startsWith('→ ')) return true;
  const s = st.currentSession;
  if (s?.id === sessionId) {
    const cfg = (s.config || {}) as { contact_agent?: string; source?: string };
    if (cfg.contact_agent) return true;
    if (cfg.source === 'human_dm') return true;
  }
  return false;
}

function isContactConfig(cfg: unknown): boolean {
  if (!cfg || typeof cfg !== 'object') return false;
  const c = cfg as { contact_agent?: string; source?: string };
  return Boolean(c.contact_agent) || c.source === 'human_dm';
}

/** 刚发过言 / 有乐观气泡：绝不当空白删 */
function hasRecentOrPendingActivity(
  sessionId: string,
  messages: {
    id?: string;
    session_id?: string;
    role?: string;
    content?: string | null;
  }[]
): boolean {
  const st = useSessionStore.getState();
  const touched = st.recentActivityBySession?.[sessionId] || 0;
  // 90s 内有发送/乐观气泡 → 保留
  if (touched && Date.now() - touched < 90_000) return true;
  // 本地仍有本会话 optimistic 用户消息
  if (
    messages.some(
      (m) =>
        String(m.id || '').startsWith('optimistic:') &&
        m.role === 'user' &&
        (!m.session_id || m.session_id === sessionId) &&
        Boolean((m.content || '').trim())
    )
  ) {
    return true;
  }
  // 有本地标题通常说明说过话
  const title = (st.sessionTitles?.[sessionId] || '').trim();
  if (title && !title.startsWith('→ ')) {
    // 联系人会话标题「→ 名字」可能尚未说话；用户消息标题才算
    if (touched) return true;
  }
  return false;
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
   * 联系人会话 / 活跃会话 / active-ids 不可用时一律不删。
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
        // 联系人 IM：永不自动删（A→B→A 连切不能变成 404）
        if (isContactSessionLocal(sessionId)) return false;

        // 刚发过言 / 乐观气泡未落库：绝不删（P2-10 竞态）
        const localMsgs = useSessionStore.getState().messages || [];
        if (hasRecentOrPendingActivity(sessionId, localMsgs)) return false;

        // 流式/agent 仍在跑：绝不自动删（防 WS 断时误删）
        try {
          const { streamSessionApi } = await import('@/stores/streamSessionStore');
          const st = streamSessionApi().get(sessionId);
          if (st.agentRunning || st.isStreaming) return false;
        } catch {
          /* ignore */
        }
        try {
          const active = await api.getActiveSessionIds();
          if (active?.includes(sessionId)) return false;
        } catch {
          // 拉 active 失败：一律不删（避免 WS 仍连时 DELETE → 409）
          return false;
        }

        // knownEmpty 仅作提示：有最近活动或年轻会话时仍以服务端为准
        let empty = options?.knownEmpty;
        const forceServerCheck =
          empty === true ||
          empty === undefined ||
          Boolean(useSessionStore.getState().recentActivityBySession?.[sessionId]);
        if (forceServerCheck) {
          try {
            const msgs = await api.getMessages(sessionId, 8, 0);
            empty = !hasChatContent(msgs || []);
          } catch {
            // 查历史失败 → 不删，避免误杀
            return false;
          }
        }
        if (!empty) return false;

        // 服务端再确认：联系人会话 / 新建 60s 内不自动删
        try {
          const sess = await api.getSession(sessionId);
          if (isContactConfig(sess?.config)) return false;
          const created = sess?.created_at ? Date.parse(String(sess.created_at)) : 0;
          if (created && Date.now() - created < 60_000) return false;
        } catch {
          // 404 / 网络失败：不继续 delete
          return false;
        }

        // 删除前再确认一次最近活动（await 期间用户可能又发了）
        if (
          hasRecentOrPendingActivity(
            sessionId,
            useSessionStore.getState().messages || []
          )
        ) {
          return false;
        }
        // 再次确认仍非联系人（切走后 title 可能后补）
        if (isContactSessionLocal(sessionId)) return false;

        await api.deleteSession(sessionId);
        // 清理本地标题 / 星标
        const st = useSessionStore.getState();
        const { [sessionId]: _removed, ...restTitles } = st.sessionTitles;
        useSessionStore.setState({
          sessionTitles: restTitles,
          starredSessionIds: st.starredSessionIds.filter((id) => id !== sessionId),
        });
        try {
          window.dispatchEvent(
            new CustomEvent('takton:session-invalid', { detail: { sessionId } })
          );
        } catch {
          /* ignore */
        }
        return true;
      } catch {
        // 409 active / 404 already gone 等：吞掉，不抛
        return false;
      }
    },
    []
  );

  /** 离开当前会话前：空白则删除（联系人会话跳过） */
  const discardCurrentIfEmpty = useCallback(async (): Promise<string | null> => {
    const st = useSessionStore.getState();
    const prevId = st.currentSession?.id || null;
    if (!prevId) return null;
    if (isContactSessionLocal(prevId) || isContactConfig(st.currentSession?.config)) {
      return null;
    }
    const knownEmpty = !hasChatContent(st.messages);
    const deleted = await discardEmptySession(prevId, { knownEmpty });
    return deleted ? prevId : null;
  }, [discardEmptySession]);

  const createAndLoadSession = useCallback(
    async (userId?: string, config?: Partial<SessionConfig>) => {
      // 切走空白会话（联系人不会被删）
      await discardCurrentIfEmpty();
      // AIOS：从 Agent Profile「联系 TA」写入 contact_agent + 人设文案
      const session = await api.createSession(
        userId,
        config ? (config as SessionConfig) : undefined
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
   * 连点同一人不会堆出多个 session；切到另一联系人时也不销毁上一会话。
   */
  const openContactSession = useCallback(
    async (name: string) => {
      const n = (name || '').trim();
      if (!n) throw new Error('contact name required');
      const st = useSessionStore.getState();
      const cur = st.currentSession;
      const curContact = (cur?.config as { contact_agent?: string } | undefined)
        ?.contact_agent;
      // 已在该联系人会话：只确保消息在
      if (cur && curContact === n) {
        await loadMessages(cur.id);
        return cur;
      }
      // 仅清理「非联系人」的空白草稿；联系人会话保留以便回切
      if (cur && !isContactConfig(cur.config) && !isContactSessionLocal(cur.id)) {
        await discardCurrentIfEmpty();
      }
      const session = await api.openContactSession(n);
      setCurrentSession(session);
      // 不先 clearMessages：等 loadMessages 替换，减轻连切闪空
      setError(null);
      useSessionStore.getState().setSessionTitle(session.id, `→ ${n}`);
      // 世代号：丢弃过期的 load 结果
      useSessionStore.setState({
        _loadSeq: (useSessionStore.getState()._loadSeq || 0) + 1,
      });
      try {
        await loadMessages(session.id);
      } catch {
        /* loadMessages 内部处理 404 */
      }
      return session;
    },
    [discardCurrentIfEmpty, setCurrentSession, setError, loadMessages]
  );

  const switchSession = useCallback(
    async (sessionId: string) => {
      const st = useSessionStore.getState();
      const prevId = st.currentSession?.id;
      if (prevId === sessionId) {
        await loadMessages(sessionId);
        return;
      }
      if (prevId) {
        // 联系人会话永不延迟清理；仅对普通空白会话延迟 discard
        const prevIsContact =
          isContactSessionLocal(prevId) ||
          isContactConfig(st.currentSession?.config);
        if (!prevIsContact) {
          const knownEmpty = !hasChatContent(st.messages);
          const toDrop = prevId;
          window.setTimeout(() => {
            const cur = useSessionStore.getState().currentSession?.id;
            if (cur === toDrop) return; // 又切回来了
            if (isContactSessionLocal(toDrop)) return;
            void discardEmptySession(toDrop, { knownEmpty });
          }, 1200);
        }
      }
      // 不作 clearMessages：等 loadMessages 一次性替换，避免侧栏连点闪空白
      // 但若 load 失败会清空（见 sessionStore），禁止 B 标题下挂 A 历史
      setError(null);
      useSessionStore.setState({ _loadSeq: (st._loadSeq || 0) + 1 });
      try {
        await loadSession(sessionId);
      } catch {
        // loadSession 内部已 set error；不继续拉消息
        return;
      }
      // 并发连切：最终 current 已是别人 → 不拉消息
      if (useSessionStore.getState().currentSession?.id !== sessionId) return;
      try {
        await loadMessages(sessionId);
      } catch {
        // loadMessages 失败路径已清空 messages（仍在本会话时）
      }
      // 防御：消息仍属其它会话 → 清空
      const after = useSessionStore.getState();
      if (after.currentSession?.id === sessionId) {
        const foreign = (after.messages || []).some(
          (m) =>
            m.session_id &&
            m.session_id !== sessionId &&
            !String(m.id || '').startsWith('optimistic:')
        );
        if (foreign) {
          useSessionStore.setState({ messages: [] });
        }
      }
    },
    [loadSession, loadMessages, setError, discardEmptySession]
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
