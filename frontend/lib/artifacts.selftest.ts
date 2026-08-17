/**
 * artifacts 抽取烟测（node + tsx）
 * 运行：cd frontend && npx tsx lib/artifacts.selftest.ts
 */
import {
  collectSessionArtifacts,
  extractArtifacts,
  isInternalRuntimePath,
  isScratchOrProcessFile,
  isUserFacingArtifactPath,
  normalizeArtifactPath,
} from './artifacts';

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(normalizeArtifactPath('sandbox:workspace/a.png')?.endsWith('a.png'), 'sandbox path');
assert(normalizeArtifactPath('https://evil.com/x.png') === null, 'reject http');
assert(normalizeArtifactPath('foo/bar') === null, 'reject no ext');
assert(normalizeArtifactPath('uploads/shot.png')?.includes('shot.png'), 'uploads');

// 过程目录 / 临时脚本不得进对话文件
assert(isInternalRuntimePath('.tevarn/file-history/x/chk_aa.json'), 'tevarn hist');
assert(isInternalRuntimePath('.computers/main/home/.tevarn/process_snapshots/a.json'), 'computers');
assert(isInternalRuntimePath('backend/__pycache__/loop.cpython-312.pyc'), 'pycache');
assert(normalizeArtifactPath('.tevarn/checkpoints/hello.txt') === null, 'reject checkpoint');
assert(normalizeArtifactPath('.computers/main/home/.tevarn/tool_results/a.txt') === null, 'reject tool_results');
assert(normalizeArtifactPath('C:\\Users\\wuyw\\tevarn\\.tevarn\\logs\\tevarn.log') === null, 'reject win abs tevarn');
assert(normalizeArtifactPath('C:\\Users\\wuyw\\AppData\\Local\\Temp\\probe.py') === null, 'reject unmapped abs');
assert(isScratchOrProcessFile('_probe.py'), 'probe');
assert(isScratchOrProcessFile('tmp/dump.ps1'), 'dump ps1');
assert(isScratchOrProcessFile('_review_list.py'), 'review list');
assert(!isUserFacingArtifactPath('src/lib.rs', 'content'), 'no bare source from prose');
assert(isUserFacingArtifactPath('src/lib.rs', 'tool'), 'write tool source ok');
assert(isUserFacingArtifactPath('out/report.xlsx', 'content'), 'xlsx deliverable');

const arts = extractArtifacts({
  content:
    '写好了 [表](workspace/out/report.xlsx)，另见 data/out.csv 以及 VPN第一次登录时间统计.xlsx',
  tool_calls: [
    {
      name: 'file_write',
      arguments: { path: 'notes/hello.md' },
      result: '{"ok":true,"path":"notes/hello.md"}',
    },
    {
      name: 'file_read',
      arguments: { path: '.tevarn/file-history/s/chk_abc.json' },
      result: '{"path":".tevarn/file-history/s/chk_abc.json","bytes":12}',
    },
    {
      name: 'python',
      arguments: { code: 'open("_probe.py","w")' },
      result: 'wrote _probe.py and tmp/scan.py',
    },
    {
      name: 'command',
      arguments: { command: 'Get-ChildItem .tevarn' },
      result: '.computers/main/home/.tevarn/process_snapshots/10d2.json\n_tmp/hello.py',
    },
  ],
});

assert(arts.some((a) => a.name.includes('report') || a.path.includes('report')), 'md link');
assert(arts.some((a) => a.path.includes('hello.md')), 'tool path');
assert(arts.some((a) => a.kind === 'table'), 'table kind');
assert(
  !arts.some((a) => /file-history|process_snapshots|_probe|scan\.py|_tmp/i.test(a.path)),
  'no process/scratch from read/python/command',
);

const sess = collectSessionArtifacts([
  { role: 'assistant', content: '见 smoke_preview/doc.pdf', tool_calls: [] },
  {
    role: 'tool',
    content: 'path=.tevarn/file-history/x.json and _probe.py',
    tool_calls: [{ name: 'file_read', result: '{"path":".tevarn/x.json"}' }],
  },
  { role: 'user', content: 'ignore.png' },
]);
assert(sess.some((a) => a.name.includes('doc.pdf')), 'session pdf');
assert(
  !sess.some((a) => /file-history|_probe|\.tevarn/i.test(a.path)),
  'session bar ignores tool-role dumps',
);

console.log('artifacts.selftest OK', arts.length, arts.map((a) => a.name).join(', '));
