import { useRef, useCallback, useEffect, useState } from 'react';

/**
 * 通用动作锁 hook：防止函数在指定冷却时间内被重复触发。
 * 适用于按钮防抖、提交表单、创建资源等场景。
 *
 * 返回 [wrapped, locked]：
 * - wrapped 用 ref 做同步互斥（必须同步，state 更新要等下一次渲染才可见，
 *   连点两下会在同一帧内都读到旧值而双发）
 * - locked 用 state 暴露给 UI，才能真正驱动按钮禁用/加载态
 *
 * 此前 locked 直接返回 lockedRef.current —— ref 变化不触发重渲染，
 * 消费者拿到的永远是上一次渲染时的值（实际恒为 false），
 * 任何依赖它做禁用的按钮都不会亮起。
 */
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
          // 卸载后不再 setState，避免 React 警告
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
  // ref 做同步互斥；state 镜像一份供渲染读取。
  // 原实现 isCooling 只读 ref，冷却状态变化不触发重渲染，
  // JSX 里的禁用态永远不会更新。
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
