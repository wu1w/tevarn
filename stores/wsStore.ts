/**
 * WebSocket 连接状态全局 Store
 */

import { create } from 'zustand';

interface WsState {
  isConnected: boolean;
  isConnecting: boolean;
  lastError: string | null;
  reconnectAttempts: number;

  setConnected: (connected: boolean) => void;
  setConnecting: (connecting: boolean) => void;
  setError: (error: string | null) => void;
  setReconnectAttempts: (attempts: number) => void;
}

export const useWsStore = create<WsState>((set) => ({
  isConnected: false,
  isConnecting: false,
  lastError: null,
  reconnectAttempts: 0,

  setConnected: (connected) =>
    set((state) => ({
      isConnected: connected,
      lastError: connected ? null : state.lastError,
    })),
  setConnecting: (connecting) => set({ isConnecting: connecting }),
  setError: (error) => set({ lastError: error, isConnected: false }),
  setReconnectAttempts: (attempts) => set({ reconnectAttempts: attempts }),
}));
