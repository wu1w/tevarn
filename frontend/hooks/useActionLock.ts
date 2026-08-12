import { useRef, useCallback, useEffect, useState } from 'react';

/**
 * 通用动作锁 hook：防止函数在指定冷却时间内被重复触发。
 * 适用于按钮防抖、提交表单、创建资源等场景。
 *
 * 返回 [wrapped, locked]：
 * - wrapped 用 ref 做同步互斥（必须同步，state 更新要等下一次渲染才可见，
 *   连点两下会在同一帧内都读到旧值而双发）
 * - locked 用 state 暴露给 UI，才能真正驱动按钮禁用/加载态
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useActionLock<T extends (...args: any[]) => any>(
  fn: T,
  cooldownMs: number = 500
): [(...args: Parameters<T>) => Promise<ReturnType<T> | undefined>, boolean] {
  const lockedRef = useRef(false);
  const [locked, setLocked] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const wrapped = useCallback(
    async (...args: Parameters<T>) => {
      if (lockedRef.current) return undefined;
      lockedRef.current = true;
      if (mountedRef.current) setLocked(true);
      try {
        return await fn(...args);
      } finally {
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => {
          lockedRef.current = false;
          if (mountedRef.current) setLocked(false);
        }, cooldownMs);
      }
    },
    [fn, cooldownMs]
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return [wrapped, locked];
}

/**
 * 局部冷却 hook：用于单个组件内的多个按钮。
 */
export function useLocalCooldown() {
  const idsRef = useRef<Set<string>>(new Set());
  const [coolingIds, setCoolingIds] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const sync = useCallback(() => {
    if (mountedRef.current) setCoolingIds(new Set(idsRef.current));
  }, []);

  const isCooling = useCallback(
    (id: string) => coolingIds.has(id),
    [coolingIds]
  );

  const run = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async <T extends (...args: any[]) => any>(id: string, fn: T, cooldownMs = 500): Promise<ReturnType<T> | undefined> => {
      if (idsRef.current.has(id)) return undefined;
      idsRef.current.add(id);
      sync();
      try {
        return await fn();
      } finally {
        setTimeout(() => {
          idsRef.current.delete(id);
          sync();
        }, cooldownMs);
      }
    },
    [sync]
  );

  return { isCooling, run };
}
