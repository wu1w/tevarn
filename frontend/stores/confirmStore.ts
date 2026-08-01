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
    if (!pending) {
      set({ pending: data });
      return;
    }
    set({ queue: [...queue, data] });
  },

  registerSender: (fn) => set({ _sender: fn }),

  respond: (scope) => {
    const { pending, queue, _sender } = get();
    if (!pending) return;
    const approved = scope !== 'deny';
    _sender?.(pending.confirmId, approved, scope);
    const [next, ...rest] = queue;
    set({ pending: next || null, queue: rest });
  },
}));
