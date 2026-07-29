/**
 * 领域事件消费者（OS 化：UI 订 Kernel 广播，而非只靠轮询）。
 * Snapshot on connect + live domain_event.
 */

import { create } from 'zustand';

export type DomainEvent = {
  type?: string;
  topic: string;
  ts: number;
  data?: Record<string, unknown>;
};

type State = {
  connected: boolean;
  events: DomainEvent[];
  lastTopic: string | null;
  error: string | null;
  _ws: WebSocket | null;
  connect: (opts: { wsBase: string; token: string }) => void;
  disconnect: () => void;
  pushLocal: (e: DomainEvent) => void;
};

const MAX = 80;

function pushCapped(list: DomainEvent[], e: DomainEvent): DomainEvent[] {
  const next = [...list, e];
  return next.length > MAX ? next.slice(next.length - MAX) : next;
}

export const useDomainEventStore = create<State>((set, get) => ({
  connected: false,
  events: [],
  lastTopic: null,
  error: null,
  _ws: null,

  pushLocal: (e) => {
    set((s) => ({
      events: pushCapped(s.events, e),
      lastTopic: e.topic,
    }));
  },

  disconnect: () => {
    const ws = get()._ws;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    set({ _ws: null, connected: false });
  },

  connect: ({ wsBase, token }) => {
    get().disconnect();
    if (!token) {
      set({ error: 'no token', connected: false });
      return;
    }
    const base = wsBase.replace(/\/$/, '');
    const url = `${base}/ws/domain?token=${encodeURIComponent(token)}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      set({ error: String(e), connected: false });
      return;
    }
    set({ _ws: ws, error: null });

    ws.onopen = () => set({ connected: true, error: null });
    ws.onclose = () => set({ connected: false, _ws: null });
    ws.onerror = () => set({ error: 'ws error', connected: false });
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data)) as Record<string, unknown>;
        if (msg.type === 'domain_snapshot' && Array.isArray(msg.events)) {
          const events = (msg.events as DomainEvent[]).slice(-MAX);
          set({
            events,
            lastTopic: events.length ? events[events.length - 1].topic : null,
          });
          return;
        }
        if (msg.type === 'domain_event' || msg.topic) {
          const e: DomainEvent = {
            type: 'domain_event',
            topic: String(msg.topic || ''),
            ts: Number(msg.ts) || Date.now() / 1000,
            data: (msg.data as Record<string, unknown>) || {},
          };
          set((s) => ({
            events: pushCapped(s.events, e),
            lastTopic: e.topic,
          }));
          return;
        }
        if (msg.type === 'ping') {
          try {
            ws.send(JSON.stringify({ type: 'pong' }));
          } catch {
            /* ignore */
          }
        }
      } catch {
        /* ignore */
      }
    };
  },
}));
