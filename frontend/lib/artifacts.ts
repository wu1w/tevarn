/**
 * 从助手消息抽取可投递的工作区产物（预览 / 下载）。
 *
 * 对齐 Cursor / Codex：只展示用户能打开的产出，不把过程文件、
 * 只读工具扫到的路径、临时探针脚本放进「本轮文件」。
 */
export interface ChatArtifact {
  /** 相对 workspace 或可下载 path */
  path: string;
  name: string;
  source: 'tool' | 'content' | 'link';
  /** 可选 mime 提示 */
  kind?: 'image' | 'table' | 'text' | 'pdf' | 'html' | 'markdown' | 'docx' | 'pptx' | 'other';
}

/** 真正写出用户文件的工具（不含 file_read / grep / command 扫盘） */
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

/** 正文里顺手提到才收录的「投递件」扩展（表格/文档/图），不含源码与 json 日志 */
const DELIVERABLE_EXTS = new Set([
  'xlsx',
  'xls',
  'csv',
  'tsv',
  'pptx',
  'ppt',
  'docx',
  'doc',
  'pdf',
  'png',
  'jpg',
  'jpeg',
  'webp',
  'gif',
  'svg',
  'html',
  'htm',
  'zip',
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
  zip: 'other',
};

const PATH_KEYS = [
  'path',
  'file',
  'filepath',
  'output',
  'filename',
  'saved_to',
  'dest',
  'destination',
] as const;

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

function slash(p: string): string {
  return (p || '').replace(/\\/g, '/');
}

/** 运行时 / 会话过程目录：下载沙箱即使能读也不该出现在对话文件里 */
export function isInternalRuntimePath(raw: string): boolean {
  const n = slash(raw);
  const lower = n.toLowerCase();
  if (/(^|\/)\.tevarn(\/|$)/i.test(n)) return true;
  if (/(^|\/)\.computers(\/|$)/i.test(n)) return true;
  if (/(^|\/)\.git(\/|$)/i.test(n)) return true;
  if (/(^|\/)(node_modules|__pycache__|\.pytest_cache|\.next|\.venv|venv)(\/|$)/i.test(n)) {
    return true;
  }
  if (
    /(^|\/)(file-history|process_snapshots|tool_results|control_inbox|run_events)(\/|$)/i.test(
      lower,
    )
  ) {
    return true;
  }
  if (
    /(^|\/)(rpc\.secret|secrets\.json|usage_ledger\.json|intent_telemetry\.jsonl|kernel_events(?:\.anchor)?\.jsonl?)$/i.test(
      lower,
    )
  ) {
    return true;
  }
  if (/\.(lock|secret|pyc|pyo)$/i.test(n)) return true;
  if (/(^|\/)media\/[a-f0-9]{6,}\.bin$/i.test(lower)) return true;
  if (/(^|\/)\.env(?:\..+)?$/i.test(n)) return true;
  return false;
}

/** 探针 / 临时脚本：agent 自用，不是给用户下载的产出 */
export function isScratchOrProcessFile(raw: string): boolean {
  const n = slash(raw);
  const base = basename(n);
  const lower = base.toLowerCase();
  if (/(^|\/)(_tmp|_snap|_diag)(\/|$)/i.test(n)) return true;
  if (
    /(^|\/)(tmp|temp|scratch)\//i.test(n) &&
    /\.(py|ps1|bat|cmd|sh|js|ts)$/i.test(lower)
  ) {
    return true;
  }
  if (/^dump\.(ps1|py|js|sh|bat|cmd)$/i.test(lower)) return true;
  if (
    /^_/.test(base) &&
    /\.(py|js|ts|tsx|ps1|bat|cmd|sh|txt|json|log|md)$/i.test(lower)
  ) {
    return true;
  }
  if (
    /\.(py|ps1|bat|cmd|sh)$/i.test(lower) &&
    /(probe|scratch|tmp|diag|dump|scan|review|filelist|hello_tmp)/i.test(lower)
  ) {
    return true;
  }
  return false;
}

function isWriteToolName(name: string): boolean {
  const n = (name || '').toLowerCase();
  if (WRITE_TOOLS.has(n)) return true;
  if (/^(file_|desktop_)?write_file$/.test(n)) return true;
  return false;
}

/**
 * 用户侧是否该出现在预览/下载列表。
 * source=tool：写工具产出的工作区文件（含源码）。
 * source=link/content：仅投递件或明确链接，且不是过程/临时文件。
 */
export function isUserFacingArtifactPath(
  raw: string,
  source: ChatArtifact['source'] = 'content',
): boolean {
  const p = slash(raw).replace(/^\/+/, '');
  if (!p || isInternalRuntimePath(p) || isScratchOrProcessFile(p)) return false;
  if (source === 'tool') return true;
  const ext = extOf(p);
  if (source === 'link') {
    // 链接是模型显式投递；仍挡过程文件，源码链接可以预览
    return !!ext;
  }
  return DELIVERABLE_EXTS.has(ext);
}

function mapAbsToWorkspaceRel(p: string): string | null {
  const uni = slash(p);
  const lower = uni.toLowerCase();
  const wsIdx = lower.lastIndexOf('/workspace/');
  if (wsIdx >= 0) return uni.slice(wsIdx + '/workspace/'.length);
  const computers = lower.indexOf('/.computers/');
  if (computers >= 0) return uni.slice(computers + 1); // keep .computers/… for internal filter
  return null;
}

/** 规范化 path：去 sandbox:、file://、包裹引号。绝对路径无法映射到工作区则丢弃。 */
export function normalizeArtifactPath(raw: string): string | null {
  let p = (raw || '').trim();
  if (!p) return null;
  p = p.replace(/^['"`]+|['"`]+$/g, '');
  p = p.replace(/^sandbox:\/*/i, '');
  p = p.replace(/^file:\/\//i, '');
  p = p.split('?')[0].split('#')[0];
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
  // Windows / UNC：不能降成 basename（下载必 404）。能映射到 workspace 才收。
  const isWinAbs = /^[A-Za-z]:[\\/]/.test(p) || p.startsWith('\\\\');
  if (isWinAbs) {
    const mapped = mapAbsToWorkspaceRel(p);
    if (!mapped) return null;
    p = mapped;
  } else if (p.startsWith('/')) {
    const mapped = mapAbsToWorkspaceRel(p);
    p = mapped || p.replace(/^\/+/, '');
  }
  p = p.replace(/\\/g, '/');
  p = p.replace(/^\/+/, '');
  p = p.replace(/^\.\/+/, '');
  if (p.endsWith('/')) return null;
  if (!/\.[A-Za-z0-9]{1,10}$/.test(p)) return null;
  if (p.length > 512) return null;
  if (/[\n\r\t]/.test(p)) return null;
  if (isInternalRuntimePath(p) || isScratchOrProcessFile(p)) return null;
  return p;
}

function pushUnique(map: Map<string, ChatArtifact>, art: ChatArtifact) {
  if (!isUserFacingArtifactPath(art.path, art.source)) return;
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
  if (t.startsWith('{') || t.startsWith('[')) {
    try {
      const obj = JSON.parse(t) as unknown;
      walkJsonForPaths(obj, source, map);
      return;
    } catch {
      /* fallthrough */
    }
  }
  const pathKey =
    /"(?:path|file|filepath|output|filename|saved_to|dest|destination)"\s*:\s*"([^"]+)"/gi;
  let m: RegExpExecArray | null;
  while ((m = pathKey.exec(t)) !== null) {
    const p = normalizeArtifactPath(m[1]);
    if (p) pushUnique(map, { path: p, name: basename(p), source, kind: kindOf(p) });
  }
}

