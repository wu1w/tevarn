import { create } from 'zustand';

/** 对话右侧唯一工作区：一次只开一栏，对标 Cursor 侧栏。 */
export type ChatInspectorTab = 'files' | 'preview' | 'terminal' | 'run' | 'trace';

interface ChatInspectorState {
  tab: ChatInspectorTab | null;
  setTab: (tab: ChatInspectorTab | null) => void;
  toggleTab: (tab: ChatInspectorTab) => void;
}

export const useChatInspectorStore = create<ChatInspectorState>((set, get) => ({
  tab: null,
  setTab: (tab) => set({ tab }),
  toggleTab: (tab) => set({ tab: get().tab === tab ? null : tab }),
}));
