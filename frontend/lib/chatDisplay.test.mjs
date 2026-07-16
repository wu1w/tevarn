/**
 * 轻量验证 prepareMessagesForDisplay 配对契约
 * （与 lib/chatDisplay.ts 逻辑对齐的可运行 harness）
 */
import assert from 'node:assert/strict';

function tryParseJson(content) {
  if (!content) return null;
  const t = content.trim();
  if (!(t.startsWith('{') || t.startsWith('['))) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

function extractToolMeta(message) {
  const list = message.tool_calls;
  if (!list?.length) return { callId: null, name: null };
  const first = list[0];
  const callId = first.tool_call_id || first.id || null;
  const name = typeof first.name === 'string' ? first.name : null;
  return { callId, name };
}

function prepareMessagesForDisplay(messages) {
  const resultByCallId = new Map();
  const toolMsgIdByCallId = new Map();
  for (const m of messages) {
    if (m.role !== 'tool') continue;
    const { callId } = extractToolMeta(m);
    if (callId && m.content != null) {
      resultByCallId.set(callId, m.content);
      toolMsgIdByCallId.set(callId, m.id);
    }
  }
  const pairedToolMsgIds = new Set();
  for (const m of messages) {
    if (m.role !== 'assistant' || !m.tool_calls?.length) continue;
    for (const tc of m.tool_calls) {
      if (tc.id && toolMsgIdByCallId.has(tc.id)) {
        pairedToolMsgIds.add(toolMsgIdByCallId.get(tc.id));
      }
    }
  }
  return messages.map((m) => {
    if (m.role === 'tool' && pairedToolMsgIds.has(m.id)) {
      return { ...m, _hidden: true };
    }
    if (m.role === 'assistant' && m.tool_calls?.length) {
      return {
        ...m,
        tool_calls: m.tool_calls.map((tc) => {
          const result = tc.id ? resultByCallId.get(tc.id) : undefined;
          let status = 'completed';
          if (result !== undefined) {
            const failed =
              /^\[Error\]/i.test(result) || tryParseJson(result)?.ok === false;
            status = failed ? 'failed' : 'completed';
          }
          return { ...tc, result, status };
        }),
      };
    }
    return { ...m };
  });
}

const messages = [
  {
    id: 'a1',
    role: 'assistant',
    content: null,
    tool_calls: [{ id: 'call_1', name: 'manage_goal', arguments: { action: 'create' } }],
  },
  {
    id: 't1',
    role: 'tool',
    content: JSON.stringify({ ok: true, message: 'Goal created' }),
    tool_calls: [{ tool_call_id: 'call_1', name: 'manage_goal' }],
  },
  {
    id: 't_orphan',
    role: 'tool',
    content: '{"orphan":true}',
    tool_calls: [{ tool_call_id: 'call_missing', name: 'other' }],
  },
];

const out = prepareMessagesForDisplay(messages);
const assistant = out.find((m) => m.id === 'a1');
const tool = out.find((m) => m.id === 't1');
const orphan = out.find((m) => m.id === 't_orphan');

assert.equal(tool._hidden, true, 'paired tool should be hidden');
assert.equal(orphan._hidden, undefined, 'orphan tool should remain visible');
assert.ok(assistant.tool_calls[0].result.includes('Goal created'), 'result merged');
assert.equal(assistant.tool_calls[0].status, 'completed');

const failed = prepareMessagesForDisplay([
  {
    id: 'a2',
    role: 'assistant',
    content: '',
    tool_calls: [{ id: 'c2', name: 'x', arguments: {} }],
  },
  {
    id: 't2',
    role: 'tool',
    content: '[Error] boom',
    tool_calls: [{ tool_call_id: 'c2', name: 'x' }],
  },
]);
assert.equal(failed[0].tool_calls[0].status, 'failed');

console.log('chatDisplay pairing: PASS');
