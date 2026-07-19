'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getChannelPresets,
  getChannels,
  createChannel,
  updateChannel,
  deleteChannel,
  testChannel,
  type ChannelPreset,
  type ChannelPresetField,
  type ChannelItem,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { useT } from '@/stores/localeStore';

export const dynamic = 'force-dynamic';

/* ─── 平台配置 ─── */
const PLATFORM_META: Record<string, { icon: string; color: string; labelKey?: string; label?: string }> = {
  telegram:  { icon: '✈️', color: 'bg-sky-500',    label: 'Telegram' },
  discord:   { icon: '🎮', color: 'bg-indigo-500',  label: 'Discord' },
  wecom:     { icon: '💼', color: 'bg-green-600',    labelKey: 'channels.platform.wecom' },
  qqbot:     { icon: '🐧', color: 'bg-cyan-500',     labelKey: 'channels.platform.qqbot' },
  slack:     { icon: '💬', color: 'bg-purple-500',   label: 'Slack' },
  feishu:    { icon: '🐦', color: 'bg-blue-500',     labelKey: 'channels.platform.feishu' },
  dingtalk:  { icon: '🔔', color: 'bg-sky-600',      labelKey: 'channels.platform.dingtalk' },
  signal:    { icon: '🔒', color: 'bg-blue-400',     label: 'Signal' },
};

function platformLabel(platform: string, t: (k: any) => string): string {
  const meta = PLATFORM_META[platform];
  if (!meta) return platform;
  if (meta.labelKey) return t(meta.labelKey as any);
  return meta.label || platform;
}

