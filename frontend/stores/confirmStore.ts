import { create } from 'zustand';

/** 危险确认授权作用域 */
export type ConfirmScope = 'once' | 'session' | 'agent' | 'deny';

export interface ConfirmRequestData {
  confirmId: string;
  title: string;
  command: string;
  reason: string;
  tool?: string;
  agentId?: string;
  agentName?: string;
  timeout?: number;
  /** 来源会话（多 tab fan-out 时展示，避免在 B 批 A 的操作时无上下文） */
  sessionId?: string;
}

interface ConfirmState {
  /** 当前展示的确认（队列头） */
  pending: ConfirmRequestData | null;
  /** 等待中的后续确认（后到不覆盖先到） */
  queue: ConfirmRequestData[];
  /** WS 发送：confirm_id + approved + scope */
  _sender: ((confirmId: string, approved: boolean, scope: ConfirmScope) => void) | null;

  showConfirm: (data: ConfirmRequestData) => void;
  registerSender: (
    fn: ((confirmId: string, approved: boolean, scope: ConfirmScope) => void) | null,
  ) => void;
  /** 用户决定当前弹窗；若有队列则弹出下一个 */
  respond: (scope: ConfirmScope) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  pending: null,
  queue: [],
  _sender: null,

  showConfirm: (data) => {
    const { pending, queue } = get();
    // 去重同 confirmId
    if (pending?.confirmId === data.confirmId) return;
    if (queue.some((q) => q.confirmId === data.confirmId)) return;

    let curSid = '';
    try {
      // 懒取，避免 confirmStore ↔ sessionStore 硬循环依赖
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useSessionStore } = require('@/stores/sessionStore') as typeof import('@/stores/sessionStore');
      curSid = String(useSessionStore.getState().currentSession?.id || '');
    } catch {
      curSid = '';
    }
    const isCur = Boolean(data.sessionId && curSid && data.sessionId === curSid);

    if (!pending) {
      set({ pending: data });
      if (isCur) return;
      // 非当前会话：仍弹，但 toast 提示（用户可能已切页）
      if (data.sessionId && typeof window !== 'undefined') {
        try {
          const { useToastStore } = require('@/stores/toastStore') as typeof import('@/stores/toastStore');
          useToastStore
            .getState()
            .addToast(
              `会话 ${String(data.sessionId).slice(0, 8)} 有操作待确认`,
              'info',
            );
        } catch {
          /* ignore */
        }
      }
      return;
    }

    // 当前会话的确认插队到队首（优先处理眼前页）
    if (isCur && pending.sessionId !== curSid) {
      set({ pending: data, queue: [pending, ...queue] });
      return;
    }
    set({ queue: [...queue, data] });
  },

  registerSender: (fn) => set({ _sender: fn }),

  respond: (scope) => {
    const { pending, queue, _sender } = get();
    if (!pending) return;
    const approved = scope !== 'deny';
    const id = pending.confirmId;
    // 优先 WS；无 sender 时 HTTP 兜底（人在 B tab 批 A 的确认时 B 可能已断）
    if (_sender) {
      _sender(id, approved, scope);
    } else if (typeof window !== 'undefined') {
      void import('@/lib/api')
        .then(({ resolveConfirmHttp }) =>
          resolveConfirmHttp(id, approved, scope === 'deny' ? 'deny' : scope)
        )
        .catch((e) => console.warn('confirm HTTP fallback failed', e));
    }
    // 下一个：当前会话优先
    let curSid = '';
    try {
      const { useSessionStore } =
        require('@/stores/sessionStore') as typeof import('@/stores/sessionStore');
      curSid = String(useSessionStore.getState().currentSession?.id || '');
    } catch {
      curSid = '';
    }
    const rest = [...queue];
    let next: ConfirmRequestData | null = null;
    if (curSid) {
      const idx = rest.findIndex((q) => q.sessionId === curSid);
      if (idx >= 0) next = rest.splice(idx, 1)[0] || null;
    }
    if (!next) next = rest.shift() || null;
    set({ pending: next, queue: rest });
  },
}));
