import { create } from 'zustand';

export interface ScreenshotEntry {
  id: string;
  image_base64: string;
  tool_name: string;
  timestamp: string;
  session_id?: string;
}

interface ScreenshotState {
  shots: ScreenshotEntry[];
  panelOpen: boolean;
  addShot: (shot: Omit<ScreenshotEntry, 'id'>) => void;
  togglePanel: () => void;
  setPanelOpen: (open: boolean) => void;
  clear: () => void;
}

const MAX_SHOTS = 20;

export const useScreenshotStore = create<ScreenshotState>((set) => ({
  shots: [],
  panelOpen: false,
  addShot: (shot) =>
    set((s) => ({
      shots: [
        { ...shot, id: crypto.randomUUID() },
        ...s.shots,
      ].slice(0, MAX_SHOTS),
      panelOpen: true, // auto-open on new screenshot
    })),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  setPanelOpen: (open) => set({ panelOpen: open }),
  clear: () => set({ shots: [] }),
}));
