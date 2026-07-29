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
}

interface ConfirmState {
  pending: ConfirmRequestData | null;
  /** WS 发送：confirm_id + approved + scope */
  _sender: ((confirmId: string, approved: boolean, scope: ConfirmScope) => void) | null;

  showConfirm: (data: ConfirmRequestData) => void;
  registerSender: (
    fn: ((confirmId: string, approved: boolean, scope: ConfirmScope) => void) | null,
  ) => void;
  /** 用户决定 */
  respond: (scope: ConfirmScope) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  pending: null,
  _sender: null,

  showConfirm: (data) => set({ pending: data }),

  registerSender: (fn) => set({ _sender: fn }),

  respond: (scope) => {
    const { pending, _sender } = get();
    if (!pending) return;
    const approved = scope !== 'deny';
    _sender?.(pending.confirmId, approved, scope);
    set({ pending: null });
  },
}));