function walkJsonForPaths(
  obj: unknown,
  source: ChatArtifact['source'],
  map: Map<string, ChatArtifact>,
  depth = 0,
) {
  if (depth > 6 || obj == null) return;
  if (Array.isArray(obj)) {
    for (const x of obj) walkJsonForPaths(x, source, map, depth + 1);
    return;
  }
  if (typeof obj !== 'object') return;
  const rec = obj as Record<string, unknown>;
  for (const k of PATH_KEYS) {
    const p = tryPathField(rec[k]);
    if (p) pushUnique(map, { path: p, name: basename(p), source, kind: kindOf(p) });
  }
  const url = tryPathField(rec.url);
  if (url && url.includes('uploads/')) {
    pushUnique(map, { path: url, name: basename(url), source, kind: kindOf(url) });
  }
  for (const v of Object.values(rec)) {
    if (v && typeof v === 'object') walkJsonForPaths(v, source, map, depth + 1);
  }
}

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

/** 正文里的投递件路径（表格/文档/图），不扫源码与过程文件 */
function extractBarePaths(content: string, map: Map<string, ChatArtifact>) {
  const re =
    /(?:^|[\s"'`(]|=)((?:[\w.-]+\/)+[\w.-]+\.[A-Za-z0-9]{1,10}|(?:workspace|uploads)\/[^\s"'`)\]]+\.[A-Za-z0-9]{1,10})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    const p = normalizeArtifactPath(m[1]);
    if (p) pushUnique(map, { path: p, name: basename(p), source: 'content', kind: kindOf(p) });
  }
  const extAlt = [...DELIVERABLE_EXTS].join('|');
  const bare = new RegExp(
    `(?:^|[\\s"'\`])((?:[\\w\\u4e00-\\u9fff.-]+)\\.(?:${extAlt}))(?=[\\s"'\`.,;:!?)]|$)`,
    'gi',
  );
  while ((m = bare.exec(content)) !== null) {
    const p = normalizeArtifactPath(m[1]);
    if (p) pushUnique(map, { path: p, name: basename(p), source: 'content', kind: kindOf(p) });
  }
}

function pathsFromWriteArgs(argObj: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const k of PATH_KEYS) {
    const p = tryPathField(argObj[k]);
    if (p) out.push(p);
  }
  const patch =
    (typeof argObj.patch === 'string' && argObj.patch) ||
    (typeof argObj.diff === 'string' && argObj.diff) ||
    '';
  const um = patch.match(/^\s*\*\*\*\s*(?:Update|Add|Delete) File:\s*(.+)$/m);
  if (um) {
    const p = normalizeArtifactPath(um[1].trim());
    if (p) out.push(p);
  }
  return out;
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
  }

  for (const tc of msg.tool_calls || []) {
    const name = (tc.name || '').toLowerCase();
    if (!isWriteToolName(name)) continue;
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
    for (const p of pathsFromWriteArgs(argObj)) {
      pushUnique(map, { path: p, name: basename(p), source: 'tool', kind: kindOf(p) });
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

/** 从多条助手消息聚合本轮产出（不含 tool 角色的过程转储） */
export function collectSessionArtifacts(
  messages: Array<{
    role?: string;
    content?: string | null;
    tool_calls?: ExtractArtifactsInput['tool_calls'];
  }>,
): ChatArtifact[] {
  const map = new Map<string, ChatArtifact>();
  for (const m of messages || []) {
    if (m.role !== 'assistant') continue;
    for (const a of extractArtifacts({ content: m.content, tool_calls: m.tool_calls })) {
      const key = a.path.replace(/\\/g, '/').toLowerCase();
      if (!map.has(key)) map.set(key, a);
    }
  }
  return Array.from(map.values()).slice(0, 48);
}
