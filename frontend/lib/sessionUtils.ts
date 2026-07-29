/**
 * 会话产品分层：
 * - 人话对话（主人 ↔ CEO / 联系员工）→ 「最近对话」
 * - 员工工单执行会话（dispatcher 创建，source=workforce）→ 「今日任务记录」详情，不占对话列表
 */

import type { Session, SessionConfig } from '@/types';

export function sessionConfigOf(s: Session | { config?: SessionConfig | Record<string, unknown> | null }): Record<string, unknown> {
  const c = s?.config;
  if (c && typeof c === 'object') return c as Record<string, unknown>;
  return {};
}

/** 员工后台干活的会话（不进「最近对话」） */
export function isWorkforceSession(s: Session | { config?: SessionConfig | Record<string, unknown> | null }): boolean {
  const c = sessionConfigOf(s);
  if (c.source === 'workforce') return true;
  if (c.workforce === true || c.workforce === 1) return true;
  if (typeof c.workforce_identity_id === 'string' && c.workforce_identity_id) return true;
  return false;
}

/** 主人可继续聊的会话 */
export function isHumanChatSession(s: Session): boolean {
  return !isWorkforceSession(s);
}

export function humanChatSessions(sessions: Session[] | null | undefined): Session[] {
  return (sessions ?? []).filter(isHumanChatSession);
}

export function workforceSessions(sessions: Session[] | null | undefined): Session[] {
  return (sessions ?? []).filter(isWorkforceSession);
}
