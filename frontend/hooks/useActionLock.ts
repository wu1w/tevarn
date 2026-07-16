import { useRef, useCallback, useEffect } from 'react';

/**
 * 通用动作锁 hook：防止函数在指定冷却时间内被重复触发。
 * 适用于按钮防抖、提交表单、创建资源等场景。
 */
export function useActionLock<T extends (...args: any[]) => any>(
  fn: T,
  cooldownMs: number = 500
): [(...args: Parameters<T>) => Promise<ReturnType<T> | undefined>, boolean] {
  const lockedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const wrapped = useCallback(
    async (...args: Parameters<T>) => {
      if (lockedRef.current) return undefined;
      lockedRef.current = true;
      try {
        return await fn(...args);
      } finally {
        timerRef.current = setTimeout(() => {
          lockedRef.current = false;
        }, cooldownMs);
      }
    },
    [fn, cooldownMs]
  );

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return [wrapped, lockedRef.current];
}

/**
 * 局部冷却 hook：用于单个组件内的多个按钮。
 */
export function useLocalCooldown() {
  const idsRef = useRef<Set<string>>(new Set());

  const isCooling = (id: string) => idsRef.current.has(id);

  const run = useCallback(
    async <T extends (...args: any[]) => any>(id: string, fn: T, cooldownMs = 500): Promise<ReturnType<T> | undefined> => {
      if (idsRef.current.has(id)) return undefined;
      idsRef.current.add(id);
      try {
        return await fn();
      } finally {
        setTimeout(() => idsRef.current.delete(id), cooldownMs);
      }
    },
    []
  );

  return { isCooling, run };
}
