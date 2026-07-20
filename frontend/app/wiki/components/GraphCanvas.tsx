'use client';

import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { WikiEntity, WikiRelation } from '@/types';
import { getWikiGraph, getWikiEntities, createWikiEntity, updateWikiEntity, deleteWikiEntity, createWikiRelation, deleteWikiRelation, importWiki, previewWikiImport } from '@/lib/api';
import { useT } from '@/stores/localeStore';

const TYPE_COLORS: Record<string, string> = {
  person: '#22c55e',
  organization: '#ef4444',
  project: '#f97316',
  tech: '#06b6d4',
  concept: '#3b82f6',
  docs: '#a855f7',
  event: '#eab308',
  location: '#14b8a6',
  problem: '#f43f5e',
  solution: '#10b981',
};

const TYPE_LABELS: Record<string, string> = {
  person: 'memory.type.person',
  organization: 'wiki._e12',
  project: 'contextDash.scope.project',
  tech: 'wiki._e13',
  concept: 'wiki._e14',
  docs: 'contextDash.kind.doc',
  event: 'wiki._e15',
  location: 'wiki._e16',
  problem: 'wiki._e17',
  solution: 'wiki._e18',
};

const RELATION_TYPES = [
  'depends_on', 'part_of', 'uses', 'solves', 'related_to',
  'alternative_to', 'belongs_to', 'participates_in', 'authored_by', 'presents',
];

interface GraphNode {
  id: string;
  name: string;
  entity_type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

interface GraphLink {
  id: string;
  source: string;
  target: string;
  relation_type: string;
}

interface Props {
  entities: WikiEntity[];
  relations: WikiRelation[];
  selectedId: string | null;
  focusedId: string | null;
  onSelect: (id: string | null) => void;
  onFocus: (id: string | null) => void;
  onCreateRelation?: (sourceId: string, targetId: string) => void;
  onDeleteRelation?: (relationId: string) => void;
  onDeleteEntity?: (entityId: string) => void;
}

export default function GraphCanvas({
  entities,
  relations,
  selectedId,
  focusedId,
  onSelect,
  onFocus,
  onCreateRelation,
  onDeleteRelation,
  onDeleteEntity,
}: Props) {
  const t = useT();
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const linksRef = useRef<GraphLink[]>([]);
  const dimsRef = useRef({ w: 800, h: 600 });
  const dprRef = useRef(1);
  const animRef = useRef(0);
  const drawRafRef = useRef(0);
  const frameRef = useRef(0);
  const dragRef = useRef<string | null>(null);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const selectedRef = useRef(selectedId);
  const focusedRef = useRef(focusedId);
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const isPanningRef = useRef(false);
  const panStartRef = useRef({ x: 0, y: 0 });
  const relationStartRef = useRef<string | null>(null);
  const mouseRef = useRef({ x: 0, y: 0 });
  const activeListenersRef = useRef<{ move: ((ev: MouseEvent) => void) | null; up: (() => void) | null }>({ move: null, up: null });
  /** 密度：compact=更挤 / normal / sparse=更疏 */
  const [density, setDensity] = useState<'compact' | 'normal' | 'sparse'>('normal');
  const densityRef = useRef(density);
  densityRef.current = density;
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);
  const showEdgeLabelsRef = useRef(showEdgeLabels);
  showEdgeLabelsRef.current = showEdgeLabels;

  const entitySet = useMemo(() => new Set(entities.map((e) => e.id)), [entities]);
  const entityMap = useMemo(() => {
    const map = new Map<string, WikiEntity>();
    entities.forEach((e) => map.set(e.id, e));
    return map;
  }, [entities]);

