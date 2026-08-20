/**
 * Cheap node harness for parseMessageContent (no vitest/jest).
 * Run: node --experimental-strip-types lib/parseMessageContent.test.mjs
 */
import assert from 'node:assert/strict';
import { parseMessageContent } from './parseMessageContent.ts';

const leading = parseMessageContent(
  '<thinking>\nsecret plan\n</thinking>\n\n可见正文'
);
assert.equal(leading.thinking, 'secret plan');
assert.equal(leading.body, '可见正文');
assert.equal(leading.thinkingOpen, false);

const prose = [
  'Hermes 侧：',
  '- 把更早的 `<think>` 从发给模型的历史里删掉',
  '- 闭合的 `</think>` 也不该吞掉后文',
  '',
  '## 3. 体验层',
  '',
  'KEEP_TAIL_SECTION_AFTER_THINK_MENTION',
  '',
  '## 4. 下一节',
  '## 5. 最后',
  '请贴 Hermes 配置。',
].join('\n');
const parsed = parseMessageContent(prose);
assert.equal(parsed.thinkingOpen, false);
assert.ok(parsed.body.includes('`<think>`'), parsed.body);
assert.ok(parsed.body.includes('KEEP_TAIL_SECTION_AFTER_THINK_MENTION'));
assert.ok(parsed.body.includes('## 5. 最后'));
assert.ok(parsed.body.includes('请贴 Hermes 配置。'));
assert.ok(parsed.body.indexOf('KEEP_TAIL_SECTION_AFTER_THINK_MENTION') > parsed.body.indexOf('`<think>`'));

const fenced = parseMessageContent(
  'before\n```\nconst x = "<think>not real</think>";\n```\n## after fence\nTAIL'
);
assert.ok(fenced.body.includes('<think>not real</think>'));
assert.ok(fenced.body.includes('TAIL'));
assert.equal(fenced.thinkingOpen, false);

const openStream = parseMessageContent('<thinking>\nhalf done');
assert.equal(openStream.thinkingOpen, true);
assert.ok((openStream.thinking || '').includes('half done'));
assert.equal(openStream.body, '');

console.log('parseMessageContent.test.mjs ok');
