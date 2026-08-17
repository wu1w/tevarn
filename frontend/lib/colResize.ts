/** Column-resize math shared by shell / dock / preview / drawers. */

export type ColEdge = 'left' | 'right';

export const RAIL_W = 56;
export const CHAT_MIN_W = 280;

export function clampColWidth(px: number, min: number, max: number): number {
  if (!Number.isFinite(px)) return min;
  return Math.round(Math.min(max, Math.max(min, px)));
}

export function widthFromDrag(opts: {
  startX: number;
  startW: number;
  clientX: number;
  edge: ColEdge;
}): number {
  const delta =
    opts.edge === 'left' ? opts.startX - opts.clientX : opts.clientX - opts.startX;
  return opts.startW + delta;
}

/** Drag well past min → snap closed (Cursor / VS Code). */
export function shouldSnapCollapse(px: number, min: number, slack = 32): boolean {
  return px < min - slack;
}

export function readSidebarTrackPx(): number {
  if (typeof document === 'undefined') return 260;
  const collapsed = document.querySelector('.tk-app-body.sidebar-collapsed');
  if (collapsed) return 0;
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--tk-sb-w');
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : 260;
}

/**
 * Max width for a right-hand column so the chat column keeps CHAT_MIN_W.
 * `self` is the panel being resized; sibling asides in the same flex row count.
 */
export function maxRightPanelWidth(self: HTMLElement | null, chatMin = CHAT_MIN_W): number {
  if (typeof window === 'undefined') return 640;
  const vw = window.innerWidth;
  const sb = readSidebarTrackPx();
  let siblings = 0;
  const parent = self?.parentElement;
  if (parent) {
    for (const child of Array.from(parent.children)) {
      if (child === self || !(child instanceof HTMLElement)) continue;
      if (child.classList.contains('tk-resizer')) continue;
      if (child.classList.contains('chat-main-column')) continue;
      const pos = getComputedStyle(child).position;
      if (pos === 'fixed' || pos === 'absolute') continue;
      siblings += child.getBoundingClientRect().width;
    }
  }
  const hard = Math.min(720, Math.round(vw * 0.52));
  const room = vw - RAIL_W - sb - chatMin - siblings;
  return Math.max(240, Math.min(hard, room));
}

export function readStoredWidth(key: string, fallback: number, min: number): number {
  if (typeof window === 'undefined') return fallback;
  try {
    const n = Number(localStorage.getItem(key));
    if (Number.isFinite(n) && n >= min) return n;
  } catch {
    /* ignore */
  }
  return fallback;
}

export function writeStoredWidth(key: string, px: number | null): void {
  try {
    if (px == null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(px));
  } catch {
    /* ignore */
  }
}