function PlatformBadge({ platform }: { platform: string }) {
  const meta = PLATFORM_META[platform] || { icon: '📡', color: 'bg-foreground-dim', label: platform };
  return (
    <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs ${meta.color}`}>
      {meta.icon}
    </span>
  );
}

/* ─── 添加通道弹窗 ─── */
function AddChannelModal({
  presets,
  onClose,
  onCreated,
}: {
  presets: ChannelPreset[];
  onClose: () => void;
  onCreated: (ch: ChannelItem) => void;
}) {
  const t = useT();
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [form, setForm] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const preset = presets.find((p) => p.platform === selected);

  const handleCreate = async () => {
    if (!selected || !name.trim()) return;
    setSaving(true);
    try {
      const payload: any = { platform: selected, name: name.trim(), enabled: false, extra: {} };
      const allFields = [
        ...(preset?.fields || []),
        ...Object.entries(preset?.extra_schema || {}).map(([k, v]: any) => ({ key: k, ...v })),
      ];
      for (const f of allFields) {
        const val = form[f.key];
        if (val === undefined || val === '') continue;
        if (f.key === 'token' || f.key === 'api_key' || f.key === 'home_channel_id') {
          payload[f.key] = val;
        } else {
          payload.extra[f.key] = val;
        }
      }
      const ch = await createChannel(payload);
      addToast(t('channels.created'), 'success');
      onCreated(ch);
      onClose();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('channels.createFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const allFields = useMemo(() => [
    ...(preset?.fields || []),
    ...Object.entries(preset?.extra_schema || {}).map(([k, v]: any) => ({ key: k, ...v })),
  ], [preset]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-border-default bg-card-bg p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {!selected ? (
          <>
            <h2 className="mb-3 text-sm font-semibold text-foreground">{t('channels.addTitle')}</h2>
            <div className="grid grid-cols-2 gap-1.5 max-h-[65vh] overflow-y-auto pr-1">
              {presets.map((p) => {
                const meta = PLATFORM_META[p.platform] || { icon: '📡', label: p.name };
                return (
                  <button
                    key={p.platform}
                    onClick={() => { setSelected(p.platform); setName(p.name); }}
                    className="flex items-center gap-2.5 rounded-lg border border-border-subtle px-3 py-2.5 text-left transition-all hover:border-brand-purple/40 hover:bg-brand-purple/5"
                  >
                    <span className="text-base">{meta.icon}</span>
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-foreground truncate">{p.name}</div>
                      <div className="text-[10px] text-foreground-dim truncate">{p.description}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <PlatformBadge platform={selected} />
              <span className="text-sm font-medium text-foreground">{preset?.name}</span>
              <button onClick={() => { setSelected(null); setName(''); setForm({}); }} className="ml-auto text-[10px] text-foreground-dim hover:text-foreground">{t('channels.back')}</button>
            </div>
            <div>
              <label className="mb-1 block text-[10px] text-foreground-muted">{t('channels.name')}</label>
              <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground" placeholder={t('channels.namePlaceholder')} />
            </div>
            {allFields.map((f: ChannelPresetField & { key: string }) => (
              <div key={f.key}>
                <label className="mb-0.5 block text-[10px] text-foreground-muted">
                  {f.label}{f.required && <span className="ml-0.5 text-error-text">*</span>}
                </label>
                {f.type === 'select' ? (
                  <select value={form[f.key] ?? f.default ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground">
                    {(f.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : f.type === 'switch' ? (
                  <label className="flex items-center gap-2 py-1">
                    <input type="checkbox" checked={form[f.key] ?? f.default ?? true} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} className="rounded" />
                    <span className="text-[10px] text-foreground-dim">{f.help || f.label}</span>
                  </label>
                ) : (
                  <input
                    type={f.type === 'password' ? 'password' : 'text'}
                    value={form[f.key] ?? ''}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                    placeholder={f.help || ''}
                    className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground"
                  />
                )}
              </div>
            ))}
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={onClose} className="rounded-md px-3 py-1.5 text-[10px] text-foreground-muted hover:text-foreground">{t('common.cancel')}</button>
              <button onClick={handleCreate} disabled={saving || !name.trim()} className="rounded-md bg-brand-purple px-3 py-1.5 text-[10px] font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50">
                {saving ? '…' : t('channels.create')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── 通道编辑面板 ─── */
function ChannelEditPanel({
  channel,
  preset,
  onSaved,
  onDeleted,
}: {
  channel: ChannelItem;
  preset: ChannelPreset | undefined;
  onSaved: (ch: ChannelItem) => void;
  onDeleted: () => void;
}) {
  const t = useT();
  const [form, setForm] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; detail?: string } | null>(null);
  const addToast = useToastStore((s) => s.addToast);
  const { confirm, ConfirmDialogComponent } = useConfirm();

  useEffect(() => {
    const init: Record<string, any> = {
      name: channel.name,
      description: channel.description || '',
      enabled: channel.enabled,
      home_channel_id: channel.home_channel_id || '',
    };
    if (channel.has_token) init.token = '••••••••';
    if (channel.has_api_key) init.api_key = '••••••••';
    for (const [k, v] of Object.entries(channel.extra || {})) init[k] = v;
    setForm(init);
  }, [channel]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: any = { name: form.name, description: form.description || null, enabled: form.enabled, extra: {} };
      const allFields = [
        ...(preset?.fields || []),
        ...Object.entries(preset?.extra_schema || {}).map(([k, v]: any) => ({ key: k, ...v })),
      ];
      for (const f of allFields) {
        const val = form[f.key];
        if (val === undefined) continue;
        if (f.key === 'token' || f.key === 'api_key') {
          if (val && val !== '••••••••') payload[f.key] = val;
          else if (!val) payload[f.key] = '';
        } else if (f.key === 'home_channel_id') {
          payload[f.key] = val || null;
        } else {
          payload.extra[f.key] = val;
        }
      }
      const ch = await updateChannel(channel.id, payload);
      addToast(t('channels.saved'), 'success');
      onSaved(ch);
    } catch (e: any) {
      addToast(e?.response?.data?.detail || t('channels.saveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testChannel(channel.id);
      setTestResult(res);
      if (res.success) addToast(res.message, 'success');
      else addToast(res.message, 'error');
      onSaved(await getChannels().then((cs) => cs.find((c) => c.id === channel.id) || channel));
    } catch (e: any) {
      setTestResult({ success: false, message: t('channels.testRequestFailed'), detail: e.message });
      addToast(t('channels.testFailed'), 'error');
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!(await confirm(t('channels.confirmDelete'), t('channels.confirmDeleteDesc')))) return;
    try {
      await deleteChannel(channel.id);
      addToast(t('channels.deleted'), 'success');
      onDeleted();
    } catch {
      addToast(t('channels.deleteFailed'), 'error');
    }
  };

  const allFields = useMemo(() => [
    ...(preset?.fields || []),
    ...Object.entries(preset?.extra_schema || {}).map(([k, v]: any) => ({ key: k, ...v })),
  ], [preset]);

  const meta = PLATFORM_META[channel.platform] || { icon: '📡', label: channel.platform };
  const metaLabel = platformLabel(channel.platform, t);

  return (
    <div className="mx-auto max-w-md space-y-4">
      {/* 顶部：平台 + 状态 */}
      <div className="flex items-center gap-3">
        <PlatformBadge platform={channel.platform} />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-foreground truncate">{channel.name}</h3>
          <span className="text-[10px] text-foreground-dim">{metaLabel}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {channel.connected ? (
            <><span className="mr-0.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" /><span className="text-[10px] text-emerald-400">{t('channels.connected')}</span></>
          ) : channel.enabled ? (
            <><span className="h-1.5 w-1.5 rounded-full bg-amber-400" /><span className="text-[10px] text-amber-400">{t('channels.notConnected')}</span></>
          ) : (
            <><span className="h-1.5 w-1.5 rounded-full bg-foreground-dim" /><span className="text-[10px] text-foreground-dim">{t('channels.notEnabled')}</span></>
          )}
        </div>
      </div>

      {/* 上次测试结果 */}
      {channel.last_test_result && (
        <div className={`rounded-md border px-2.5 py-1.5 text-[10px] ${channel.connected ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : 'border-amber-500/30 bg-amber-500/5 text-amber-300'}`}>
          {channel.last_test_result}
        </div>
      )}

      {/* 表单 */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <label className="text-[10px] text-foreground-muted">{t('channels.enable')}</label>
          <label className="relative inline-flex cursor-pointer items-center">
            <input type="checkbox" checked={form.enabled ?? false} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="peer sr-only" />
            <div className="h-4 w-7 rounded-full bg-foreground-dim/30 after:absolute after:left-[2px] after:top-[2px] after:h-3 after:w-3 after:rounded-full after:bg-white after:transition-all peer-checked:bg-brand-purple peer-checked:after:translate-x-3" />
          </label>
        </div>
        <div>
          <label className="mb-0.5 block text-[10px] text-foreground-muted">{t('channels.fieldName')}</label>
          <input value={form.name ?? ''} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground" />
        </div>
        {allFields.map((f: ChannelPresetField & { key: string }) => (
          <div key={f.key}>
            <label className="mb-0.5 block text-[10px] text-foreground-muted">{f.label}{f.required && <span className="ml-0.5 text-error-text">*</span>}</label>
            {f.type === 'select' ? (
              <select value={form[f.key] ?? f.default ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground">
                {(f.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : f.type === 'switch' ? (
              <label className="flex items-center gap-2 py-1">
                <input type="checkbox" checked={form[f.key] ?? f.default ?? true} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} className="rounded" />
                <span className="text-[10px] text-foreground-dim">{f.help || f.label}</span>
              </label>
            ) : (
              <input
                type={f.type === 'password' ? 'password' : 'text'}
                value={form[f.key] ?? ''}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={f.type === 'password' && form[f.key] === '••••••••' ? t('channels.keepOriginal') : (f.help || '')}
                className="w-full rounded-md border border-border-subtle bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground"
              />
            )}
          </div>
        ))}
      </div>

      {/* 测试结果 */}
      {testResult && (
        <div className={`rounded-md border px-2.5 py-1.5 text-[10px] ${testResult.success ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : 'border-rose-500/30 bg-rose-500/5 text-rose-300'}`}>
          <div className="font-medium">{testResult.message}</div>
          {testResult.detail && <div className="mt-0.5 opacity-70">{testResult.detail}</div>}
        </div>
      )}

      {/* 底部操作 */}
      <div className="flex items-center justify-between border-t border-border-subtle pt-3">
        <button onClick={handleDelete} className="text-[10px] text-rose-400/70 hover:text-rose-300">{t('common.delete')}</button>
        <div className="flex gap-1.5">
          <button onClick={handleTest} disabled={testing} className="rounded-md border border-border-subtle px-2.5 py-1 text-[10px] text-foreground-muted hover:text-foreground disabled:opacity-50">
            {testing ? '…' : t('channels.testConnection')}
          </button>
          <button onClick={handleSave} disabled={saving} className="rounded-md bg-brand-purple px-2.5 py-1 text-[10px] font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50">
            {saving ? '…' : t('common.save')}
          </button>
        </div>
      </div>
      {ConfirmDialogComponent}
    </div>
  );
}

/* ─── 主页面 ─── */
export default function ChannelsPage() {
  const t = useT();
  const [presets, setPresets] = useState<ChannelPreset[]>([]);
  const [channels, setChannels] = useState<ChannelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const addToast = useToastStore((s) => s.addToast);

  const loadData = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([getChannelPresets(), getChannels()]);
      setPresets(p);
      setChannels(c);
    } catch {
      addToast(t('channels.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { loadData(); }, [loadData]);

  const editingChannel = channels.find((c) => c.id === editing);
  const editingPreset = presets.find((p) => p.platform === editingChannel?.platform);

  if (loading) return <div className="flex h-full items-center justify-center text-xs text-foreground-dim">{t('channels.loading')}</div>;

  return (
    <div className="flex h-full flex-col">
      {/* 顶栏 */}
      <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3">
        <div>
          <h1 className="text-sm font-semibold text-foreground">{t('channels.title')}</h1>
          <p className="text-[10px] text-foreground-dim">{t('channels.subtitle')}</p>
        </div>
        <button onClick={() => setShowAdd(true)} className="flex items-center gap-1 rounded-md bg-brand-purple px-2.5 py-1.5 text-[10px] font-medium text-white hover:bg-brand-purple/80">
          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
          {t('channels.add')}
        </button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* 左侧列表 */}
        <div className="w-60 flex-shrink-0 border-r border-border-subtle overflow-y-auto">
          {channels.length === 0 ? (
            <div className="px-5 py-10 text-center">
              <div className="text-xl mb-1.5">📡</div>
              <div className="text-[10px] text-foreground-dim">{t('channels.emptyList')}</div>
            </div>
          ) : (
            <div className="p-1.5 space-y-0.5">
              {channels.map((ch) => {
                const meta = PLATFORM_META[ch.platform] || { icon: '📡', label: ch.platform };
                const label = platformLabel(ch.platform, t);
                return (
                  <button
                    key={ch.id}
                    onClick={() => setEditing(ch.id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all ${
                      editing === ch.id
                        ? 'bg-brand-purple/10 border border-brand-purple/30'
                        : 'hover:bg-white/[0.03] border border-transparent'
                    }`}
                  >
                    <span className="text-sm">{meta.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        {/* 已连接并测试通过 → 脉冲绿色小点 */}
                        {ch.connected && (
                          <span className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-emerald-400" />
                        )}
                        <span className="text-xs font-medium text-foreground truncate">{ch.name}</span>
                      </div>
                      <div className="text-[10px] text-foreground-dim">{label}</div>
                    </div>
                    {!ch.enabled && (
                      <span className="text-[9px] text-foreground-dim/60 bg-foreground-dim/10 rounded px-1">{t('channels.disabled')}</span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* 右侧编辑 */}
        <div className="flex-1 overflow-y-auto p-5">
          {editingChannel ? (
            <ChannelEditPanel
              key={editingChannel.id}
              channel={editingChannel}
              preset={editingPreset}
              onSaved={(ch) => setChannels((cs) => cs.map((c) => c.id === ch.id ? ch : c))}
              onDeleted={() => { setEditing(null); loadData(); }}
            />
          ) : (
            <div className="flex h-full min-h-[280px] items-center justify-center p-6">
              <div className="max-w-sm text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border-subtle bg-elevated-bg/50 text-2xl">
                  📡
                </div>
                <div className="text-sm font-semibold text-foreground">{t('channels.selectTitle')}</div>
                <p className="mt-1.5 text-xs leading-relaxed text-foreground-muted">
                  {t('channels.selectHint')}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      {showAdd && (
        <AddChannelModal
          presets={presets}
          onClose={() => setShowAdd(false)}
          onCreated={() => { setShowAdd(false); loadData(); }}
        />
      )}
    </div>
  );
}
