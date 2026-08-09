/**
 * 从助手消息正文 / tool_calls 抽取可投递的工作区文件产物。
 * 不依赖模型自觉写 markdown 链接。
 */
export interface ChatArtifact {
  /** 相对 workspace 或可下载 path */
  path: string;
  name: string;
  source: 'tool' | 'content' | 'link';
  /** 可选 mime 提示 */
  kind?: 'image' | 'table' | 'text' | 'pdf' | 'html' | 'markdown' | 'docx' | 'pptx' | 'other';
}

const WRITE_TOOLS = new Set([
  'file_write',
  'edit',
  'apply_patch',
  'desktop_write_file',
  'doc_write',
  'generate_ppt',
  'generate_report',
  'image_generate',
]);

const EXT_KIND: Record<string, ChatArtifact['kind']> = {
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  webp: 'image',
  bmp: 'image',
  svg: 'image',
  csv: 'table',
  tsv: 'table',
  xls: 'table',
  xlsx: 'table',
  md: 'markdown',
  markdown: 'markdown',
  txt: 'text',
  log: 'text',
  json: 'text',
  yaml: 'text',
  yml: 'text',
  py: 'text',
  ts: 'text',
  tsx: 'text',
  js: 'text',
  css: 'text',
  pdf: 'pdf',
  html: 'html',
  htm: 'html',
  docx: 'docx',
  pptx: 'pptx',
  ppt: 'pptx',
  doc: 'docx',
};

function basename(p: string): string {
  const n = p.replace(/\\/g, '/').split('/').filter(Boolean).pop();
  return n || p;
}

function extOf(p: string): string {
  const m = basename(p).match(/\.([A-Za-z0-9]{1,10})$/);
  return m ? m[1].toLowerCase() : '';
}

function kindOf(p: string): ChatArtifact['kind'] {
  return EXT_KIND[extOf(p)] || 'other';
}

/** 规范化 path：去 sandbox:、file://、包裹引号 */
export function normalizeArtifactPath(raw: string): string | null {
  let p = (raw || '').trim();
  if (!p) return null;
  p = p.replace(/^['"`]+|['"`]+$/g, '');
  p = p.replace(/^sandbox:\/*/i, '');
  p = p.replace(/^file:\/\//i, '');
  // 去掉 query/hash
  p = p.split('?')[0].split('#')[0];
  // http 外链不当作 workspace artifact（上传 /uploads 另议）
  if (/^https?:\/\//i.test(p)) {
    try {
      const u = new URL(p);
      if (u.pathname.includes('/uploads/')) {
        return u.pathname.replace(/^\/+/, '');
      }
    } catch {
      /* ignore */
    }
    return null;
  }
  p = p.replace(/^\/+/, '');
  p = p.replace(/^\.\/+/, '');
  // 目录不要
  if (p.endsWith('/')) return null;
  if (!/\.[A-Za-z0-9]{1,10}$/.test(p)) return null;
  // 拒绝明显非文件噪音
  if (p.length > 512) return null;
  if (/[\n\r\t]/.test(p)) return null;
  return p;
}

function pushUnique(map: Map<string, ChatArtifact>, art: ChatArtifact) {
  const key = art.path.replace(/\\/g, '/').toLowerCase();
  if (!map.has(key)) map.set(key, art);
}

function tryPathField(v: unknown): string | null {
  if (typeof v === 'string') return normalizeArtifactPath(v);
  return null;
}

function extractFromJsonish(text: string, source: ChatArtifact['source'], map: Map<string, ChatArtifact>) {
  const t = (text || '').trim();
  if (!t) return;
  // 直接是 JSON
  if (t.startsWith('{') || t.startsWith('[')) {
    try {
      const obj = JSON.parse(t) as unknown;
      walkJsonForPaths(obj, source, map);
      return;
    } catch {
      /* fallthrough */
    }
  }
  // "path": "..."
  const pathKey = /"(?:path|file|filepath|output|filename|saved_to|dest|destination)"\s*:\s*"([^"]+)"/gi;
  let m: RegExpExecArray | null;
  while ((m = pathKey.exec(t)) !== null) {
    const p = normalizeArtifactPath(m[1]);
    if (p) pushUnique(map, { path: p, name: basename(p), source, kind: kindOf(p) });
  }
}

function walkJsonForPaths(obj: unknown, source: ChatArtifact['source'], map: Map<string, ChatArtifact>, depth = 0) {
  if (depth > 6 || obj == null) return;
  if (typeof obj === 'string') {
    const p = normalizeArtifactPath(obj);
    if (p) pushUnique(map, { path: p, name: basename(p), source, kind: kindOf(p) });
    return;
  }
  if (Array.isArray(obj)) {
    for (const x of obj) walkJsonForPaths(x, source, map, depth + 1);
    return;
  }
  if (typeof obj === 'object') {
    const rec = obj as Record<string, unknown>;
    for (const k of ['path', 'file', 'filepath', 'output', 'filename', 'saved_to', 'dest', 'destination', 'url']) {
      const p = tryPathField(rec[k]);
      if (p) pushUnique(map, { path: p, name: basename(p), source, kind: kindOf(p) });
    }
    for (const v of Object.values(rec)) {
      if (v && typeof v === 'object') walkJsonForPaths(v, source, map, depth + 1);
    }
  }
}

