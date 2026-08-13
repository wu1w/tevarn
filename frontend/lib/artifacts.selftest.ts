/**
 * artifacts 抽取烟测（node + tsx）
 * 运行：cd frontend && npx tsx lib/artifacts.selftest.ts
 */
import { extractArtifacts, normalizeArtifactPath } from './artifacts';

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(normalizeArtifactPath('sandbox:workspace/a.png')?.endsWith('a.png'), 'sandbox path');
assert(normalizeArtifactPath('https://evil.com/x.png') === null, 'reject http');
assert(normalizeArtifactPath('foo/bar') === null, 'reject no ext');

const arts = extractArtifacts({
  content:
    '写好了 [表](workspace/out/report.xlsx)，另见 data/out.csv 以及 VPN第一次登录时间统计.xlsx',
  tool_calls: [
    {
      name: 'file_write',
      arguments: { path: 'notes/hello.md' },
      result: '{"ok":true,"path":"notes/hello.md"}',
    },
  ],
});

assert(arts.some((a) => a.name.includes('report') || a.path.includes('report')), 'md link');
assert(arts.some((a) => a.path.includes('hello.md')), 'tool path');
assert(arts.some((a) => a.kind === 'table'), 'table kind');

const visio = extractArtifacts({ content: '见 docs/网络拓扑.vsd 以及 flow.vsdx' });
assert(visio.some((a) => a.kind === 'visio'), 'visio kind');
console.log('artifacts.selftest OK', arts.length, arts.map((a) => a.name).join(', '));
