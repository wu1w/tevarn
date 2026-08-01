/**
 * 同会话多 Tab 轻量协同：BroadcastChannel 同步 streaming / stopping 提示。
 * 不替代 WS（两边仍各自连后端）；避免「B 屏不知道 A 在跑 / 双份 Stop 误解」。
 */

export type SessionTabMsg =
  | {
      type: 'stream_state';
      sessionId: string;
      isStreaming: boolean;
      isStopping: boolean;
      statusDetail?: string | null;
      tabId: string;
      ts: number;
    }
  | {
      type: 'hello';
      sessionId: string;
      tabId: string;
      ts: number;
    }
  | {
      type: 'peer_claim';
      sessionId: string;
      tabId: string;
      isStreaming: boolean;
      ts: number;
    };

const TAB_ID =
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `tab-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

export function getTabId(): string {
  return TAB_ID;
}

function channelName(sessionId: string): string {
  return `takton-session-${sessionId}`;
}

/** 发送时可省略 tabId/ts（由 channel 注入） */
export type SessionTabPostMsg =
  | {
      type: 'stream_state';
      sessionId: string;
      isStreaming: boolean;
      isStopping: boolean;
      statusDetail?: string | null;
    }
  | {
      type: 'hello';
      sessionId: string;
    }
  | {
      type: 'peer_claim';
      sessionId: string;
      isStreaming: boolean;
    };

export function openSessionTabChannel(
  sessionId: string,
  onMessage: (msg: SessionTabMsg) => void,
): { post: (msg: SessionTabPostMsg) => void; close: () => void } {
  if (typeof window === 'undefined' || typeof BroadcastChannel === 'undefined' || !sessionId) {
    return { post: () => undefined, close: () => undefined };
  }
  let ch: BroadcastChannel | null = null;
  try {
    ch = new BroadcastChannel(channelName(sessionId));
  } catch {
    return { post: () => undefined, close: () => undefined };
  }
  const handler = (ev: MessageEvent) => {
    const data = ev.data as SessionTabMsg | null;
    if (!data || typeof data !== 'object') return;
    if ((data as { tabId?: string }).tabId === TAB_ID) return;
    if ((data as { sessionId?: string }).sessionId !== sessionId) return;
    onMessage(data);
  };
  ch.addEventListener('message', handler);
  return {
    post: (msg) => {
      try {
        ch?.postMessage({
          ...msg,
          tabId: TAB_ID,
          ts: Date.now(),
        } satisfies SessionTabMsg);
      } catch {
        /* ignore */
      }
    },
    close: () => {
      try {
        ch?.removeEventListener('message', handler);
        ch?.close();
      } catch {
        /* ignore */
      }
      ch = null;
    },
  };
}
