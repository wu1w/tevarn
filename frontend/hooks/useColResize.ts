'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  clampColWidth,
  readStoredWidth,
  widthFromDrag,
  writeStoredWidth,
  type ColEdge,
} from '@/lib/colResize';

export function useColResize(opts: {
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number | (() => number);
  edge: ColEdge;
}) {
  const { storageKey, defaultWidth, min, max, edge } = opts;
  const [width, setWidth] = useState(defaultWidth);
  const drag = useRef({ startX: 0, startW: defaultWidth, active: false });
  const widthRef = useRef(width);
  widthRef.current = width;

  const resolveMax = useCallback(() => {
    const hi = typeof max === 'function' ? max() : max;
    return Math.max(min, hi);
  }, [max, min]);

  useEffect(() => {
    setWidth(readStoredWidth(storageKey, defaultWidth, min));
  }, [storageKey, defaultWidth, min]);

  const onStart = useCallback(
    (clientX: number) => {
      drag.current = {
        startX: clientX,
        startW: widthRef.current,
        active: true,
      };
    },
    [],
  );

  const onDrag = useCallback(
    (clientX: number) => {
      if (!drag.current.active) onStart(clientX);
      const next = widthFromDrag({
        startX: drag.current.startX,
        startW: drag.current.startW,
        clientX,
        edge,
      });
      setWidth(clampColWidth(next, min, resolveMax()));
    },
    [edge, min, onStart, resolveMax],
  );

  const onEnd = useCallback(() => {
    drag.current.active = false;
    setWidth((w) => {
      const clamped = clampColWidth(w, min, resolveMax());
      writeStoredWidth(storageKey, clamped);
      return clamped;
    });
  }, [min, resolveMax, storageKey]);

  const onReset = useCallback(() => {
    drag.current.active = false;
    setWidth(defaultWidth);
    writeStoredWidth(storageKey, null);
  }, [defaultWidth, storageKey]);

  return { width, setWidth, onStart, onDrag, onEnd, onReset };
}
