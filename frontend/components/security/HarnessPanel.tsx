'use client';

/**
 * Harness 控制条：沙箱档位 + allow/deny 规则（Grok 风格 DSL）
 * 保持轻量，挂在权限页底部，不塞进巨型 page.tsx。
 */

import React, { useEffect, useState } from 'react';
import {
  getHarnessPermissionRules,
  getSandboxProfiles,
  putHarnessPermissionRules,
  setSandboxProfile,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';

export function HarnessPanel({ zh = true }: { zh?: boolean }) {
  const addToast = useToastStore((s) => s.addToast);
  const [profiles, setProfiles] = useState<
    Array<{ id: string; label: string; description: string; network: boolean }>
  >([]);
  const [current, setCurrent] = useState('workspace');
  const [deny, setDeny] = useState('');
  const [allow, setAllow] = useState('');
  const [busy, setBusy] = useState(false);
  const [secrets, setSecrets] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const [p, r] = await Promise.all([getSandboxProfiles(), getHarnessPermissionRules()]);
        setProfiles(p.profiles || []);
        setCurrent(p.current || 'workspace');
        setAllow((r.rules?.allow || []).join('\n'));
        setDeny((r.rules?.deny || []).join('\n'));
        setSecrets(Boolean(r.secrets_enforced));
      } catch {
        /* interceptor */
      }
    })();
  }, []);

  const lines = (s: string) =>
    s
      .split(/[\n,]+/)
      .map((x) => x.trim())
      .filter(Boolean);

  const onProfile = async (id: string) => {
    setBusy(true);
    try {
      const r = await setSandboxProfile(id);
      setCurrent(r.profile);
      addToast(zh ? `沙箱档位：${r.profile}` : `Sandbox: ${r.profile}`, 'success');
    } catch {
      /* */
    } finally {
      setBusy(false);
    }
  };

  const onSaveRules = async () => {
    setBusy(true);
    try {
      await putHarnessPermissionRules({ allow: lines(allow), ask: [], deny: lines(deny) });
      addToast(zh ? '权限规则已保存' : 'Rules saved', 'success');
    } catch {
      /* */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        marginTop: 20,
        padding: 16,
        borderRadius: 12,
        border: '1px solid var(--border-subtle)',
        background: 'var(--card-bg)',
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 650, color: 'var(--foreground)', marginBottom: 6 }}>
        {zh ? '执行 Harness（沙箱 · 规则）' : 'Execution harness'}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginBottom: 12, lineHeight: 1.5 }}>
        {zh
          ? '对齐 Grok Build：沙箱档位 + Bash(rm*) / Edit(**/.env) 风格规则。密钥路径默认硬拒绝。'
          : 'Grok-style sandbox profiles + Bash(rm*) / Edit(**/.env) rules. Secrets denied by default.'}
        {secrets ? (zh ? ' · 密钥保护：开' : ' · secrets: on') : ''}
      </div>

      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{zh ? '沙箱档位' : 'Sandbox'}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        {profiles.map((p) => (
          <button
            key={p.id}
            type="button"
            disabled={busy}
            onClick={() => void onProfile(p.id)}
            title={p.description}
            style={{
              padding: '6px 12px',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 600,
              cursor: busy ? 'wait' : 'pointer',
              border: `1px solid ${current === p.id ? 'var(--brand-purple)' : 'var(--border-subtle)'}`,
              background:
                current === p.id
                  ? 'color-mix(in srgb, var(--brand-purple) 14%, transparent)'
                  : 'transparent',
              color: current === p.id ? 'var(--brand-purple)' : 'var(--foreground-muted)',
            }}
          >
            {zh ? p.label : p.id}
          </button>
        ))}
      </div>

      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        {zh ? '拒绝规则（每行一条）' : 'Deny rules (one per line)'}
      </div>
      <textarea
        value={deny}
        onChange={(e) => setDeny(e.target.value)}
        placeholder={'Bash(rm*)\nEdit(**/.env)\nRead(**/*.pem)'}
        rows={3}
        style={{
          width: '100%',
          fontFamily: 'var(--font-mono)',
          fontSize: 11.5,
          padding: 10,
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
          background: 'var(--input-bg)',
          color: 'var(--foreground)',
          marginBottom: 10,
        }}
      />
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        {zh ? '允许规则（可选）' : 'Allow rules (optional)'}
      </div>
      <textarea
        value={allow}
        onChange={(e) => setAllow(e.target.value)}
        placeholder="Bash(npm*)"
        rows={2}
        style={{
          width: '100%',
          fontFamily: 'var(--font-mono)',
          fontSize: 11.5,
          padding: 10,
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
          background: 'var(--input-bg)',
          color: 'var(--foreground)',
          marginBottom: 10,
        }}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => void onSaveRules()}
        style={{
          padding: '7px 14px',
          borderRadius: 8,
          border: 'none',
          background: 'var(--brand-purple)',
          color: '#fff',
          fontSize: 12,
          fontWeight: 600,
          cursor: busy ? 'wait' : 'pointer',
        }}
      >
        {zh ? '保存规则' : 'Save rules'}
      </button>
    </div>
  );
}
