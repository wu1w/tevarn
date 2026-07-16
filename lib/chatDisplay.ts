/**
 * Chat 展示层工具：消息配对、工具结果美化、错误识别
 */
import type { Message, ToolCall } from '@/types';

export interface DisplayToolCall extends ToolCall {
  result?: string;
  status?: 'running' | 'completed' | 'failed';
}

export interface DisplayMessage extends Message {
  tool_calls: DisplayToolCall[] | null;
  /** 该 tool 消息已被合并进 assistant 的 tool_calls，列表中隐藏 */
  _hidden?: boolean;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

type LooseTc = ToolCall & {
  tool_call_id?: string;
  function?: { name?: string; arguments?: string | Record<string, unknown> };
};

/** 从 tool / assistant 消息的 tool_calls 元数据里取出 call id / name / args */
export function extractToolMeta(message: Message): {
  callId: string | null;
  name: string | null;
} {
  const list = message.tool_calls;
  if (!list?.length) return { callId: null, name: null };
  const first = list[0] as LooseTc;
  const callId =
    (typeof first.tool_call_id === 'string' && first.tool_call_id) ||
    (typeof first.id === 'string' && first.id) ||
    null;
  const name =
    (typeof first.name === 'string' && first.name) ||
    (typeof first.function?.name === 'string' && first.function.name) ||
    null;
  return { callId, name };
}

function normalizeToolCall(tc: ToolCall): DisplayToolCall {
  const loose = tc as LooseTc;
  const id = loose.id || loose.tool_call_id || '';
  const name =
    loose.name ||
    (typeof loose.function?.name === 'string' ? loose.function.name : '') ||
    'tool';
  let args: Record<string, unknown> = {};
  if (asRecord(loose.arguments)) {
    args = asRecord(loose.arguments)!;
  } else if (typeof loose.arguments === 'string') {
    args = (tryParseJson(loose.arguments) as Record<string, unknown>) || {};
  } else if (loose.function?.arguments) {
    if (typeof loose.function.arguments === 'string') {
      args = (tryParseJson(loose.function.arguments) as Record<string, unknown>) || {};
    } else if (asRecord(loose.function.arguments)) {
      args = asRecord(loose.function.arguments)!;
    }
  }
  return {
    id,
    name,
    arguments: args,
    result: loose.result,
    status: loose.status,
  };
}

/** 是否为 Goal 相关工具（进度已在任务看板，聊天区不必再堆 JSON） */
export function isGoalToolMessage(message: Message): boolean {
  const { name } = extractToolMeta(message);
  if (name === 'manage_goal') return true;
  const parsed = tryParseJson(message.content);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return false;
  const o = parsed as Record<string, unknown>;
  if ('goal' in o) return true;
  const msg = typeof o.message === 'string' ? o.message : '';
  return /goal/i.test(msg) || /todos?/i.test(msg);
}

/** 尝试把 content 解析为 JSON 对象 */
export function tryParseJson(content: string | null | undefined): unknown | null {
  if (!content) return null;
  const t = content.trim();
  if (!t) return null;
  if (!(t.startsWith('{') || t.startsWith('['))) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

/** 美化工具结果：JSON 缩进；过长截断仅用于摘要 */
export function formatToolResultForDisplay(
  content: string | null | undefined,
  maxLen = 8000
): { text: string; isJson: boolean; truncated: boolean } {
  if (content == null || content === '') {
    return { text: '(空结果)', isJson: false, truncated: false };
  }
  const parsed = tryParseJson(content);
  if (parsed !== null) {
    const pretty = JSON.stringify(parsed, null, 2);
    if (pretty.length > maxLen) {
      return {
        text: pretty.slice(0, maxLen) + '\n… (已截断)',
        isJson: true,
        truncated: true,
      };
    }
    return { text: pretty, isJson: true, truncated: false };
  }
  if (content.length > maxLen) {
    return {
      text: content.slice(0, maxLen) + '\n… (已截断)',
      isJson: false,
      truncated: true,
    };
  }
  return { text: content, isJson: false, truncated: false };
}

/** 从工具结果 JSON 抽一句人类可读摘要 */
export function summarizeToolResult(
  content: string | null | undefined,
  toolName?: string | null
): string {
  if (!content) return toolName ? `${toolName} 完成` : '工具执行完成';
  const parsed = tryParseJson(content);
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const o = parsed as Record<string, unknown>;
    if (typeof o.message === 'string' && o.message.trim()) return o.message.trim();
    if (typeof o.error === 'string' && o.error.trim()) return `错误: ${o.error}`;
    if (o.ok === true && toolName) return `${toolName} 成功`;
    if (o.ok === false && toolName) return `${toolName} 失败`;
    if (typeof o.status === 'string') return String(o.status);
  }
  const oneLine = content.replace(/\s+/g, ' ').trim();
  return oneLine.length > 100 ? oneLine.slice(0, 100) + '…' : oneLine;
}

export function isErrorContent(content: string | null | undefined): boolean {
  if (!content) return false;
  return (
    /^\[Error\]/i.test(content.trim()) ||
    /^Error:/i.test(content.trim()) ||
    content.includes('LLM 服务失败') ||
    content.includes('LLM service failed')
  );
}

/**
 * 将后续 tool 消息的结果合并进前序 assistant.tool_calls，
 * 已配对 / Goal 工具消息标记 _hidden，避免再堆一坨原始 JSON 气泡。
 */
export function prepareMessagesForDisplay(messages: Message[]): DisplayMessage[] {
  const resultByCallId = new Map<string, string>();
  const toolMsgIdByCallId = new Map<string, string>();

  for (const m of messages) {
    if (m.role !== 'tool') continue;
    const { callId } = extractToolMeta(m);
    if (callId && m.content != null) {
      resultByCallId.set(callId, m.content);
      toolMsgIdByCallId.set(callId, m.id);
    }
  }

  const pairedToolMsgIds = new Set<string>();
  for (const m of messages) {
    if (m.role !== 'assistant' || !m.tool_calls?.length) continue;
    for (const tc of m.tool_calls) {
      const n = normalizeToolCall(tc);
      if (n.id && toolMsgIdByCallId.has(n.id)) {
        pairedToolMsgIds.add(toolMsgIdByCallId.get(n.id)!);
      }
    }
  }

  return messages.map((m) => {
    // Goal 工具结果：只在任务看板展示
    if (m.role === 'tool' && isGoalToolMessage(m)) {
      return { ...m, _hidden: true };
    }
    if (m.role === 'tool' && pairedToolMsgIds.has(m.id)) {
      return { ...m, _hidden: true };
    }
    if (m.role === 'assistant' && m.tool_calls?.length) {
      const enriched: DisplayToolCall[] = m.tool_calls.map((tc) => {
        const base = normalizeToolCall(tc);
        // manage_goal 不在卡片里塞大段 JSON
        if (base.name === 'manage_goal') {
          return {
            ...base,
            result: undefined,
            status: 'completed',
            arguments: {},
          };
        }
        const result = base.id ? resultByCallId.get(base.id) : undefined;
        let status: DisplayToolCall['status'] = 'completed';
        if (result !== undefined) {
          const failed =
            /^\[Error\]/i.test(result) ||
            (tryParseJson(result) as { ok?: boolean } | null)?.ok === false;
          status = failed ? 'failed' : 'completed';
        }
        return { ...base, result, status };
      });
      return { ...m, tool_calls: enriched };
    }
    return { ...m };
  });
}
