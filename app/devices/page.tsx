'use client';

import React, { useEffect, useState } from 'react';
import { Device } from '@/types';
import { getDevices, createDevice, updateDevice, deleteDevice, heartbeatDevice } from '@/lib/api';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

export default function DevicesPage() {
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Device | null>(null);
  const [form, setForm] = useState({ name: '', device_type: '', status: 'offline' });
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    setLoading(true);
    getDevices()
      .then((data) => setDevices(Array.isArray(data) ? data : []))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', device_type: '', status: 'offline' });
    setShowForm(true);
  };

  const openEdit = (device: Device) => {
    setEditing(device);
    setForm({ name: device.name, device_type: device.device_type, status: device.status });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (editing) {
        await updateDevice(editing.id, form);
      } else {
        await createDevice(form);
      }
      setShowForm(false);
      load();
    } catch (err) {
      console.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此设备？'); if (!ok) return;
    try {
      await deleteDevice(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  const handleHeartbeat = async (id: string) => {
    try {
      await heartbeatDevice(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">设备管理</h1>
        <button
          onClick={openCreate}
          className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white hover:from-brand-purple hover:to-brand-cyan transition-all shadow-lg shadow-violet-500/20"
        >
          + 新建设备
        </button>
      </div>

      {showForm && (
        <div className="mb-6 rounded-xl border border-border-subtle bg-card-bg/60 p-4">
          <h2 className="mb-3 text-sm font-semibold text-foreground">
            {editing ? '编辑设备' : '新建设备'}
          </h2>
          <form onSubmit={handleSubmit} className="grid gap-3 sm:grid-cols-3">
            <input
              placeholder="名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
              required
            />
            <input
              placeholder="类型"
              value={form.device_type}
              onChange={(e) => setForm({ ...form, device_type: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
              required
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={submitting}
                className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white hover:from-brand-purple hover:to-brand-cyan disabled:from-gray-700 disabled:to-gray-700 disabled:text-foreground-dim transition-all"
              >
                {submitting ? '保存中...' : '保存'}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-xl border border-border-default bg-card-bg-hover px-4 py-2 text-sm font-medium text-foreground-muted hover:bg-card-bg-hover hover:text-foreground transition-all"
              >
                取消
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-foreground-dim">
          <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-violet-500/30 border-t-violet-500" />
          <p className="mt-2 text-sm">加载中...</p>
        </div>
      ) : devices.length === 0 ? (
        <div className="rounded-xl border border-border-subtle border-dashed py-12 text-center text-foreground-dim">
          暂无设备
        </div>
      ) : (
        <div className="grid gap-3">
          {devices.map((device) => (
            <div
              key={device.id}
              className="flex items-center justify-between rounded-xl border border-border-subtle bg-card-bg/40 p-4 hover:border-border-default transition-colors"
            >
              <div>
                <div className="font-medium text-foreground text-sm">{device.name}</div>
                <div className="mt-1 text-xs text-foreground-dim">
                  {device.device_type} · <span className={device.status === 'online' ? 'text-success-text' : 'text-foreground-dim'}>{device.status}</span>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span
                  className={`h-2 w-2 rounded-full flex-shrink-0 ${
                    device.status === 'online'
                      ? 'bg-status-online shadow-sm shadow-status-online/30 animate-pulse'
                      : 'bg-gray-700'
                  }`}
                />
                <span className="text-xs text-foreground-dim font-mono">
                  {new Date(device.last_seen_at).toLocaleString()}
                </span>
                <button
                  onClick={() => handleHeartbeat(device.id)}
                  className="rounded-lg bg-card-bg-hover border border-border-subtle px-2.5 py-1 text-xs text-foreground-muted hover:bg-cyan-500/10 hover:text-brand-cyan hover:border-cyan-500/20 transition-all"
                >
                  心跳
                </button>
                <button
                  onClick={() => openEdit(device)}
                  className="rounded-lg bg-card-bg-hover border border-border-subtle px-2.5 py-1 text-xs text-foreground-muted hover:bg-card-bg-hover hover:text-foreground transition-all"
                >
                  编辑
                </button>
                <button
                  onClick={() => handleDelete(device.id)}
                  className="rounded-lg bg-error-bg border border-error-text/20 px-2.5 py-1 text-xs text-error-text hover:bg-error-bg0/20 transition-all"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {ConfirmDialogComponent}
    </div>
  );
}