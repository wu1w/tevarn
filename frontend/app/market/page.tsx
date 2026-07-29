'use client';

/**
 * AIOS 扩展页（demo v2）
 * 技能市场 + MCP 服务 · 网上看到什么有意思的，装进来
 * 样式严格对齐 aios-workbench-demo-v2.html：card 网格 / chip tab / 从链接添加
 */

import React, { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listStoreSkills,
  listInstalledStoreSkills,
  installStoreSkill,
  uninstallStoreSkill,
  getMCPServers,
  getMCPStatus,
  listMCPStore,
  installMCPFromStore,
  reviewSkillUrl,
  installSkillFromUrl,
  type UnifiedSkill,
  type SkillSource,
  type UnifiedMCPStoreItem,
  type UrlReviewReport,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useZh } from '@/hooks/useZh';
import type { MCPServer, MCPServerStatus } from '@/types';
import { AdvancedShell } from '@/components/layout/AdvancedShell';

type Tab = 'skills' | 'mcp';

function formatCount(n: number): string {
  if (!n || n <= 0) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function MarketPage() {
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('skills');
  const [search, setSearch] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [urlOpen, setUrlOpen] = useState(false);
  const [urlValue, setUrlValue] = useState('');
  const [urlReview, setUrlReview] = useState<UrlReviewReport | null>(null);
  const [urlBusy, setUrlBusy] = useState(false);
  const [installSkill, setInstallSkill] = useState<UnifiedSkill | null>(null);

  const skillsQ = useQuery({
    queryKey: ['store-skills', search],
    queryFn: () => listStoreSkills({ search: search || undefined, limit: 48, offset: 0 }),
    staleTime: 30_000,
    retry: 1,
  });
  const installedQ = useQuery({
    queryKey: ['store-installed'],
    queryFn: listInstalledStoreSkills,
    staleTime: 20_000,
    retry: 1,
  });
  const mcpServersQ = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: getMCPServers,
    staleTime: 20_000,
    retry: 1,
  });
  const mcpStatusQ = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMCPStatus,
    staleTime: 15_000,
    retry: 1,
  });
  const mcpStoreQ = useQuery({
    queryKey: ['mcp-store', search],
    queryFn: () => listMCPStore({ search: search || undefined, limit: 36 }),
    staleTime: 30_000,
    retry: 1,
    enabled: tab === 'mcp',
  });

  const installedSet = useMemo(() => {
    const s = new Set<string>();
    for (const i of installedQ.data ?? []) s.add(`${i.source}/${i.name}`);
    return s;
  }, [installedQ.data]);

  const statusMap = useMemo(() => {
    const m = new Map<string, MCPServerStatus>();
    for (const st of mcpStatusQ.data ?? []) m.set(st.name, st);
    return m;
  }, [mcpStatusQ.data]);

  const skills = skillsQ.data?.items ?? [];
  const mcpServers = mcpServersQ.data ?? [];
  const mcpStoreItems = mcpStoreQ.data?.items ?? [];

  const doInstallSkill = async (skill: UnifiedSkill) => {
    const key = `${skill.source}/${skill.id || skill.name}`;
    setBusyKey(key);
    try {
      const r = await installStoreSkill(skill.source as SkillSource, skill.id || skill.name);
      if (r.success) {
        addToast(zh ? `已安装 ${skill.display_name || skill.name}` : `Installed ${skill.display_name || skill.name}`, 'success');
        qc.invalidateQueries({ queryKey: ['store-installed'] });
        setInstallSkill(null);
      } else {
        addToast(r.error || (zh ? '安装失败' : 'Install failed'), 'error');
      }
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const doUninstallSkill = async (skill: UnifiedSkill) => {
    const key = `${skill.source}/${skill.id || skill.name}`;
    setBusyKey(key);
    try {
      await uninstallStoreSkill(skill.source as SkillSource, skill.id || skill.name);
      addToast(zh ? `已卸载 ${skill.display_name || skill.name}` : `Uninstalled ${skill.display_name || skill.name}`, 'success');
      qc.invalidateQueries({ queryKey: ['store-installed'] });
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const doInstallMcp = async (item: UnifiedMCPStoreItem) => {
    setBusyKey(item.id);
    try {
      const r = await installMCPFromStore(item.source, item.id);
      if (r.success) {
        addToast(zh ? `已接入 ${item.display_name || item.name}` : `Connected ${item.display_name || item.name}`, 'success');
        qc.invalidateQueries({ queryKey: ['mcp-servers'] });
        qc.invalidateQueries({ queryKey: ['mcp-status'] });
      } else {
        addToast(r.message || (zh ? '安装失败' : 'Install failed'), 'error');
      }
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusyKey(null);
    }
  };

  const runUrlReview = async () => {
    if (!urlValue.trim()) return;
    setUrlBusy(true);
    setUrlReview(null);
    try {
      const r = await reviewSkillUrl(urlValue.trim());
      setUrlReview(r);
      if (!r.ok) addToast(r.error || (zh ? '审查失败' : 'Review failed'), 'error');
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setUrlBusy(false);
    }
  };

  const confirmUrlInstall = async (force = false) => {
    if (!urlValue.trim() || !urlReview?.installable) return;
    setUrlBusy(true);
    try {
      const r = await installSkillFromUrl(urlValue.trim(), urlReview.name, force);
      if (r.success) {
        addToast(zh ? `已安装 ${r.skill_id}` : `Installed ${r.skill_id}`, 'success');
        qc.invalidateQueries({ queryKey: ['store-installed'] });
        setUrlOpen(false);
        setUrlValue('');
        setUrlReview(null);
      } else {
        const msg = r.error || (zh ? '安装失败' : 'Install failed');
        if (/already installed/i.test(msg) && !force) {
          // 同名已存在：提示可强制覆盖
          addToast(zh ? `${msg} — 再次点击「强制覆盖」` : `${msg} — click Force overwrite`, 'error');
          setUrlReview({ ...urlReview, error: msg });
        } else {
          addToast(msg, 'error');
        }
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      const msg = String(detail || e);
      if (/already installed/i.test(msg)) {
        setUrlReview((prev) => (prev ? { ...prev, error: msg } : prev));
      }
      // axios interceptor 已 toast
    } finally {
      setUrlBusy(false);
    }
  };

  return (
    <AdvancedShell
      titleZh="扩展市场是高级能力"
      titleEn="Extensions marketplace is advanced"
      hintZh="主路径不依赖装扩展。技能/MCP 在此安装。"
      hintEn="Spine does not require the market. Install skills/MCP here."
    >
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '扩展' : 'Extensions'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {zh ? '网上看到什么有意思的，装进来 · 技能与 MCP 服务' : 'Install what you find · skills & MCP servers'}
          </div>
        </div>
        <button onClick={() => setUrlOpen(true)} style={btnPrimary}>
          + {zh ? '从链接添加' : 'Add from URL'}
        </button>
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <Chip active={tab === 'skills'} onClick={() => setTab('skills')}>{zh ? '技能市场' : 'Skill market'}</Chip>
        <Chip active={tab === 'mcp'} onClick={() => setTab('mcp')}>{zh ? 'MCP 服务' : 'MCP servers'}</Chip>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={zh ? '搜索技能 / 服务…' : 'Search skills / servers…'}
          style={{
            marginLeft: 'auto', background: 'var(--input-bg)', border: '1px solid var(--border-subtle)',
            borderRadius: 8, padding: '6px 11px', color: 'var(--foreground)', fontSize: 12, outline: 'none', width: 220,
          }}
        />
      </div>

      {tab === 'skills' ? (
        <>
          {skillsQ.isLoading ? (
            <div style={{ ...card, textAlign: 'center', padding: 40, color: 'var(--foreground-dim)', fontSize: 12.5 }}>Loading…</div>
          ) : skills.length === 0 ? (
            <div style={{ ...card, textAlign: 'center', padding: '48px 20px' }}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>📦</div>
              <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)' }}>
                {zh ? '市场暂无结果' : 'No skills found'}
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
              {skills.map((s) => {
                const key = `${s.source}/${s.id || s.name}`;
                const installed = installedSet.has(`${s.source}/${s.name}`) || installedSet.has(key);
                const busy = busyKey === key;
                return (
                  <div key={key} style={card}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', fontFamily: 'var(--font-mono)' }}>
                        {s.display_name || s.name}
                      </div>
                      {installed ? (
                        <span style={tagGreen}>{zh ? '已安装' : 'Installed'}</span>
                      ) : null}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6 }}>
                      {s.author || s.source} · {formatCount(s.stats?.installs || s.stats?.downloads || 0)} {zh ? '次安装' : 'installs'}
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.5, minHeight: 40 }}>
                      {(s.summary || s.description || '—').slice(0, 120)}
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
                      {installed ? (
                        <>
                          <button style={btnGhost} onClick={() => setInstallSkill(s)}>{zh ? '详情' : 'Details'}</button>
                          <button style={btnGhostRed} disabled={busy} onClick={() => doUninstallSkill(s)}>
                            {zh ? '卸载' : 'Uninstall'}
                          </button>
                        </>
                      ) : (
                        <>
                          <button style={btnPrimarySm} disabled={busy} onClick={() => setInstallSkill(s)}>
                            {zh ? '安装' : 'Install'}
                          </button>
                          <button style={btnGhost} onClick={() => setInstallSkill(s)}>
                            {zh ? '审查' : 'Review'}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 18, lineHeight: 1.55 }}>
            {zh
              ? '安装 = 发给 Agent 学习。技能契约（skill.yaml）声明工具白名单，越界调用会被 kernel mediate 拦截。'
              : 'Install = teach the agent. skill.yaml declares tool whitelist; kernel mediates out-of-bounds calls.'}
          </div>
        </>
      ) : (
        <>
          {/* 已接入 MCP */}
          {mcpServers.length > 0 ? (
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>
                {zh ? '已接入' : 'Connected'}{' '}
                <span style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--foreground-dim)' }}>{mcpServers.length}</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
                {mcpServers.map((s: MCPServer) => {
                  const st = statusMap.get(s.name);
                  const ok = st?.connected === true || s.enabled;
                  return (
                    <div key={s.id || s.name} style={card}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ fontSize: 13, fontWeight: 650, fontFamily: 'var(--font-mono)', color: 'var(--foreground)' }}>{s.name}</div>
                        <span style={ok ? tagGreen : tagRed}>{ok ? (zh ? '已连接' : 'connected') : (zh ? '断开' : 'offline')}</span>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6, fontFamily: 'var(--font-mono)' }}>
                        {s.transport || s.command || s.url || '—'}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginTop: 8 }}>
                        {s.description || (zh ? 'MCP 服务' : 'MCP server')}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8 }}>
                        {zh ? '暴露工具' : 'Tools'} · {st?.tool_count ?? '—'}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* 商店目录 */}
          <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>
            {zh ? '精选目录' : 'Catalog'}
          </div>
          {mcpStoreQ.isLoading ? (
            <div style={{ ...card, textAlign: 'center', padding: 40, color: 'var(--foreground-dim)', fontSize: 12.5 }}>Loading…</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
              {mcpStoreItems.map((item) => {
                const already = mcpServers.some((s) => s.name === item.name);
                return (
                  <div key={`${item.source}/${item.id}`} style={card}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 650, fontFamily: 'var(--font-mono)', color: 'var(--foreground)' }}>
                        {item.display_name || item.name}
                      </div>
                      {already ? <span style={tagGreen}>{zh ? '已有' : 'Added'}</span> : null}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6 }}>{item.source}</div>
                    <div style={{ fontSize: 12.5, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.5, minHeight: 40 }}>
                      {(item.description || item.summary || '—').slice(0, 120)}
                    </div>
                    <div style={{ marginTop: 12 }}>
                      <button
                        style={btnPrimarySm}
                        disabled={already || busyKey === item.id}
                        onClick={() => doInstallMcp(item)}
                      >
                        {already ? (zh ? '已接入' : 'Added') : (zh ? '安装' : 'Install')}
                      </button>
                    </div>
                  </div>
                );
              })}
              <a href="/mcp" style={{
                ...card, display: 'flex', alignItems: 'center', justifyContent: 'center',
                minHeight: 140, textDecoration: 'none', color: 'var(--foreground-dim)', fontSize: 13,
              }}>
                + {zh ? '手动添加 MCP 服务' : 'Add MCP manually'}
              </a>
            </div>
          )}
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 18, lineHeight: 1.55 }}>
            {zh
              ? 'MCP 服务接入后，其工具进入能力体系——授予 Agent 需在「编辑配置」中显式声明。'
              : 'After MCP connect, tools enter the capability system — grant them via Edit config.'}
          </div>
        </>
      )}

      {/* 从链接添加 + 安全审查 */}
      {urlOpen ? (
        <Modal onClose={() => { setUrlOpen(false); setUrlReview(null); }} wide>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '从链接添加' : 'Add from URL'}
          </div>
          <div style={{ marginTop: 14 }}>
            <label style={{ display: 'block', fontSize: 11.5, color: 'var(--foreground-dim)', marginBottom: 5 }}>
              {zh ? '链接' : 'URL'}
            </label>
            <input
              autoFocus
              value={urlValue}
              onChange={(e) => { setUrlValue(e.target.value); setUrlReview(null); }}
              placeholder="https://github.com/xxx/skill/blob/main/SKILL.md"
              style={inputStyle}
            />
            <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 6, lineHeight: 1.5 }}>
              {zh
                ? '支持 GitHub 直链 / raw / skill.yaml。先审查再安装；内网与高危模式会被拦截。'
                : 'GitHub blob/raw / skill.yaml. Review first; private nets & dangerous patterns blocked.'}
            </div>
          </div>

          {urlReview ? (
            <div style={{ marginTop: 14, ...card, padding: 12 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                <span style={{
                  fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 6,
                  color: urlReview.risk === 'safe' || urlReview.risk === 'low' ? 'var(--status-online)'
                    : urlReview.risk === 'dangerous' ? 'var(--status-offline)' : '#c9a05e',
                  background: 'var(--input-bg)',
                }}>{urlReview.risk || '—'}</span>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)' }}>
                  {urlReview.name || '—'}
                </span>
                <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginLeft: 'auto' }}>
                  {urlReview.installable ? (zh ? '可安装' : 'installable') : (zh ? '不可安装' : 'blocked')}
                </span>
              </div>
              {(urlReview.tools ?? []).length > 0 ? (
                <div style={{ fontSize: 11, color: 'var(--foreground-muted)', marginBottom: 6 }}>
                  tools: {(urlReview.tools ?? []).join(', ')}
                </div>
              ) : null}
              {(urlReview.findings ?? []).slice(0, 6).map((f, i) => (
                <div key={i} style={{ fontSize: 11, color: 'var(--foreground-dim)', padding: '2px 0' }}>
                  [{f.severity}] {f.tag} — {f.detail}
                </div>
              ))}
              {urlReview.error ? (
                <div style={{ fontSize: 11, color: 'var(--status-offline)', marginTop: 6 }}>{urlReview.error}</div>
              ) : null}
              {urlReview.preview ? (
                <pre style={{
                  marginTop: 8, padding: 10, borderRadius: 8, background: 'var(--input-bg)',
                  fontSize: 10.5, maxHeight: 120, overflow: 'auto', color: 'var(--foreground-dim)',
                  fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap',
                }}>{urlReview.preview.slice(0, 800)}</pre>
              ) : null}
            </div>
          ) : null}

          <div style={{ display: 'flex', gap: 8, marginTop: 18, justifyContent: 'flex-end' }}>
            <button style={btnGhost} onClick={() => { setUrlOpen(false); setUrlReview(null); }}>{zh ? '取消' : 'Cancel'}</button>
            {!urlReview ? (
              <button style={btnPrimary} disabled={urlBusy || !urlValue.trim()} onClick={runUrlReview}>
                {urlBusy ? (zh ? '审查中…' : 'Reviewing…') : (zh ? '安全审查' : 'Security review')}
              </button>
            ) : (
              <>
                <button
                  style={btnPrimary}
                  disabled={urlBusy || !urlReview.installable}
                  onClick={() => confirmUrlInstall(false)}
                >
                  {urlBusy ? (zh ? '安装中…' : 'Installing…') : (zh ? '确认安装' : 'Confirm install')}
                </button>
                {urlReview.error && /already installed/i.test(urlReview.error) ? (
                  <button
                    style={btnGhostRed}
                    disabled={urlBusy}
                    onClick={() => confirmUrlInstall(true)}
                  >
                    {zh ? '强制覆盖' : 'Force overwrite'}
                  </button>
                ) : null}
              </>
            )}
          </div>
        </Modal>
      ) : null}

      {/* 安装/审查技能 */}
      {installSkill ? (
        <Modal onClose={() => setInstallSkill(null)} wide>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '安装技能' : 'Install skill'}：{installSkill.display_name || installSkill.name}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--foreground-muted)', marginTop: 10, lineHeight: 1.55 }}>
            {installSkill.summary || installSkill.description}
          </div>
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginBottom: 6 }}>
              {zh ? '能力声明审查' : 'Capability review'}
            </div>
            <div style={{
              ...card, padding: 12, fontSize: 11, fontFamily: 'var(--font-mono)',
              color: 'var(--foreground-dim)', maxHeight: 140, overflow: 'auto', lineHeight: 1.55,
            }}>
              source: {installSkill.source}{'\n'}
              version: {installSkill.version || '—'}{'\n'}
              topics: {(installSkill.topics || installSkill.tags || []).join(', ') || '—'}{'\n'}
              license: {installSkill.license || '—'}{'\n'}
              url: {installSkill.source_url || installSkill.skill_md_url || '—'}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 6 }}>
              {zh
                ? '声明的工具将并入 Agent 能力白名单，越界调用由 kernel mediate 拦截。'
                : 'Declared tools join capability whitelist; mediate intercepts out-of-bounds calls.'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 18, justifyContent: 'flex-end' }}>
            <button style={btnGhost} onClick={() => setInstallSkill(null)}>{zh ? '取消' : 'Cancel'}</button>
            {installedSet.has(`${installSkill.source}/${installSkill.name}`) ? null : (
              <button
                style={btnPrimary}
                disabled={busyKey === `${installSkill.source}/${installSkill.id || installSkill.name}`}
                onClick={() => doInstallSkill(installSkill)}
              >
                {zh ? '确认安装' : 'Confirm install'}
              </button>
            )}
          </div>
        </Modal>
      ) : null}
    </div>
    </AdvancedShell>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '5px 14px', borderRadius: 999, fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer',
      border: active ? '1px solid var(--brand-purple)' : '1px solid var(--border-subtle)',
      background: active ? 'color-mix(in srgb, var(--brand-purple) 12%, transparent)' : 'transparent',
      color: active ? 'var(--brand-purple)' : 'var(--foreground-dim)',
    }}>{children}</button>
  );
}

