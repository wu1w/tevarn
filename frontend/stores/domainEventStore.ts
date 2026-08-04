/**
 * 领域事件消费者（OS 化：UI 订 Kernel 广播，而非只靠轮询）。
 * Snapshot on connect + live domain_event.
 * P1：旧 socket onclose 不得抹掉新连接；断线自动重连。
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
  _gen: number;
  _reconnectTimer: ReturnType<typeof setTimeout> | null;
  _reconnectAttempts: number;
  _opts: { wsBase: string; token: string } | null;
  connect: (opts: { wsBase: string; token: string }) => void;
  disconnect: () => void;
  pushLocal: (e: DomainEvent) => void;
};

const MAX = 80;

// audit-fix: 重连退避参数（风格对齐 hooks/useWebSocket.ts）
const RECONNECT_DELAY_BASE = 1000;
const MAX_RECONNECT_DELAY = 30000;
/** 连续失败上限；达到后转 60s 慢试 */
const MAX_FAST_RECONNECT_ATTEMPTS = 10;
const SLOW_RECONNECT_DELAY = 60000;
/** 后端鉴权失败关闭码：不再重连 */
const WS_CLOSE_AUTH_FAILED = 4401;

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
  _gen: 0,
  _reconnectTimer: null,
  _reconnectAttempts: 0,
  _opts: null,

  pushLocal: (e) => {
    set((s) => ({
      events: pushCapped(s.events, e),
      lastTopic: e.topic,
    }));
  },

  disconnect: () => {
    const st = get();
    if (st._reconnectTimer) {
      clearTimeout(st._reconnectTimer);
    }
    const ws = st._ws;
    // 抬 gen，使旧 socket 的 onclose 失效
    set({ _ws: null, connected: false, _reconnectTimer: null, _gen: st._gen + 1, _opts: null });
    if (ws) {
      try {
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        ws.close();
      } catch {
        /* ignore */
      }
    }
  },

  connect: ({ wsBase, token }) => {
    const prev = get();
    if (prev._reconnectTimer) {
      clearTimeout(prev._reconnectTimer);
    }
    // 关掉旧连接，但用 gen 防止其 onclose 清掉新状态
    const oldWs = prev._ws;
    const gen = prev._gen + 1;
    set({
      _ws: null,
      connected: false,
      error: null,
      _gen: gen,
      _opts: { wsBase, token },
      _reconnectTimer: null,
    });
    if (oldWs) {
      try {
        oldWs.onclose = null;
        oldWs.onerror = null;
        oldWs.onmessage = null;
        oldWs.close();
      } catch {
        /* ignore */
      }
    }
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

    ws.onopen = () => {
      if (get()._gen !== gen || get()._ws !== ws) return;
      set({ connected: true, error: null, _reconnectAttempts: 0 });
    };
    ws.onclose = (ev) => {
      // 仅本代 socket 才更新；避免旧连接抹掉新连接
      if (get()._gen !== gen || get()._ws !== ws) return;
      set({ connected: false, _ws: null });
      // audit-fix: 鉴权失败（4401）不再重连，避免重连风暴
      if (ev?.code === WS_CLOSE_AUTH_FAILED) {
        set({ error: 'auth failed (4401), reconnect stopped', _reconnectAttempts: 0 });
        return;
      }
      // 自动重连（有 opts 时）
      const opts = get()._opts;
      if (!opts?.token) return;
      // audit-fix: 指数退避 1s→2s→…→30s 封顶；连续失败 10 次后转 60s 慢试
      const attempts = get()._reconnectAttempts;
      const delay =
        attempts >= MAX_FAST_RECONNECT_ATTEMPTS
          ? SLOW_RECONNECT_DELAY
          : Math.min(RECONNECT_DELAY_BASE * Math.pow(2, attempts), MAX_RECONNECT_DELAY);
      set({ _reconnectAttempts: attempts + 1 });
      const timer = setTimeout(() => {
        if (get()._gen !== gen) return;
        const o = get()._opts;
        if (o?.token) get().connect(o);
      }, delay);
      set({ _reconnectTimer: timer });
    };
    ws.onerror = () => {
      if (get()._gen !== gen || get()._ws !== ws) return;
      set({ error: 'ws error', connected: false });
    };
    ws.onmessage = (ev) => {
      if (get()._gen !== gen || get()._ws !== ws) return;
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
