/**
 * 完成后残留 content/tools 不得当成「还在思考」。
 */
import assert from 'node:assert/strict';

function isActiveStream(s) {
  return Boolean(s && (s.agentRunning || s.isStreaming));
}

assert.equal(isActiveStream(null), false);
assert.equal(
  isActiveStream({
    isStreaming: false,
    agentRunning: false,
    content: 'leftover',
    tools: [{ name: 'command' }],
    statusDetail: '思考中…',
  }),
  false,
);
assert.equal(
  isActiveStream({ isStreaming: true, agentRunning: false, content: '', tools: [] }),
  true,
);
assert.equal(
  isActiveStream({ isStreaming: false, agentRunning: true, content: '', tools: [] }),
  true,
);
console.log('streamSessionActive.test.mjs ok');
