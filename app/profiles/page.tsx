'use client';

import React, { useEffect, useState } from 'react';
import { AgentProfile } from '@/types';
import { getAgentProfiles, createAgentProfile, updateAgentProfile, deleteAgentProfile, setDefaultAgentProfile } from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

export default function ProfilesPage() {
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const addToast = useToastStore((state) => state.addToast);
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<AgentProfile | null>(null);
  const [form, setForm] = useState({ name: '', description: '', system_prompt: '', agent_md: '', skills: '' });
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    setLoading(true);
    getAgentProfiles()
      .then((data) => setProfiles(Array.isArray(data) ? data : []))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', description: '', system_prompt: '', agent_md: '', skills: '' });
    setShowForm(true);
  };

  const openEdit = (profile: AgentProfile) => {
    setEditing(profile);
    setForm({
      name: profile.name,
      description: profile.description || '',
      system_prompt: profile.system_prompt,
      agent_md: profile.agent_md,
      skills: profile.skills.join(','),
    });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    const payload = { ...form, skills: form.skills.split(',').map((s) => s.trim()).filter(Boolean) };
    try {
      if (editing) {
        await updateAgentProfile(editing.id, payload);
        addToast('画像已更新', 'success');
      } else {
        await createAgentProfile(payload);
        addToast('画像已创建', 'success');
      }
      setShowForm(false);
      load();
    } catch {
      // 错误由全局 API 拦截器统一 toast 提示
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此画像？'); if (!ok) return;
    try {
      await deleteAgentProfile(id);
      addToast('画像已删除', 'success');
      load();
    } catch {
      // 错误由全局 API 拦截器统一 toast 提示
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await setDefaultAgentProfile(id);
      addToast('默认画像已设置', 'success');
      load();
    } catch {
      // 错误由全局 API 拦截器统一 toast 提示
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">Agent 画像</h1>
        <button
          onClick={openCreate}
          className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white hover:from-brand-purple hover:to-brand-cyan transition-all shadow-lg shadow-violet-500/20"
        >
          + 新建画像
        </button>
      </div>

      {showForm && (
        <div className="mb-6 rounded-xl border border-border-subtle bg-card-bg/60 p-4">
          <h2 className="mb-3 text-sm font-semibold text-foreground">
            {editing ? '编辑画像' : '新建画像'}
          </h2>
          <form onSubmit={handleSubmit} className="grid gap-3">
            <input
              placeholder="名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
              required
            />
            <input
              placeholder="描述"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
            />
            <textarea
              placeholder="System Prompt"
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              className="h-24 rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all resize-none"
            />
            <textarea
              placeholder="Agent.md"
              value={form.agent_md}
              onChange={(e) => setForm({ ...form, agent_md: e.target.value })}
              className="h-24 rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all resize-none"
            />
            <input
              placeholder="技能 (逗号分隔)"
              value={form.skills}
              onChange={(e) => setForm({ ...form, skills: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
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
      ) : profiles.length === 0 ? (
        <div className="rounded-xl border border-border-subtle border-dashed py-12 text-center text-foreground-dim">
          暂无 Agent 画像
        </div>
      ) : (
        <div className="grid gap-3">
          {profiles.map((profile) => (
            <div
              key={profile.id}
              className="rounded-xl border border-border-subtle bg-card-bg/40 p-4 hover:border-border-default transition-colors"
            >
              <div className="flex items-center justify-between">
                <div className="font-medium text-foreground text-sm">{profile.name}</div>
                <div className="flex items-center gap-2">
                  {profile.is_default && (
                    <span className="rounded-md bg-gradient-to-r from-brand-purple/20 to-brand-cyan/20 px-2 py-0.5 text-[10px] font-bold uppercase text-brand-cyan border border-violet-500/20">
                      默认
                    </span>
                  )}
                  {!profile.is_default && (
                    <button
                      onClick={() => handleSetDefault(profile.id)}
                      className="rounded-md bg-violet-500/10 px-2 py-0.5 text-[10px] text-violet-400 hover:bg-violet-500/20 transition-colors border border-violet-500/20"
                    >
                      设为默认
                    </button>
                  )}
                </div>
              </div>
              {profile.description && (
                <div className="mt-1 text-sm text-foreground-dim">{profile.description}</div>
              )}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {profile.skills.map((skill) => (
                  <span
                    key={skill}
                    className="rounded-md bg-card-bg-hover border border-border-subtle px-2 py-0.5 text-[10px] text-foreground-muted"
                  >
                    {skill}
                  </span>
                ))}
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => openEdit(profile)}
                  className="rounded-lg bg-card-bg-hover border border-border-subtle px-2.5 py-1 text-xs text-foreground-muted hover:bg-card-bg-hover hover:text-foreground transition-all"
                >
                  编辑
                </button>
                <button
                  onClick={() => handleDelete(profile.id)}
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