function Modal({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 96, background: 'var(--mask, rgba(10,9,7,0.6))', backdropFilter: 'blur(4px)' }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: wide ? 560 : 480, maxWidth: '94vw', zIndex: 99,
        background: 'var(--elevated-bg)', border: '1px solid var(--border-default)',
        borderRadius: 16, boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.6))',
        padding: '22px 24px',
      }}>{children}</div>
    </>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', padding: '14px 16px', boxShadow: 'var(--glass-inner)',
};
const btnPrimary: React.CSSProperties = {
  padding: '7px 16px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
  boxShadow: '0 2px 10px color-mix(in srgb, var(--brand-purple) 30%, transparent)',
};
const btnPrimarySm: React.CSSProperties = { ...btnPrimary, padding: '4px 12px', fontSize: 11.5, borderRadius: 7 };
const btnGhost: React.CSSProperties = {
  padding: '4px 10px', borderRadius: 7,
  border: '1px solid var(--border-subtle)', background: 'transparent',
  color: 'var(--foreground-muted)', fontSize: 11.5, fontWeight: 500, cursor: 'pointer',
};
const btnGhostRed: React.CSSProperties = {
  ...btnGhost,
  color: 'var(--status-offline)',
  borderColor: 'color-mix(in srgb, var(--status-offline) 35%, transparent)',
};
const tagGreen: React.CSSProperties = {
  fontSize: 10.5, fontWeight: 600, padding: '2px 8px', borderRadius: 6,
  color: 'var(--status-online)',
  background: 'color-mix(in srgb, var(--status-online) 10%, transparent)',
  border: '1px solid color-mix(in srgb, var(--status-online) 25%, transparent)',
};
const tagRed: React.CSSProperties = {
  ...tagGreen,
  color: 'var(--status-offline)',
  background: 'color-mix(in srgb, var(--status-offline) 10%, transparent)',
  border: '1px solid color-mix(in srgb, var(--status-offline) 25%, transparent)',
};
const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--input-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 8, padding: '8px 11px', color: 'var(--foreground)', fontSize: 13, outline: 'none',
};
