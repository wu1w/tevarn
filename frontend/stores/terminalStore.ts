import { create } from 'zustand';

/**
 * 实时终端流 store（替代原截图面板）。
 *
 * Agent computer（desktop_*）与 shell 类工具调用以命令行风格实时入流，
 * 供 TerminalPanel 渲染。截图推送已退役——desktop_screenshot 工具仍保留
 * 供 agent 视觉感知，但其结果不再以图片形式占用面板，仅留一行文本记录。
 */

export interface TerminalEntry {
  id: string;
  /** 工具调用 id（start/end 配对更新同一条目） */
  callId: string;
  name: string;
  /** 参数摘要（已截断） */
  argsText: string;
  status: 'running' | 'completed' | 'failed';
  /** 结果摘要（已截断，running 时为空） */
  resultText: string;
  timestamp: string;
}

interface TerminalState {
  entries: TerminalEntry[];
  panelOpen: boolean;
  /** start：追加新条目；end：按 callId 更新既有条目 */
  upsert: (entry: Omit<TerminalEntry, 'id' | 'timestamp'>) => void;
  setPanelOpen: (open: boolean) => void;
  togglePanel: () => void;
  clear: () => void;
}

const MAX_ENTRIES = 100;

export const useTerminalStore = create<TerminalState>((set) => ({
  entries: [],
  panelOpen: false,
  upsert: (entry) =>
    set((s) => {
      const idx = s.entries.findIndex((e) => e.callId === entry.callId);
      const full: TerminalEntry = {
        ...entry,
        id: idx >= 0 ? s.entries[idx].id : crypto.randomUUID(),
        timestamp: new Date().toISOString(),
      };
      let next: TerminalEntry[];
      if (idx >= 0) {
        next = [...s.entries];
        next[idx] = full;
      } else {
        next = [...s.entries, full];
      }
      // desktop computer 操作自动展开面板（首次出现时引起注意）
      const autoOpen = entry.name.startsWith('desktop_') ? { panelOpen: true } : {};
      return { entries: next.slice(-MAX_ENTRIES), ...autoOpen };
    }),
  setPanelOpen: (open) => set({ panelOpen: open }),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  clear: () => set({ entries: [] }),
}));
