'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient } from '@/lib/api';
import { t } from '@/stores/localeStore';

export type UiMode = 'simple' | 'pro';

export interface TerminalLine {
  id: string;
  type: 'in' | 'out' | 'err' | 'sys';
  text: string;
  ts: number;
}

export interface TerminalTab {
  id: string;
  title: string;
  kind: 'agent' | 'shell';
  lines: TerminalLine[];
  status: 'idle' | 'running';
}

export interface WorkspaceState {
  uiMode: UiMode;
  dockOpen: boolean;
  root: string | null;
  name: string | null;
  tree: Array<{
    name: string;
    path: string;
    type: string;
    children?: WorkspaceState['tree'];
    size?: number;
  }>;
  treeLoading: boolean;
  selectedPath: string | null;
  tabs: TerminalTab[];
  activeTabId: string;
  unreadTerminal: boolean;
  forceProjectOpen: boolean;

  setUiMode: (m: UiMode) => void;
  setDockOpen: (v: boolean) => void;
  toggleDock: () => void;
  setForceProjectOpen: (v: boolean) => void;

  bindRoot: (root: string) => Promise<void>;
  unbind: () => Promise<void>;
  refreshTree: (path?: string) => Promise<void>;
  selectPath: (path: string | null) => void;

  addShellTab: () => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  appendToTab: (tabId: string, line: Omit<TerminalLine, 'id' | 'ts'> & { id?: string }) => void;
  appendAgentOutput: (text: string, type?: 'out' | 'err' | 'sys' | 'in') => void;
  runCommand: (command: string, tabId?: string) => Promise<void>;
  clearUnread: () => void;
}

function lid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const agentTab = (): TerminalTab => ({
  id: 'agent',
  title: 'Agent',
  kind: 'agent',
  lines: [
    {
      id: lid(),
      type: 'sys',
      text: t('workspaceStore._e11'),
      ts: Date.now(),
    },
  ],
  status: 'idle',
});

const shellTab = (n: number): TerminalTab => ({
  id: lid(),
  title: n <= 1 ? 'shell' : `shell ${n}`,
  kind: 'shell',
  lines: [],
  status: 'idle',
});

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      uiMode: 'simple',
      dockOpen: true,
      root: null,
      name: null,
      tree: [],
      treeLoading: false,
      selectedPath: null,
      tabs: [agentTab(), shellTab(1)],
      activeTabId: 'agent',
      unreadTerminal: false,
      forceProjectOpen: false,

      setUiMode: (m) => {
        set({ uiMode: m });
        if (m === 'pro' && !get().root) {
          set({ forceProjectOpen: true, dockOpen: true });
        }
      },
      setDockOpen: (v) => set({ dockOpen: v }),
      toggleDock: () => set({ dockOpen: !get().dockOpen }),
      setForceProjectOpen: (v) => set({ forceProjectOpen: v }),

      bindRoot: async (root: string) => {
        const { data } = await apiClient.post('/workspace/bind', { root });
        set({
          root: data.root,
          name: data.name,
          forceProjectOpen: false,
          dockOpen: true,
        });
        await get().refreshTree();
        get().appendAgentOutput(`Bound project：${data.root}`, 'sys');
      },

      unbind: async () => {
        await apiClient.post('/workspace/unbind').catch(() => null);
        set({ root: null, name: null, tree: [], selectedPath: null });
      },

      refreshTree: async (path = '') => {
        if (!get().root) {
          set({ tree: [] });
          return;
        }
        set({ treeLoading: true });
        try {
          const { data } = await apiClient.get('/workspace/tree', {
            params: { path, depth: 2 },
          });
          set({ tree: Array.isArray(data) ? data : [] });
        } catch {
          set({ tree: [] });
        } finally {
          set({ treeLoading: false });
        }
      },

      selectPath: (path) => set({ selectedPath: path }),

      addShellTab: () => {
        const shells = get().tabs.filter((t) => t.kind === 'shell').length;
        const tab = shellTab(shells + 1);
        set({ tabs: [...get().tabs, tab], activeTabId: tab.id });
      },

      closeTab: (id) => {
        if (id === 'agent') return;
        const tabs = get().tabs.filter((t) => t.id !== id);
        const activeTabId =
          get().activeTabId === id ? tabs[tabs.length - 1]?.id || 'agent' : get().activeTabId;
        set({ tabs, activeTabId });
      },

      setActiveTab: (id) => set({ activeTabId: id, unreadTerminal: false }),

      appendToTab: (tabId, line) => {
        const full: TerminalLine = {
          id: line.id || lid(),
          type: line.type,
          text: line.text,
          ts: Date.now(),
        };
        set({
          tabs: get().tabs.map((t) =>
            t.id === tabId ? { ...t, lines: [...t.lines, full].slice(-2000) } : t
          ),
          unreadTerminal:
            get().uiMode === 'pro' && (!get().dockOpen || get().activeTabId !== tabId)
              ? true
              : get().unreadTerminal,
        });
      },

      appendAgentOutput: (text, type = 'out') => {
        get().appendToTab('agent', { type, text });
        if (get().uiMode === 'pro') {
          set({ unreadTerminal: !get().dockOpen || get().activeTabId !== 'agent' });
        }
      },

      runCommand: async (command, tabId) => {
        const id = tabId || get().activeTabId;
        const tab = get().tabs.find((t) => t.id === id);
        if (!tab || tab.kind === 'agent') return;
        if (!get().root) {
          get().appendToTab(id, { type: 'err', text: t('workspaceStore._e12') });
          return;
        }
        get().appendToTab(id, { type: 'in', text: `$ ${command}` });
        set({
          tabs: get().tabs.map((t) => (t.id === id ? { ...t, status: 'running' } : t)),
        });
        try {
          const { data } = await apiClient.post('/workspace/exec', { command });
          if (data.stdout) get().appendToTab(id, { type: 'out', text: data.stdout.replace(/\n$/, '') });
          if (data.stderr) get().appendToTab(id, { type: 'err', text: data.stderr.replace(/\n$/, '') });
          get().appendToTab(id, {
            type: 'sys',
            text: `exit ${data.exit_code}${data.dangerous ? '  ⚠ Command flagged as high-risk' : ''}`,
          });
        } catch (e: unknown) {
          const msg =
            (e as { response?: { data?: { detail?: string } }; message?: string })?.response?.data
              ?.detail ||
            (e as { message?: string })?.message ||
            'exec failed';
          get().appendToTab(id, { type: 'err', text: String(msg) });
        } finally {
          set({
            tabs: get().tabs.map((t) => (t.id === id ? { ...t, status: 'idle' } : t)),
          });
        }
      },

      clearUnread: () => set({ unreadTerminal: false }),
    }),
    {
      name: 'takton-workspace',
      partialize: (s) => ({
        uiMode: s.uiMode,
        dockOpen: s.dockOpen,
        root: s.root,
        name: s.name,
      }),
    }
  )
);
