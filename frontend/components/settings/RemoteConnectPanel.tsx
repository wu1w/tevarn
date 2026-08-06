'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  meshAuthMobile,
  meshSetMobile,
  meshStatusMobile,
  meshVpsSetMobile,
  meshVpsTestMobile,
  pairCancelMobile,
  pairConfirmMobile,
  pairDevicesMobile,
  pairRevokeMobile,
  pairStartMobile,
  pairStatusMobile,
  type VpsMeshStatus,
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
  vps?: string | null;
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
  vps?: VpsMeshStatus;
  install_hint?: string;
};

const INSTALL_CMD = `cd deploy/vps-relay
sudo bash install.sh`;

async function renderQrToCanvas(canvas: HTMLCanvasElement, text: string): Promise<boolean> {
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod: any = await import('qrcode');
    const toCanvas =
      mod?.toCanvas || mod?.default?.toCanvas || mod?.default?.default?.toCanvas;
    if (typeof toCanvas === 'function') {
      await toCanvas(canvas, text, {
        width: 200,
        margin: 2,
        color: { dark: '#0f172a', light: '#ffffff' },
        errorCorrectionLevel: 'M',
      });
      return true;
    }
  } catch (err) {
    console.warn('[pair-qr] render failed:', err);
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
  const [showVpsInstall, setShowVpsInstall] = useState(false);
  const [meshMode, setMeshMode] = useState('auto');
  const [vpsHost, setVpsHost] = useState('');
  const [vpsPort, setVpsPort] = useState('80');
  const [vpsToken, setVpsToken] = useState('');
  const [vpsScheme, setVpsScheme] = useState('http');
  const [vpsTestHint, setVpsTestHint] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshMesh = useCallback(async () => {
    try {
      const st = (await meshStatusMobile()) as MeshStatus;
      setMesh(st);
      if (st.mode) setMeshMode(String(st.mode));
      const v = st.vps;
      if (v?.host && !vpsHost) setVpsHost(String(v.host));
      if (v?.port != null && vpsPort === '80') setVpsPort(String(v.port));
      if (v?.scheme) setVpsScheme(String(v.scheme));
    } catch {
      /* backend may be older */
    }
  }, [vpsHost, vpsPort]);

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
    const t = setInterval(() => void refreshMesh(), 8000);
    return () => {
      clearInterval(t);
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
        vps: (r as { vps?: string }).vps ?? null,
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

  const onVpsTest = async () => {
    setBusy(true);
    setVpsTestHint(null);
    try {
      const r = await meshVpsTestMobile({
        host: vpsHost.trim() || undefined,
        port: Number(vpsPort) || 80,
        token: vpsToken.trim() || undefined,
        scheme: vpsScheme,
      });
      if (r.ok) {
        const msg = r.detail || (zh ? `中继可达 · ${r.latency_ms ?? '—'}ms` : `OK · ${r.latency_ms}ms`);
        setVpsTestHint(msg);
        addToast(msg, 'success');
      } else {
        const msg = r.error || (zh ? '中继不可达' : 'Unreachable');
        setVpsTestHint(msg);
        addToast(msg, 'error');
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setVpsTestHint(msg);
      addToast(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  const onVpsEnable = async (enabled: boolean) => {
    if (enabled && (!vpsHost.trim() || !vpsToken.trim())) {
      addToast(zh ? '请先填写 VPS 地址和访问令牌' : 'Host and token required', 'error');
      return;
    }
    setBusy(true);
    try {
      const r = await meshVpsSetMobile({
        host: vpsHost.trim(),
        port: Number(vpsPort) || 80,
        token: vpsToken.trim() || undefined,
        scheme: vpsScheme,
        enabled,
      });
      addToast(
        r.detail ||
          (enabled
            ? zh
              ? '中继已启用'
              : 'Relay enabled'
            : zh
              ? '中继已关闭'
              : 'Relay disabled'),
        'success',
      );
      // clear token field after save (stored on server); keep host
      if (enabled) setVpsToken('');
      void refreshMesh();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const modes = [
    { id: 'auto', zh: '自动（推荐）', en: 'Auto' },
    { id: 'lan', zh: '仅局域网', en: 'LAN only' },
    { id: 'vps', zh: '优先 VPS', en: 'VPS first' },
    { id: 'ts', zh: '仅 Tailscale', en: 'Tailscale' },
  ];

  const vps = mesh.vps || {};
  const vpsOnline = Boolean(vps.online);
  const vpsEnabled = Boolean(vps.enabled);
  const vpsConfigured = Boolean(vps.configured);

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
        <div className="mb-1 text-[13.5px] font-semibold text-foreground">
          {zh ? '远程连接 · 匹配手机' : 'Remote · Match phone'}
        </div>
        <p className="mb-4 text-[12.5px] leading-relaxed text-foreground-muted">
          {zh
            ? '在 PC 生成二维码，手机 App「连接」页扫描即可。在家走局域网，出门自动走 VPS / Tailscale；手机无需填写任何地址。'
            : 'Generate a QR on PC; scan in the phone Connect tab. LAN at home, VPS/Tailscale when away — phone never types an address.'}
        </p>

        {/* mesh status */}
        <div className="mb-4 rounded-xl border border-border-subtle bg-elevated-bg/60 px-3.5 py-3 text-[12.5px] text-foreground-muted">
          <div>{mesh.detail || (zh ? '检测网络…' : 'Detecting network…')}</div>
          <div className="mt-1 font-mono text-[11px] text-foreground-dim">
            {zh ? '局域网' : 'LAN'} {mesh.lan_ip || '—'}
            {' · '}
            VPS{' '}
            {vpsOnline
              ? zh
                ? '在线'
                : 'online'
              : vpsEnabled
                ? zh
                  ? '连接中'
                  : 'connecting'
                : vpsConfigured
                  ? zh
                    ? '已配置'
                    : 'configured'
                  : '—'}
            {vps.host ? ` ${vps.host}` : ''}
            {' · '}
            TS {mesh.tailscale_ip || (mesh.auth_key_set ? (zh ? '密钥已配置' : 'key set') : '—')}
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

        {/* ── VPS relay block ─────────────────────────────────────────── */}
        <div className="mb-4 rounded-xl border border-border-subtle bg-elevated-bg/40 p-3.5">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <div className="text-[13px] font-semibold text-foreground">
              {zh ? '自有 VPS 中继' : 'Self-hosted VPS relay'}
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                vpsOnline
                  ? 'bg-emerald-500/15 text-emerald-600'
                  : vpsEnabled
                    ? 'bg-amber-500/15 text-amber-600'
                    : 'bg-slate-500/10 text-foreground-dim'
              }`}
            >
              {vpsOnline
                ? zh
                  ? '隧道在线'
                  : 'Online'
                : vpsEnabled
                  ? zh
                    ? '连接中…'
                    : 'Connecting…'
                  : zh
                    ? '未启用'
                    : 'Off'}
            </span>
          </div>
          <p className="mb-3 text-[11.5px] leading-relaxed text-foreground-dim">
            {zh
              ? '把公网 VPS 当作跳板：PC 只出站连中继，手机扫码即可在 5G 连回家。无需家里端口映射。'
              : 'Your VPS is a hop: PC dials out only; phone scans QR on cellular. No home port-forward.'}
          </p>

          {/* step guide */}
          <ol className="mb-3 list-decimal space-y-1 pl-4 text-[11.5px] text-foreground-muted">
            <li>
              {zh ? '在 VPS 上安装中继（点下方「一键部署说明」）' : 'Install relay on VPS (see deploy guide)'}
            </li>
            <li>{zh ? '把打印的 Host + Token 填到下面' : 'Paste printed Host + Token below'}</li>
            <li>{zh ? '检测连通 → 启用中继 → 生成二维码给手机扫' : 'Test → Enable → Generate QR'}</li>
          </ol>

          <button
            type="button"
            className="mb-3 text-[12px] font-medium text-brand-cyan hover:underline"
            onClick={() => setShowVpsInstall((v) => !v)}
          >
            {showVpsInstall
              ? zh
                ? '收起 · 一键部署说明'
                : 'Hide deploy guide'
              : zh
                ? '一键部署说明（在 VPS 上执行）'
                : 'One-click deploy guide (run on VPS)'}
          </button>

          {showVpsInstall && (
            <div className="mb-3 space-y-2 rounded-lg border border-border-subtle bg-card-bg p-3">
              <p className="text-[11.5px] leading-relaxed text-foreground-dim">
                {zh
                  ? '要求：Ubuntu 22.04+，安全组放行 TCP 80。把仓库里 deploy/vps-relay 拷到 VPS 后执行：'
                  : 'Needs Ubuntu 22.04+, open TCP 80. Copy deploy/vps-relay to the VPS, then:'}
              </p>
              <pre className="overflow-x-auto rounded-lg bg-slate-900/90 p-3 font-mono text-[11px] text-slate-100">
                {INSTALL_CMD}
              </pre>
              <button
                type="button"
                onClick={() => void onCopy(INSTALL_CMD)}
                className="text-[12px] font-medium text-brand-cyan hover:underline"
              >
                {zh ? '复制安装命令' : 'Copy install command'}
              </button>
              <p className="text-[11px] text-foreground-dim">
                {zh
                  ? '安装结束会打印 Host 与 Token。也可阅读 deploy/vps-relay/README.md。'
                  : 'Install prints Host + Token. See deploy/vps-relay/README.md.'}
              </p>
            </div>
          )}

          <div className="grid gap-2 sm:grid-cols-[1fr_100px]">
            <input
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 font-mono text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
              placeholder={zh ? 'VPS 地址，如 150.x.x.x 或 relay.example.com' : 'VPS host or IP'}
              value={vpsHost}
              onChange={(e) => setVpsHost(e.target.value)}
              autoComplete="off"
            />
            <input
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 font-mono text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
              placeholder="80"
              value={vpsPort}
              onChange={(e) => setVpsPort(e.target.value)}
              inputMode="numeric"
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {(['http', 'https'] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setVpsScheme(s)}
                className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium ${
                  vpsScheme === s
                    ? 'border-brand-purple/40 bg-brand-purple/15 text-foreground'
                    : 'border-border-subtle text-foreground-muted'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <input
            className="mt-2 w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 font-mono text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none"
            placeholder={
              vps.has_token
                ? zh
                  ? '访问令牌（已保存，留空不改）'
                  : 'Token (saved — leave blank to keep)'
                : zh
                  ? '访问令牌 tr_live_…'
                  : 'Token tr_live_…'
            }
            value={vpsToken}
            onChange={(e) => setVpsToken(e.target.value)}
            type="password"
            autoComplete="off"
          />

          {vpsTestHint && (
            <p className="mt-2 text-[11.5px] text-foreground-muted">{vpsTestHint}</p>
          )}
          {vps.public_base && (
            <p className="mt-1 font-mono text-[11px] text-foreground-dim">
              {zh ? '公网基址' : 'Public base'}: {vps.public_base}
            </p>
          )}
          {vps.detail && (
            <p className="mt-1 text-[11.5px] text-foreground-dim">
              {zh ? '状态' : 'Status'}: {vps.detail}
            </p>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || !vpsHost.trim()}
              onClick={() => void onVpsTest()}
              className="inline-flex items-center rounded-xl border border-border-default bg-card-bg px-3.5 py-2 text-[12.5px] font-medium text-foreground-muted hover:bg-card-bg-hover disabled:opacity-50"
            >
              {zh ? '检测连通' : 'Test'}
            </button>
            {!vpsEnabled ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onVpsEnable(true)}
                className="inline-flex items-center rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-3.5 py-2 text-[12.5px] font-medium text-white disabled:opacity-50"
              >
                {zh ? '启用中继' : 'Enable relay'}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onVpsEnable(false)}
                className="inline-flex items-center rounded-xl border border-border-default bg-card-bg px-3.5 py-2 text-[12.5px] font-medium text-foreground-muted hover:bg-card-bg-hover disabled:opacity-50"
              >
                {zh ? '断开中继' : 'Disable'}
              </button>
            )}
          </div>
        </div>

        {/* Tailscale one-time key */}
        {!mesh.auth_key_set ? (
          <div className="mb-4">
            <button
              type="button"
              className="text-[12px] font-medium text-brand-cyan hover:underline"
              onClick={() => setShowAuth((v) => !v)}
            >
              {showAuth
                ? zh
                  ? '收起 · Tailscale 外出（可选）'
                  : 'Hide Tailscale setup'
                : zh
                  ? '可选 · Tailscale 外出（与 VPS 并行）'
                  : 'Optional · Tailscale (alongside VPS)'}
            </button>
            {showAuth && (
              <div className="mt-2 space-y-2">
                <p className="text-[11.5px] leading-relaxed text-foreground-dim">
                  {zh
                    ? '若不用 VPS，也可粘贴一次 Tailscale 访问密钥。与 VPS 中继互不冲突。'
                    : 'Or paste a Tailscale auth key once. Works alongside VPS.'}
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
                  {zh ? '启用 Tailscale' : 'Enable Tailscale'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <p className="mb-4 text-[12px] text-brand-cyan">
            {zh ? 'Tailscale 密钥已配置' : 'Tailscale key configured'}
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
                ? '手机打开 Takton → 连接 → 扫描二维码（无需填 VPS）'
                : 'Phone: Takton → Connect → Scan (no VPS typing)'}
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
                    className="shrink-0 text-[12px] text-error-text hover:underline"
                    onClick={async () => {
                      try {
                        await pairRevokeMobile(id);
                        addToast(zh ? '已解除配对' : 'Revoked', 'success');
                        void refreshDevices();
                      } catch (e: unknown) {
                        addToast(e instanceof Error ? e.message : String(e), 'error');
                      }
                    }}
                  >
                    {zh ? '解除' : 'Revoke'}
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
