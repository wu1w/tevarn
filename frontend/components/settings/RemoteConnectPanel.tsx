'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  meshAuthMobile,
  meshSetMobile,
  meshStatusMobile,
  pairCancelMobile,
  pairConfirmMobile,
  pairDevicesMobile,
  pairRevokeMobile,
  pairStartMobile,
  pairStatusMobile,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useZh } from '@/hooks/useZh';

type ActivePair = {
  pair_id: string;
  code: string;
  qr: string;
  exp: number;
  ttl_secs: number;
  require_confirm?: boolean;
  mesh?: string;
  base_url?: string;
  lan?: string | null;
  ts?: string | null;
  seamless?: boolean;
  hint?: string;
  remaining_secs?: number;
  claimed?: boolean;
};

type MeshStatus = {
  mode?: string;
  lan_ip?: string | null;
  tailscale_ip?: string | null;
  auth_key_set?: boolean;
  detail?: string;
  hostname?: string;
  backend_port?: number;
};

async function renderQrToCanvas(canvas: HTMLCanvasElement, text: string): Promise<boolean> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod: any = await import('qrcode');
    const toCanvas = mod.toCanvas || mod.default?.toCanvas;
    if (toCanvas) {
      await toCanvas(canvas, text, {
        width: 200,
        margin: 2,
        color: { dark: '#0f172a', light: '#ffffff' },
      });
      return true;
    }
  } catch {
    /* package missing — show link fallback */
  }
  return false;
}

function QrView({ data }: { data: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!ref.current || !data) return;
      const drawn = await renderQrToCanvas(ref.current, data);
      if (!cancelled) setOk(drawn);
    })();
    return () => {
      cancelled = true;
    };
  }, [data]);

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="rounded-xl border border-border-subtle bg-white p-3 shadow-sm">
        <canvas
          ref={ref}
          width={200}
          height={200}
          className={ok ? 'block' : 'hidden'}
          aria-label="pairing-qr"
        />
        {!ok && (
          <div className="flex h-[200px] w-[200px] flex-col items-center justify-center gap-2 text-center text-xs text-slate-500">
            <span className="text-2xl font-semibold tracking-tight text-slate-800">QR</span>
            <span>请用下方链接或复制配对码</span>
          </div>
        )}
      </div>
    </div>
  );
}

