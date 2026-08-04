/**
 * Agents 模块共享工具
 */

export const GRADS: Array<[string, string]> = [
  ['#7e9e6a', '#5c7a4c'], ['#699682', '#4f7d6a'], ['#7a98b0', '#5b7d94'],
  ['#8ab06a', '#648550'], ['#c9a05e', '#a67c3e'], ['#a89bbf', '#857a9e'], ['#c0785e', '#9e5a42'],
];

export function gradOf(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const [a, b] = GRADS[h % GRADS.length];
  return `linear-gradient(135deg, ${a}, ${b})`;
}

export const ST_TEXT: Record<string, string> = {
  running: '运行', idle: '待命', waiting: '等待', suspended: '挂起',
  active: '运行', done: '完成', failed: '失败', archived: '已归档',
};

export function stColor(st: string): string {
  if (st === 'running' || st === 'active') return 'var(--status-online)';
  if (st === 'suspended' || st === 'failed') return 'var(--status-offline)';
  if (st === 'waiting') return 'var(--sem-warn)'; // audit-fix: P1 语义色 token 化
  return 'var(--foreground-dim)';
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** 进程 identity 常见为 wf:{uuid}，卡片/抽屉不能只比 name */
export function processBelongsToAgent(
  p: { identity?: string | null },
  agent: { id: string; name: string },
): boolean {
  const id = String(p.identity || '');
  if (!id) return false;
  if (id === agent.name) return true;
  const aid = String(agent.id || '');
  if (!aid) return false;
  if (id === `wf:${aid}`) return true;
  // 兼容短前缀 / 无连字符
  const compact = aid.replace(/-/g, '');
  if (id === `wf:${compact}`) return true;
  if (id.startsWith(`wf:${aid}`) || id.startsWith(`wf:${aid.slice(0, 8)}`)) return true;
  if (id.includes(aid) || (compact.length > 8 && id.includes(compact))) return true;
  return false;
}

export function sumAgentTokens(
  processes: Array<{ identity?: string | null; tokens_used?: number | null }>,
  agent: { id: string; name: string },
): number {
  return processes
    .filter((p) => processBelongsToAgent(p, agent))
    .reduce((s, p) => s + (Number(p.tokens_used) || 0), 0);
}

export function pickAgentProcess<T extends { identity?: string | null; state?: string; tokens_used?: number | null }>(
  processes: T[],
  agent: { id: string; name: string },
): T | undefined {
  const mine = processes.filter((p) => processBelongsToAgent(p, agent));
  if (!mine.length) return undefined;
  const live = mine.find((p) => p.state === 'running' || p.state === 'waiting' || p.state === 'suspended');
  if (live) return live;
  // 取用量最大的终态，避免卡片永远 0
  return [...mine].sort((a, b) => (Number(b.tokens_used) || 0) - (Number(a.tokens_used) || 0))[0];
}

/** 可勾选能力池（对应 kernel mediate 的工具白名单语义） */
export const CAP_POOL: Array<{ id: string; zh: string; en: string }> = [
  { id: 'file_rw', zh: '文件读写', en: 'File read/write' },
  { id: 'command', zh: '命令执行', en: 'Command exec' },
  { id: 'web_search', zh: '联网检索', en: 'Web search' },
  { id: 'git', zh: '代码仓库', en: 'Git repo' },
  { id: 'browser', zh: '浏览器', en: 'Browser' },
  { id: 'calendar', zh: '日程', en: 'Calendar' },
  { id: 'db_read', zh: '数据库只读', en: 'DB read-only' },
  { id: 'notify', zh: '消息通知', en: 'Notify' },
];