/** markdown 链接 [text](path) */
function extractMdLinks(content: string, map: Map<string, ChatArtifact>) {
  const re = /\[([^\]]*)\]\(([^)]+)\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    const p = normalizeArtifactPath(m[2]);
    if (p) {
      pushUnique(map, {
        path: p,
        name: (m[1] || basename(p)).trim() || basename(p),
        source: 'link',
        kind: kindOf(p),
      });
    }
  }
}

/** 正文里像路径的 token */
function extractBarePaths(content: string, map: Map<string, ChatArtifact>) {
  // workspace/foo/bar.xlsx 或 ./out/a.csv 或 path with chinese
  const re =
    /(?:^|[\s"'`(]|=)((?:[\w.-]+\/)+[\w.-]+\.[A-Za-z0-9]{1,10}|(?:workspace|uploads|\.tevarn)\/[^\s"'`)\]]+\.[A-Za-z0-9]{1,10}|[A-Za-z]:\\[^\s"'`]+\.[A-Za-z0-9]{1,10})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    let raw = m[1];
    // Windows 绝对路径：仅取文件名走 sandbox 可能 404，仍展示卡，下载可能失败
    if (/^[A-Za-z]:\\/.test(raw)) {
      raw = basename(raw.replace(/\\/g, '/'));
    }
    const p = normalizeArtifactPath(raw);
    if (p) pushUnique(map, { path: p, name: basename(p), source: 'content', kind: kindOf(p) });
  }
  // 纯文件名带常见生成扩展
  const bare =
    /(?:^|[\s"'`])((?:[\w\u4e00-\u9fff.-]+)\.(?:xlsx|xls|csv|pptx|ppt|docx|doc|pdf|png|jpg|jpeg|webp|gif|md|txt|json|html|htm))(?=[\s"'`.,;:!?)]|$)/gi;
  while ((m = bare.exec(content)) !== null) {
    const p = normalizeArtifactPath(m[1]);
    if (p) pushUnique(map, { path: p, name: basename(p), source: 'content', kind: kindOf(p) });
  }
}

export interface ExtractArtifactsInput {
  content?: string | null;
  tool_calls?: Array<{
    name?: string;
    arguments?: Record<string, unknown> | string | null;
    result?: string | null;
  }> | null;
}

export function extractArtifacts(msg: ExtractArtifactsInput): ChatArtifact[] {
  const map = new Map<string, ChatArtifact>();
  const content = msg.content || '';

  if (content) {
    extractMdLinks(content, map);
    extractBarePaths(content, map);
    extractFromJsonish(content, 'content', map);
  }

  for (const tc of msg.tool_calls || []) {
    const name = (tc.name || '').toLowerCase();
    const args = tc.arguments;
    let argObj: Record<string, unknown> = {};
    if (typeof args === 'string') {
      try {
        argObj = JSON.parse(args) as Record<string, unknown>;
      } catch {
        argObj = {};
      }
    } else if (args && typeof args === 'object') {
      argObj = args as Record<string, unknown>;
    }

    if (WRITE_TOOLS.has(name) || name.includes('write') || name.includes('generate')) {
      for (const k of ['path', 'file', 'filepath', 'output', 'filename', 'dest']) {
        const p = tryPathField(argObj[k]);
        if (p) pushUnique(map, { path: p, name: basename(p), source: 'tool', kind: kindOf(p) });
      }
    }
    if (tc.result) extractFromJsonish(String(tc.result), 'tool', map);
  }

  return Array.from(map.values()).slice(0, 24);
}

export function artifactPreviewable(kind: ChatArtifact['kind'] | undefined): boolean {
  return (
    kind === 'image' ||
    kind === 'text' ||
    kind === 'table' ||
    kind === 'pdf' ||
    kind === 'html' ||
    kind === 'markdown' ||
    kind === 'docx' ||
    kind === 'pptx'
  );
}

/** 从多条消息聚合会话级产物 */
export function collectSessionArtifacts(
  messages: Array<{
    role?: string;
    content?: string | null;
    tool_calls?: ExtractArtifactsInput['tool_calls'];
  }>
): ChatArtifact[] {
  const map = new Map<string, ChatArtifact>();
  for (const m of messages || []) {
    if (m.role !== 'assistant' && m.role !== 'tool') continue;
    for (const a of extractArtifacts({ content: m.content, tool_calls: m.tool_calls })) {
      const key = a.path.replace(/\\/g, '/').toLowerCase();
      if (!map.has(key)) map.set(key, a);
    }
  }
  return Array.from(map.values()).slice(0, 48);
}