export function RemoteConnectPanel() {
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const [mesh, setMesh] = useState<MeshStatus>({});
  const [pair, setPair] = useState<ActivePair | null>(null);
  const [devices, setDevices] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [authKey, setAuthKey] = useState('');
  const [showAuth, setShowAuth] = useState(false);
  const [meshMode, setMeshMode] = useState('auto');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshMesh = useCallback(async () => {
    try {
      const st = (await meshStatusMobile()) as MeshStatus;
      setMesh(st);
      if (st.mode) setMeshMode(String(st.mode));
    } catch {
      /* backend may be older */
    }
  }, []);

  const refreshDevices = useCallback(async () => {
    try {
      const r = await pairDevicesMobile();
      setDevices(r.devices || []);
    } catch {
      setDevices([]);
    }
  }, []);

  useEffect(() => {
    void refreshMesh();
    void refreshDevices();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refreshMesh, refreshDevices]);

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPoll = (pairId: string) => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      try {
        const st = await pairStatusMobile(pairId);
        setPair((prev) =>
          prev && prev.pair_id === pairId
            ? {
                ...prev,
                remaining_secs: st.remaining_secs,
                claimed: st.claimed,
                require_confirm: st.require_confirm,
              }
            : prev,
        );
        if (st.claimed) {
          stopPoll();
          addToast(zh ? '手机已配对成功' : 'Phone paired', 'success');
          setPair(null);
          void refreshDevices();
        } else if (st.remaining_secs <= 0) {
          stopPoll();
          setPair(null);
          addToast(zh ? '配对码已过期，请重新生成' : 'Pair code expired', 'info');
        }
      } catch {
        /* ignore transient */
      }
    }, 2000);
  };

  const onGenerate = async () => {
    setBusy(true);
    try {
      const r = await pairStartMobile({ mesh: meshMode || 'auto' });
      if (!r.ok || !r.qr) {
        addToast(r.error || (zh ? '无法生成配对码' : 'Failed to start pair'), 'error');
        return;
      }
      const active: ActivePair = {
        pair_id: r.pair_id,
        code: r.code,
        qr: r.qr,
        exp: r.exp,
        ttl_secs: r.ttl_secs,
        require_confirm: r.require_confirm,
        mesh: r.mesh,
        base_url: r.base_url,
        lan: r.lan,
        ts: r.ts,
        seamless: r.seamless,
        hint: r.hint,
        remaining_secs: r.ttl_secs,
      };
      setPair(active);
      startPoll(r.pair_id);
      addToast(r.hint || (zh ? '二维码已生成，请用手机扫描' : 'QR ready — scan with phone'), 'success');
      void refreshMesh();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!pair) return;
    try {
      await pairCancelMobile(pair.pair_id);
    } catch {
      /* ignore */
    }
    stopPoll();
    setPair(null);
  };

  const onConfirm = async () => {
    if (!pair) return;
    try {
      await pairConfirmMobile(pair.pair_id);
      addToast(zh ? '已允许此手机' : 'Phone allowed', 'success');
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : String(e), 'error');
    }
  };

  const onCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      addToast(zh ? '已复制' : 'Copied', 'success');
    } catch {
      addToast(zh ? '复制失败' : 'Copy failed', 'error');
    }
  };

  const onSaveAuth = async () => {
    setBusy(true);
    try {
      const r = await meshAuthMobile(authKey.trim());
      addToast(r.detail || (zh ? '已保存' : 'Saved'), 'success');
      setAuthKey('');
      setShowAuth(false);
      void refreshMesh();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const onMode = async (mode: string) => {
    setMeshMode(mode);
    try {
      await meshSetMobile({ mode });
      void refreshMesh();
    } catch {
      /* ignore */
    }
  };

  const modes = [
    { id: 'auto', zh: '自动（推荐）', en: 'Auto' },
    { id: 'lan', zh: '仅局域网', en: 'LAN only' },
    { id: 'ts', zh: '仅远程', en: 'Remote only' },
  ];

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
        <div className="mb-1 text-[13.5px] font-semibold text-foreground">
          {zh ? '远程连接 · 匹配手机' : 'Remote · Match phone'}
        </div>
        <p className="mb-4 text-[12.5px] leading-relaxed text-foreground-muted">
          {zh
            ? '在 PC 生成二维码，手机 App「连接」页扫描即可。在家走局域网，出门自动切换；无需公网 IP。'
            : 'Generate a QR on PC; scan in the phone app Connect tab. LAN at home, remote when away — no public IP needed.'}
        </p>

        {/* mesh status */}
        <div className="mb-4 rounded-xl border border-border-subtle bg-elevated-bg/60 px-3.5 py-3 text-[12.5px] text-foreground-muted">
          <div>{mesh.detail || (zh ? '检测网络…' : 'Detecting network…')}</div>
          <div className="mt-1 font-mono text-[11px] text-foreground-dim">
            {zh ? '局域网' : 'LAN'} {mesh.lan_ip || '—'}
            {' · '}
            {zh ? '外出' : 'Remote'} {mesh.tailscale_ip || (mesh.auth_key_set ? (zh ? '密钥已配置' : 'key set') : '—')}
            {mesh.backend_port ? ` · :${mesh.backend_port}` : ''}
          </div>
        </div>

        {/* mode chips */}
        <div className="mb-4 flex flex-wrap gap-2">
          {modes.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => void onMode(m.id)}
              className={`rounded-lg border px-3 py-1.5 text-[12px] font-medium transition ${
                meshMode === m.id
                  ? 'border-brand-purple/40 bg-brand-purple/15 text-foreground'
                  : 'border-border-subtle bg-card-bg text-foreground-muted hover:bg-card-bg-hover'
              }`}
            >
              {zh ? m.zh : m.en}
            </button>
          ))}
        </div>

        {/* one-time remote key */}
        {!mesh.auth_key_set ? (
          <div className="mb-4">
            <button
              type="button"
              className="text-[12px] font-medium text-brand-cyan hover:underline"
              onClick={() => setShowAuth((v) => !v)}
            >
              {showAuth
                ? zh
                  ? '收起 · 首次启用外出连接'
                  : 'Hide remote setup'
                : zh
                  ? '首次启用外出连接（只需一次）'
                  : 'Enable remote once (optional)'}
            </button>
            {showAuth && (
              <div className="mt-2 space-y-2">
                <p className="text-[11.5px] leading-relaxed text-foreground-dim">
                  {zh
                    ? '粘贴一次 Tailscale 访问密钥后，之后扫码即可在 5G 连回家。密钥仅保存在本机。'
                    : 'Paste a Tailscale auth key once so the phone can join your tailnet from cellular.'}
                </p>
                <input
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 font-mono text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
                  placeholder="tskey-auth-…"
                  value={authKey}
                  onChange={(e) => setAuthKey(e.target.value)}
                  type="password"
                  autoComplete="off"
                />
                <button
                  type="button"
                  disabled={busy || !authKey.trim()}
                  onClick={() => void onSaveAuth()}
                  className="inline-flex items-center rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {zh ? '启用远程' : 'Enable remote'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <p className="mb-4 text-[12px] text-brand-cyan">
            {zh ? '外出连接已启用 · 扫码即连' : 'Remote ready · scan to connect'}
          </p>
        )}

        {/* QR / actions */}
        {pair ? (
          <div className="space-y-3">
            <QrView data={pair.qr} />
            <div className="text-center font-mono text-[12px] text-brand-cyan">
              {zh ? '配对码' : 'Code'} {pair.code} · {pair.remaining_secs ?? pair.ttl_secs}s
            </div>
            {pair.hint && (
              <p className="text-center text-[12px] text-foreground-muted">{pair.hint}</p>
            )}
            <div className="rounded-xl border border-border-subtle bg-elevated-bg/50 px-3 py-2">
              <div className="mb-1 text-[11px] text-foreground-dim">
                {zh ? '配对链接（可复制到手机）' : 'Pair link'}
              </div>
              <div className="break-all font-mono text-[11px] text-foreground-muted">{pair.qr}</div>
            </div>
            <div className="flex flex-wrap gap-2">
              {pair.require_confirm && (
                <button
                  type="button"
                  onClick={() => void onConfirm()}
                  className="inline-flex flex-1 items-center justify-center rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2.5 text-sm font-medium text-white"
                >
                  {zh ? '允许手机' : 'Allow phone'}
                </button>
              )}
              <button
                type="button"
                onClick={() => void onCopy(pair.qr)}
                className="inline-flex flex-1 items-center justify-center rounded-xl border border-border-default bg-card-bg px-4 py-2.5 text-sm text-foreground-muted hover:bg-card-bg-hover"
              >
                {zh ? '复制链接' : 'Copy link'}
              </button>
              <button
                type="button"
                onClick={() => void onCancel()}
                className="inline-flex flex-1 items-center justify-center rounded-xl border border-border-default bg-card-bg px-4 py-2.5 text-sm text-foreground-muted hover:bg-card-bg-hover"
              >
                {zh ? '取消' : 'Cancel'}
              </button>
            </div>
            <p className="text-center text-[11px] text-foreground-dim">
              {zh
                ? '手机打开 Takton → 连接 → 扫描二维码'
                : 'Phone: Takton → Connect → Scan QR'}
            </p>
          </div>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onGenerate()}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:opacity-95 disabled:opacity-50"
          >
            {busy
              ? zh
                ? '生成中…'
                : 'Generating…'
              : zh
                ? '匹配手机 · 生成二维码'
                : 'Match phone · Generate QR'}
          </button>
        )}
      </section>

      {devices.length > 0 && (
        <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
          <div className="mb-3 text-[13.5px] font-semibold text-foreground">
            {zh ? '已配对手机' : 'Paired phones'}
          </div>
          <ul className="divide-y divide-border-subtle">
            {devices.map((d) => {
              const id = String(d.id || '');
              const name = String(d.name || 'device');
              const base = String(d.base_url || '');
              return (
                <li key={id} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium text-foreground">{name}</div>
                    <div className="truncate font-mono text-[11px] text-foreground-dim">{base}</div>
                  </div>
                  <button
                    type="button"
                    className="shrink-0 text-[12px] font-medium text-warning-text hover:underline"
                    onClick={async () => {
                      await pairRevokeMobile(id);
                      void refreshDevices();
                    }}
                  >
                    {zh ? '解绑' : 'Unlink'}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
