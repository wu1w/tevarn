import { create } from 'zustand';

export interface ConfirmRequestData {
  confirmId: string;
  title: string;
  command: string;
  reason: string;
}

interface ConfirmState {
  /** 当前待确认请求（null=无弹窗） */
  pending: ConfirmRequestData | null;
  /** WS 发送函数（由 useWebSocket 连接时注入） */
  _sender: ((confirmId: string, approved: boolean) => void) | null;

  showConfirm: (data: ConfirmRequestData) => void;
  registerSender: (fn: ((confirmId: string, approved: boolean) => void) | null) => void;
  /** 用户决定：发送 confirm_response 并关闭弹窗 */
  respond: (approved: boolean) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  pending: null,
  _sender: null,

  showConfirm: (data) => set({ pending: data }),

  registerSender: (fn) => set({ _sender: fn }),

  respond: (approved) => {
    const { pending, _sender } = get();
    if (!pending) return;
    _sender?.(pending.confirmId, approved);
    set({ pending: null });
  },
}));
