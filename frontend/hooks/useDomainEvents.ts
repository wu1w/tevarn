'use client';

/**
 * 领域事件：
 * - AppShell 的 DomainEventBridge 使用 useDomainEventsOwner 建立唯一连接
 * - 页面只用 useDomainEvents() 读 store（不再自建 WS）
 */

import { useEffect } from 'react';
import { useDomainEventStore } from '@/stores/domainEventStore';
import { useAuthStore } from '@/stores/authStore';

function resolveWsBase(): string {
  if (typeof window === 'undefined') return 'ws://127.0.0.1:8090/api';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/api`;
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
