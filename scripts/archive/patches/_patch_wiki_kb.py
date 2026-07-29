from pathlib import Path

path = Path(r"E:/项目/taktonl-0.1.0/frontend/app/wiki/components/GraphCanvas.tsx")
text = path.read_text(encoding="utf-8")

def must_replace(old: str, new: str, label: str) -> str:
    global text
    if old not in text:
        raise SystemExit(f"miss: {label}")
    text = text.replace(old, new, 1)
    print("ok", label)

must_replace(
    """  const activeListenersRef = useRef<{ move: ((ev: MouseEvent) => void) | null; up: (() => void) | null }>({ move: null, up: null });

  const entitySet = useMemo""",
    """  const activeListenersRef = useRef<{ move: ((ev: MouseEvent) => void) | null; up: (() => void) | null }>({ move: null, up: null });
  /** 密度：compact=更挤 / normal / sparse=更疏 */
  const [density, setDensity] = useState<'compact' | 'normal' | 'sparse'>('normal');
  const densityRef = useRef(density);
  densityRef.current = density;
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);
  const showEdgeLabelsRef = useRef(showEdgeLabels);
  showEdgeLabelsRef.current = showEdgeLabels;

  const entitySet = useMemo""",
    "density state",
)

must_replace(
    """  const startSimulation = useCallback(() => {
    const simulate = () => {
      const nodes = nodesRef.current;
      if (nodes.length === 0) return;
      const { w, h } = dimsRef.current;
      const cx = w / 2;
      const cy = h / 2;
      const links = linksRef.current;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = 2500 / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      for (const link of links) {
        const a = nodes.find((n) => n.id === link.source);
        const b = nodes.find((n) => n.id === link.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 130) * 0.01;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      for (const node of nodes) {
        node.vx += (cx - node.x) * 0.0004;
        node.vy += (cy - node.y) * 0.0004;
      }

      for (const node of nodes) {
        if (dragRef.current === node.id) continue;
        node.vx *= 0.92;
        node.vy *= 0.92;
        node.x += node.vx;
        node.y += node.vy;
        const pad = node.r + 12;
        node.x = Math.max(pad, Math.min(w - pad, node.x));
        node.y = Math.max(pad, Math.min(h - pad, node.y));
      }

      frameRef.current++;
      scheduleDraw();
      if (frameRef.current < 300) {
        animRef.current = requestAnimationFrame(simulate);
      }
    };
    cancelAnimationFrame(animRef.current);
    animRef.current = requestAnimationFrame(simulate);
  }, [scheduleDraw]);
""",
    """  const startSimulation = useCallback(() => {
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
""",
    "simulation",
)

must_replace(
    """    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * 0.35;

    nodesRef.current = entities.map((e, i) => {
      const old = existing.get(e.id);
      if (old) return { ...old, name: e.name, entity_type: e.entity_type };
      const angle = (i / Math.max(entities.length, 1)) * Math.PI * 2 + Math.random() * 0.2;
      return {
        id: e.id,
        name: e.name,
        entity_type: e.entity_type,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        r: 26,
      };
    });
""",
    """    const cx = w / 2;
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
""",
    "init layout",
)

must_replace(
    """    for (const link of links) {
      const a = nodes.find((n) => n.id === link.source);
      const b = nodes.find((n) => n.id === link.target);
      if (!a || !b) continue;
      const isActive = !currentFocused || (focusedSet.has(a.id) && focusedSet.has(b.id));
      if (!isActive) continue;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const text = link.relation_type;
      ctx.save();
      ctx.font = '9px sans-serif';
      const w = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(255,255,255,0.92)';
      ctx.beginPath();
      ctx.roundRect(mx - w / 2 - 4, my - 6, w + 8, 12, 4);
      ctx.fill();
      ctx.fillStyle = '#6b7280';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, mx, my + 0.5);
      ctx.restore();
    }
""",
    """    const edgeLabelMode = showEdgeLabelsRef.current;
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
""",
    "edge labels",
)

must_replace(
    """      ctx.fillStyle = isSelected || isFocused ? '#111827' : '#374151';
      ctx.font = `${isSelected || isFocused ? 600 : 500} 11px sans-serif`;
      const label = node.name.length > 9 ? node.name.slice(0, 8) + '…' : node.name;
      ctx.fillText(label, node.x, node.y + node.r + 13);
      ctx.restore();
""",
    """      const showName =
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
""",
    "node labels",
)

must_replace(
    """  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      <canvas
        ref={canvasRef}
        className="cursor-grab active:cursor-grabbing block"
        onMouseDown={handleMouseDown}
        onWheel={handleWheel}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
      />
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border-default bg-card-bg/90 px-3 py-2 shadow-sm backdrop-blur">
""",
    """  return (
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
          ['compact', '紧凑'],
          ['normal', '标准'],
          ['sparse', '宽松'],
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
          title="显示全部关系标签（默认仅选中/聚焦时显示）"
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
          title="重新排布"
        >
          重排
        </button>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-border-default bg-card-bg/90 px-3 py-2 shadow-sm backdrop-blur">
""",
    "toolbar ui",
)

path.write_text(text, encoding="utf-8")
print("GraphCanvas written", len(text))