  const neighborMap = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const r of relations) {
      if (!map.has(r.source_id)) map.set(r.source_id, new Set());
      if (!map.has(r.target_id)) map.set(r.target_id, new Set());
      map.get(r.source_id)!.add(r.target_id);
      map.get(r.target_id)!.add(r.source_id);
    }
    return map;
  }, [relations]);

  const focusedNeighborSet = useMemo(() => {
    const set = new Set<string>();
    if (!focusedId) return set;
    set.add(focusedId);
    const neighbors = neighborMap.get(focusedId);
    if (neighbors) neighbors.forEach((id) => set.add(id));
    return set;
  }, [focusedId, neighborMap]);

  useEffect(() => { selectedRef.current = selectedId; scheduleDraw(); }, [selectedId]);
  useEffect(() => { focusedRef.current = focusedId; scheduleDraw(); }, [focusedId]);

  const drawArrow = useCallback((ctx: CanvasRenderingContext2D, x: number, y: number, angle: number, color: string) => {
    const size = 7;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-size, -size / 2);
    ctx.lineTo(-size, size / 2);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = dprRef.current;
    const nodes = nodesRef.current;
    const links = linksRef.current;
    const currentSelected = selectedRef.current;
    const currentFocused = focusedRef.current;
    const focusedSet = currentFocused ? focusedNeighborSet : new Set<string>();

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.scale(dpr * zoomRef.current, dpr * zoomRef.current);
    ctx.translate(panRef.current.x, panRef.current.y);

    for (const link of links) {
      const a = nodes.find((n) => n.id === link.source);
      const b = nodes.find((n) => n.id === link.target);
      if (!a || !b) continue;

      const isActive = !currentFocused || (focusedSet.has(a.id) && focusedSet.has(b.id));
      const isSelected = currentSelected && (a.id === currentSelected || b.id === currentSelected);
      const opacity = currentFocused && !isActive ? 0.12 : 1;
      const color = isSelected ? '#a78bfa' : '#9ca3af';

      ctx.save();
      ctx.globalAlpha = opacity;
      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 2.2 : 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();

      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const angle = Math.atan2(dy, dx);
      const targetX = b.x - ((b.r + 3) / dist) * dx;
      const targetY = b.y - ((b.r + 3) / dist) * dy;
      drawArrow(ctx, targetX, targetY, angle, color);
      ctx.restore();
    }

    const edgeLabelMode = showEdgeLabelsRef.current;
    for (const link of links) {
      const a = nodes.find((n) => n.id === link.source);
      const b = nodes.find((n) => n.id === link.target);
      if (!a || !b) continue;
      const isActive = !currentFocused || (focusedSet.has(a.id) && focusedSet.has(b.id));
      if (!isActive) continue;
      const isSelected =
        !!currentSelected && (a.id === currentSelected || b.id === currentSelected);
      if (!edgeLabelMode && !isSelected && !currentFocused) continue;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const text = link.relation_type;
      ctx.save();
      ctx.font = '9px sans-serif';
      const tw = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(17,24,39,0.78)';
      ctx.beginPath();
      ctx.roundRect(mx - tw / 2 - 4, my - 6, tw + 8, 12, 4);
      ctx.fill();
      ctx.fillStyle = '#e5e7eb';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, mx, my + 0.5);
      ctx.restore();
    }

    for (const node of nodes) {
      const isSelected = node.id === currentSelected;
      const isFocused = node.id === currentFocused;
      const isActive = !currentFocused || focusedSet.has(node.id);
      const color = TYPE_COLORS[node.entity_type] || '#9ca3af';

      ctx.save();
      ctx.globalAlpha = isActive ? 1 : 0.25;

      if (isSelected || isFocused) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + 5, 0, Math.PI * 2);
        ctx.strokeStyle = isFocused ? '#f59e0b' : '#7c3aed';
        ctx.lineWidth = 2.5;
        ctx.setLineDash([4, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = 'white';
      ctx.stroke();

      ctx.fillStyle = 'white';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText((TYPE_LABELS[node.entity_type] || '?')[0], node.x, node.y - 1);

      const showName =
        isSelected ||
        isFocused ||
        zoomRef.current >= 0.85 ||
        nodes.length <= 18 ||
        densityRef.current === 'sparse';
      if (showName) {
        const maxLen = densityRef.current === 'compact' ? 6 : densityRef.current === 'sparse' ? 12 : 9;
        const label = node.name.length > maxLen ? node.name.slice(0, maxLen - 1) + '…' : node.name;
        const lw = ctx.measureText(label).width;
        ctx.fillStyle = 'rgba(0,0,0,0.45)';
        ctx.beginPath();
        ctx.roundRect(node.x - lw / 2 - 3, node.y + node.r + 5, lw + 6, 14, 3);
        ctx.fill();
        ctx.fillStyle = isSelected || isFocused ? '#f9fafb' : '#e5e7eb';
        ctx.font = `${isSelected || isFocused ? 600 : 500} 11px sans-serif`;
        ctx.fillText(label, node.x, node.y + node.r + 12);
      }
      ctx.restore();
    }

    ctx.restore();
  }, [drawArrow, focusedNeighborSet]);

  const scheduleDraw = useCallback(() => {
    if (drawRafRef.current) return;
    drawRafRef.current = requestAnimationFrame(() => {
      drawRafRef.current = 0;
      draw();
    });
  }, [draw]);

  const resizeCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.max(1, Math.floor(window.devicePixelRatio || 1));
    dprRef.current = dpr;
    const { w, h } = dimsRef.current;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
  }, []);

  const startSimulation = useCallback(() => {
    const simulate = () => {
      const nodes = nodesRef.current;
      if (nodes.length === 0) return;
      const { w, h } = dimsRef.current;
      const cx = w / 2;
      const cy = h / 2;
      const links = linksRef.current;
      const dens = densityRef.current;
      const charge = dens === 'compact' ? 1800 : dens === 'sparse' ? 5200 : 3200;
      const linkLen = dens === 'compact' ? 90 : dens === 'sparse' ? 190 : 140;
      const linkStrength = dens === 'compact' ? 0.018 : dens === 'sparse' ? 0.008 : 0.012;
      const centerK = dens === 'compact' ? 0.0008 : dens === 'sparse' ? 0.00025 : 0.00045;
      const damp = dens === 'compact' ? 0.9 : 0.93;
      const collPad = dens === 'compact' ? 4 : dens === 'sparse' ? 14 : 8;
      const n = Math.max(nodes.length, 1);
      const chargeScale = charge * (1 + Math.min(n, 80) / 120);

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
          const minDist = a.r + b.r + collPad;
          if (dist < minDist) {
            const push = (minDist - dist) * 0.08;
            const nx = dx / dist;
            const ny = dy / dist;
            a.vx -= nx * push;
            a.vy -= ny * push;
            b.vx += nx * push;
            b.vy += ny * push;
            dist = minDist;
          }
          const force = chargeScale / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      for (const link of links) {
        const a = nodes.find((nd) => nd.id === link.source);
        const b = nodes.find((nd) => nd.id === link.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - linkLen) * linkStrength;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      for (const node of nodes) {
        node.vx += (cx - node.x) * centerK;
        node.vy += (cy - node.y) * centerK;
      }

      for (const node of nodes) {
        if (dragRef.current === node.id) continue;
        node.vx *= damp;
        node.vy *= damp;
        node.x += node.vx;
        node.y += node.vy;
        const pad = node.r + 16;
        node.x = Math.max(pad, Math.min(w - pad, node.x));
        node.y = Math.max(pad, Math.min(h - pad, node.y));
      }

      frameRef.current++;
      scheduleDraw();
      if (frameRef.current < 360) {
        animRef.current = requestAnimationFrame(simulate);
      }
    };
    cancelAnimationFrame(animRef.current);
    animRef.current = requestAnimationFrame(simulate);
  }, [scheduleDraw]);

  const restartSimulation = useCallback(() => {
    frameRef.current = 0;
    startSimulation();
  }, [startSimulation]);

  useEffect(() => {
    return () => {
      const { move, up } = activeListenersRef.current;
      if (move) document.removeEventListener('mousemove', move);
      if (up) document.removeEventListener('mouseup', up);
      cancelAnimationFrame(animRef.current);
      cancelAnimationFrame(drawRafRef.current);
    };
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      dimsRef.current = { w: rect.width, h: rect.height };
      resizeCanvas();
      scheduleDraw();
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener('resize', update);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [resizeCanvas, scheduleDraw]);

  useEffect(() => {
    const existing = new Map(nodesRef.current.map((n) => [n.id, n]));
    const { w, h } = dimsRef.current;
    const cx = w / 2;
    const cy = h / 2;
    const dens = densityRef.current;
    const baseR = dens === 'compact' ? 20 : dens === 'sparse' ? 28 : 24;
    const ring = Math.min(w, h) * (dens === 'compact' ? 0.28 : dens === 'sparse' ? 0.42 : 0.35);
    const ringScale = ring * (1 + Math.min(entities.length, 60) / 100);

    nodesRef.current = entities.map((e, i) => {
      const old = existing.get(e.id);
      if (old) return { ...old, name: e.name, entity_type: e.entity_type, r: baseR };
      const angle = (i / Math.max(entities.length, 1)) * Math.PI * 2 + (i % 3) * 0.05;
      const jitter = ((i * 17) % 7) - 3;
      return {
        id: e.id,
        name: e.name,
        entity_type: e.entity_type,
        x: cx + Math.cos(angle) * (ringScale + jitter),
        y: cy + Math.sin(angle) * (ringScale + jitter),
        vx: 0,
        vy: 0,
        r: baseR,
      };
    });

    linksRef.current = relations.map((r) => ({
      id: r.id,
      source: r.source_id,
      target: r.target_id,
      relation_type: r.relation_type,
    }));

    frameRef.current = 0;
    startSimulation();
  }, [entities, relations, startSimulation]);

  const toWorld = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left - panRef.current.x) / zoomRef.current,
      y: (clientY - rect.top - panRef.current.y) / zoomRef.current,
    };
  }, []);

  const findNodeAt = useCallback((x: number, y: number) => {
    for (let i = nodesRef.current.length - 1; i >= 0; i--) {
      const node = nodesRef.current[i];
      const dx = x - node.x;
      const dy = y - node.y;
      if (Math.sqrt(dx * dx + dy * dy) <= node.r + 2) return node;
    }
    return null;
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const pos = toWorld(e.clientX, e.clientY);
    const node = findNodeAt(pos.x, pos.y);
    mouseRef.current = { x: e.clientX, y: e.clientY };

    if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
      isPanningRef.current = true;
      panStartRef.current = { x: e.clientX - panRef.current.x, y: e.clientY - panRef.current.y };
      return;
    }

    if (relationStartRef.current) {
      if (node && node.id !== relationStartRef.current) {
        onCreateRelation?.(relationStartRef.current, node.id);
      }
      relationStartRef.current = null;
      scheduleDraw();
      return;
    }

    if (!node) {
      onSelect(null);
      onFocus(null);
      return;
    }

    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {
      relationStartRef.current = node.id;
      return;
    }

    dragRef.current = node.id;
    dragOffsetRef.current = { x: pos.x - node.x, y: pos.y - node.y };
    onSelect(node.id);

    const handleMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const n = nodesRef.current.find((x) => x.id === dragRef.current);
      if (!n) return;
      const p = toWorld(ev.clientX, ev.clientY);
      n.x = p.x - dragOffsetRef.current.x;
      n.y = p.y - dragOffsetRef.current.y;
      n.vx = 0;
      n.vy = 0;
      scheduleDraw();
    };

    const handleUp = () => {
      dragRef.current = null;
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      activeListenersRef.current = { move: null, up: null };
      restartSimulation();
    };

    activeListenersRef.current = { move: handleMove, up: handleUp };
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [toWorld, findNodeAt, onSelect, onFocus, onCreateRelation, scheduleDraw, restartSimulation]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const oldZoom = zoomRef.current;
    const newZoom = Math.max(0.3, Math.min(3, oldZoom * delta));
    // Zoom toward cursor: keep the world point under the cursor fixed
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const worldX = (e.clientX - rect.left - panRef.current.x) / oldZoom;
    const worldY = (e.clientY - rect.top - panRef.current.y) / oldZoom;
    panRef.current.x = e.clientX - rect.left - worldX * newZoom;
    panRef.current.y = e.clientY - rect.top - worldY * newZoom;
    zoomRef.current = newZoom;
    scheduleDraw();
  }, [scheduleDraw]);

  const handleDoubleClick = useCallback((e: React.MouseEvent) => {
    const pos = toWorld(e.clientX, e.clientY);
    const node = findNodeAt(pos.x, pos.y);
    if (node) {
      onFocus(node.id === focusedId ? null : node.id);
    } else {
      zoomRef.current = 1;
      panRef.current = { x: 0, y: 0 };
      onFocus(null);
      scheduleDraw();
    }
  }, [toWorld, findNodeAt, onFocus, focusedId, scheduleDraw]);

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    const pos = toWorld(e.clientX, e.clientY);
    const node = findNodeAt(pos.x, pos.y);
    if (node) {
      e.preventDefault();
      if (confirm(`删除实体「${node.name}」？相关关系也会一并删除。`)) {
        onDeleteEntity?.(node.id);
      }
    }
  }, [toWorld, findNodeAt, onDeleteEntity]);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      <canvas
        ref={canvasRef}
        className="cursor-grab active:cursor-grabbing block"
        onMouseDown={handleMouseDown}
        onWheel={handleWheel}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
      />
      <div className="absolute top-3 left-3 z-10 flex flex-wrap items-center gap-1.5 rounded-xl border border-border-default bg-card-bg/90 p-1.5 shadow-sm backdrop-blur">
        {([
          ['compact', t('wiki._e48')],
          ['normal', t('wiki._e49')],
          ['sparse', t('wiki._e50')],
        ] as const).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => {
              setDensity(k);
              requestAnimationFrame(() => {
                frameRef.current = 0;
                const baseR = k === 'compact' ? 20 : k === 'sparse' ? 28 : 24;
                for (const n of nodesRef.current) n.r = baseR;
                startSimulation();
              });
            }}
            className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors ${
              density === k
                ? 'bg-brand-purple text-white'
                : 'text-foreground-muted hover:bg-elevated-bg hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
        <span className="mx-0.5 h-4 w-px bg-border-subtle" />
        <button
          type="button"
          onClick={() => {
            setShowEdgeLabels((v) => !v);
            scheduleDraw();
          }}
          className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors ${
            showEdgeLabels
              ? 'bg-brand-cyan/20 text-brand-cyan'
              : 'text-foreground-muted hover:bg-elevated-bg hover:text-foreground'
          }`}
          title={t('wiki._e1')}
        >
          边标签
        </button>
        <button
          type="button"
          onClick={() => {
            frameRef.current = 0;
            startSimulation();
          }}
          className="rounded-lg px-2.5 py-1 text-[11px] font-medium text-foreground-muted hover:bg-elevated-bg hover:text-foreground"
          title={t('wiki._e2')}
        >
          重排
        </button>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border-default bg-card-bg/90 px-3 py-2 shadow-sm backdrop-blur">
        <div className="mb-1 text-[10px] font-semibold text-foreground-dim">{t('wiki._e3')}</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          {Object.entries(TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[10px] text-foreground-dim">{TYPE_LABELS[type]}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="pointer-events-none absolute bottom-3 right-3 rounded-lg border border-border-default bg-card-bg/90 px-3 py-2 shadow-sm backdrop-blur text-[10px] text-foreground-dim space-y-0.5">
        <div>滚轮缩放 · 拖拽节点</div>
        <div>Shift+拖拽 平移 · 双击聚焦</div>
        <div>Ctrl/⌘ 点击 新建关系到目标</div>
        <div>{t('wiki._e4')}</div>
      </div>
      {relationStartRef.current && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 rounded-full bg-brand-purple px-3 py-1 text-xs text-white shadow">
          请选择目标实体建立关系
        </div>
      )}
    </div>
  );
}
