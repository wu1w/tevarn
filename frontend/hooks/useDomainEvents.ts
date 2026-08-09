'use client';

/**
 * 领域事件：
 * - AppShell 的 DomainEventBridge 使用 useDomainEventsOwner 建立唯一连接
 * - 页面只用 useDomainEvents() 读 store（不再自建 WS）
 */

import { useEffect } from 'react';
import { useDomainEventStore } from '@/stores/domainEventStore';
import { useAuthStore } from '@/stores/authStore';

/**
 * 与 useWebSocket 对齐：浏览器 next dev (:3000/:3001) 下 Next rewrites
 * 不支持 WebSocket upgrade，必须直连后端 8090，否则会 ECONNRESET / 控制台刷屏。
 */
function resolveWsBase(): string {
  if (typeof window === 'undefined') return 'ws://127.0.0.1:8090/api';

  const { hostname, port, protocol } = window.location;
  const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost';
  const hasElectron = Boolean(
    (window as unknown as { electronAPI?: unknown }).electronAPI
  );

  // Next dev：页面在 3000，必须直连 8090；忽略被 TEVARN_APP_PORT=8000 污染的发现结果
  if (isLocalHost && (port === '3000' || port === '3001') && !hasElectron) {
    return 'ws://127.0.0.1:8090/api';
  }

  const injected = (window as unknown as { __TEVARN_WS_URL__?: string }).__TEVARN_WS_URL__;
  if (injected) {
    const u = injected.replace(/\/$/, '');
    if (/:\/\/(127\.0\.0\.1|localhost):8000(\/|$)/i.test(u) && isLocalHost) {
      return 'ws://127.0.0.1:8090/api';
    }
    return u;
  }

  // Electron：走同源反代
  if (hasElectron) {
    const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
    const host = port ? `${hostname}:${port}` : hostname;
    return `${wsProto}//${host}/api`;
  }

  if (process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL.replace(/\/$/, '');
  }

  const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
  const host = port ? `${hostname}:${port}` : hostname;
  return `${wsProto}//${host}/api`;
}

/** 页面只读：不管理连接生命周期 */
export function useDomainEvents(_enabled = true) {
  const connected = useDomainEventStore((s) => s.connected);
  const events = useDomainEventStore((s) => s.events);
  const lastTopic = useDomainEventStore((s) => s.lastTopic);
  return { connected, events, lastTopic };
}

/** 仅 AppShell DomainEventBridge：拥有 connect/disconnect */
export function useDomainEventsOwner(enabled = true) {
  const token = useAuthStore((s) => s.token);
  const connect = useDomainEventStore((s) => s.connect);
  const disconnect = useDomainEventStore((s) => s.disconnect);
  const connected = useDomainEventStore((s) => s.connected);
  const events = useDomainEventStore((s) => s.events);
  const lastTopic = useDomainEventStore((s) => s.lastTopic);

  useEffect(() => {
    if (!enabled || !token) {
      disconnect();
      return;
    }
    connect({ wsBase: resolveWsBase(), token });
    return () => disconnect();
  }, [enabled, token, connect, disconnect]);

  return { connected, events, lastTopic };
}
