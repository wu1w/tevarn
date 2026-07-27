'use client';

/**
 * AIOS 新建 Agent 向导（demo v2）
 * 5 步：角色 → 能力 → 预算 → 初始记忆 → 确认
 * 提交：POST /kernel/identities + 初始记忆入库
 */

import React, { useState } from 'react';
import { useToastStore } from '@/stores/toastStore';
import { createIdentity, addIdentityMemory } from '@/lib/api';
import { CAP_POOL, fmtTokens } from './shared';

const STEPS = [
  { zh: '角色', en: 'Role' },
  { zh: '能力', en: 'Capabilities' },
  { zh: '预算', en: 'Budget' },
  { zh: '初始记忆', en: 'Memory' },
  { zh: '确认', en: 'Confirm' },
];

export function HireWizard({ zh, onClose, onHired }: {
  zh: boolean;
  onClose: () => void;
  onHired: () => void;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [persona, setPersona] = useState('');
  const [duty, setDuty] = useState('');
  const [caps, setCaps] = useState<string[]>(['file_rw']);
  const [budget, setBudget] = useState(30000);
  const [initMemory, setInitMemory] = useState('');
  const [busy, setBusy] = useState(false);

  const canNext =
    step === 0 ? name.trim().length > 0 :
    step === 1 ? caps.length > 0 :
    step === 2 ? budget > 0 :
    true;

  const submit = async () => {
    setBusy(true);
    try {
      const ident = await createIdentity({
        name: name.trim(),
        role: role.trim(),
        capabilities: caps,
        default_token_budget: budget,
        meta: { persona: persona.trim(), duty: duty.trim() },
      });
      if (ident.id && initMemory.trim()) {
        await addIdentityMemory(ident.id, 'persona', initMemory.trim(), 'hire-wizard').catch(() => null);
      }
      addToast(zh ? `${name} 已启用 · 可在 Agent 列表查看` : `${name} activated · visible in the agent list`, 'success');
      onHired();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 96, background: 'var(--mask, rgba(10,9,7,0.6))', backdropFilter: 'blur(4px)' }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: 560, maxWidth: '94vw', maxHeight: '86vh', zIndex: 99, overflowY: 'auto',
        background: 'var(--elevated-bg)', border: '1px solid var(--border-default)',
        borderRadius: 16, boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.6))',
        padding: '22px 24px',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '新建 Agent' : 'New Agent'}
          </div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', color: 'var(--foreground-dim)', cursor: 'pointer', fontSize: 14 }}>✕</button>
        </div>

        {/* 步骤条 */}
        <div style={{ display: 'flex', gap: 6, margin: '16px 0 20px' }}>
          {STEPS.map((s, i) => (
            <div key={s.zh} style={{ flex: 1, textAlign: 'center' }}>
              <div style={{
                height: 4, borderRadius: 2,
                background: i <= step ? 'var(--brand-purple)' : 'var(--input-bg)',
                transition: 'background 200ms',
              }} />
              <div style={{
                fontSize: 10, marginTop: 5,
                color: i === step ? 'var(--brand-purple)' : 'var(--foreground-dim)',
                fontWeight: i === step ? 700 : 500,
              }}>{zh ? s.zh : s.en}</div>
            </div>
          ))}
        </div>

        {/* Step 0 角色 */}
        {step === 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Field label={zh ? '姓名' : 'Name'}>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder={zh ? '例如：小译' : 'e.g.: Yi'} style={inp} />
            </Field>
            <Field label={zh ? '角色定位' : 'Role'}>
              <input value={role} onChange={(e) => setRole(e.target.value)} placeholder={zh ? '例如：i18n Translator · 文档翻译' : 'e.g.: i18n Translator'} style={inp} />
            </Field>
            <Field label={zh ? '人格（persona）' : 'Persona'}>
              <textarea value={persona} onChange={(e) => setPersona(e.target.value)} rows={2}
                placeholder={zh ? '例如：译文风格克制专业，术语必须查表，不臆造。' : 'e.g.: Restrained, professional tone; always check the glossary.'} style={{ ...inp, resize: 'vertical' }} />
            </Field>
            <Field label={zh ? '职责（duty）' : 'Duty'}>
              <textarea value={duty} onChange={(e) => setDuty(e.target.value)} rows={2}
                placeholder={zh ? '例如：负责所有对外文档的中英互译与术语一致性。' : 'e.g.: Owns all CN↔EN translation of external docs.'} style={{ ...inp, resize: 'vertical' }} />
            </Field>
          </div>
        )}

        {/* Step 1 能力 */}
        {step === 1 && (
          <div>
            <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginBottom: 10, lineHeight: 1.6 }}>
              {zh
                ? '能力白名单——TA 能用什么工具。越界调用会被 kernel 拦截并升级为审批。'
                : 'Capability whitelist — tools this agent may use. Out-of-bounds calls are intercepted and escalated.'}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {CAP_POOL.map((c) => {
                const on = caps.includes(c.id);
                return (
                  <button key={c.id} onClick={() => setCaps((v) => (on ? v.filter((x) => x !== c.id) : [...v, c.id]))}
                    style={{
                      fontSize: 12, padding: '7px 13px', borderRadius: 9, cursor: 'pointer',
                      border: `1px solid ${on ? 'var(--brand-purple)' : 'var(--border-subtle)'}`,
                      background: on ? 'color-mix(in srgb, var(--brand-purple) 12%, transparent)' : 'transparent',
                      color: on ? 'var(--brand-purple)' : 'var(--foreground-dim)', fontWeight: 600,
                    }}>{zh ? c.zh : c.en}</button>
                );
              })}
            </div>
          </div>
        )}

        {/* Step 2 预算 */}
        {step === 2 && (
          <div>
            <Field label={zh ? '单任务预算（tokens）' : 'Per-task budget (tokens)'}>
              <input type="number" value={budget} onChange={(e) => setBudget(Math.max(0, Number(e.target.value)))} style={inp} />
            </Field>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8, lineHeight: 1.6 }}>
              {zh
                ? '超支即事前刹车中断。不设限 = 兜底 50k（不推荐）。'
                : 'Overspend triggers the pre-spend brake. Unlimited = fallback 50k (not recommended).'}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              {[10000, 30000, 50000].map((b) => (
                <button key={b} onClick={() => setBudget(b)}
                  style={{
                    fontSize: 11, padding: '5px 11px', borderRadius: 8, cursor: 'pointer',
                    border: `1px solid ${budget === b ? 'var(--brand-purple)' : 'var(--border-subtle)'}`,
                    background: budget === b ? 'color-mix(in srgb, var(--brand-purple) 12%, transparent)' : 'transparent',
                    color: budget === b ? 'var(--brand-purple)' : 'var(--foreground-dim)', fontWeight: 600,
                  }}>{fmtTokens(b)}</button>
              ))}
            </div>
          </div>
        )}

        {/* Step 3 初始记忆 */}
        {step === 3 && (
          <div>
            <Field label={zh ? '初始记忆' : 'Initial memory'}>
              <textarea value={initMemory} onChange={(e) => setInitMemory(e.target.value)} rows={4}
                placeholder={zh ? '写下来——Agent 会在下个任务中应用。' : 'Write it down — the agent applies it in the next task.'} style={{ ...inp, resize: 'vertical' }} />
            </Field>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6 }}>
              {zh
                ? '初始记忆将向量入库（identity_memory），执行中按输入自动召回。'
                : 'Initial memory is vector-indexed (identity_memory), auto-recalled by input at runtime.'}
            </div>
          </div>
        )}

        {/* Step 4 确认 */}
        {step === 4 && (
          <div>
            <div style={{ padding: 14, borderRadius: 12, border: '1px solid var(--border-subtle)', background: 'var(--card-bg)' }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
                {name} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>{role}</span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.7 }}>
                {zh ? '能力' : 'Capabilities'}：{caps.join(' · ')}<br />
                {zh ? '预算' : 'Budget'}：{fmtTokens(budget)}/{zh ? '任务' : 'task'}<br />
                {persona ? <>persona：{persona}<br /></> : null}
                {initMemory ? <>{zh ? '初始记忆' : 'Initial memory'}：{initMemory.slice(0, 80)}{initMemory.length > 80 ? '…' : ''}</> : null}
              </div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 10, lineHeight: 1.6 }}>
              {zh
                ? '启用后 TA 会出现在 Agent 列表与协作关系中，并可接收任务。'
                : 'Once activated, they appear in the agent list and org chart, and can receive tasks.'}
            </div>
          </div>
        )}

        {/* 底部按钮 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 22 }}>
          <button onClick={() => (step === 0 ? onClose() : setStep((s) => s - 1))} style={btnGhost}>
            {step === 0 ? (zh ? '取消' : 'Cancel') : (zh ? '上一步' : 'Back')}
          </button>
          {step < 4 ? (
            <button disabled={!canNext} onClick={() => setStep((s) => s + 1)} style={{ ...btnPrimary, opacity: canNext ? 1 : 0.45 }}>
              {zh ? '下一步' : 'Next'}
            </button>
          ) : (
            <button disabled={busy} onClick={submit} style={btnPrimary}>
              {busy ? (zh ? '启用中…' : 'Activating…') : (zh ? '确认启用' : 'Confirm activation')}
            </button>
          )}
        </div>
      </div>
    </>
  );
}

const inp: React.CSSProperties = {
  width: '100%', padding: '9px 12px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'var(--input-bg)',
  color: 'var(--foreground)', fontSize: 13, outline: 'none',
};
const btnPrimary: React.CSSProperties = {
  padding: '8px 18px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  padding: '8px 14px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'transparent',
  color: 'var(--foreground-muted)', fontSize: 12.5, fontWeight: 500, cursor: 'pointer',
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block' }}>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--foreground-muted)', marginBottom: 5 }}>{label}</div>
      {children}
    </label>
  );
}
