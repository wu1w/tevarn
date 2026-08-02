/**
 * 浏览器端多格式预览解析（xlsx / docx / pptx / csv …）
 * 仅用于 FilePreviewHost，不进默认 agent schema。
 */

export type SheetTable = { name: string; rows: string[][] };

/** 统一成独立 ArrayBuffer，避免 Uint8Array.buffer 带 byteOffset 时 SheetJS/mammoth 读歪 */
export function toArrayBuffer(input: ArrayBuffer | ArrayBufferView): ArrayBuffer {
  if (input instanceof ArrayBuffer) return input;
  const view = input as ArrayBufferView;
  return view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength) as ArrayBuffer;
}

export function parseCsvText(text: string, maxRows = 120): string[][] {
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0).slice(0, maxRows);
  return lines.map((line) => {
    const cells: string[] = [];
    let cur = '';
    let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        inQ = !inQ;
        continue;
      }
      if (ch === ',' && !inQ) {
        cells.push(cur);
        cur = '';
        continue;
      }
      cur += ch;
    }
    cells.push(cur);
    return cells;
  });
}

export async function loadXlsxTables(
  buf: ArrayBuffer | ArrayBufferView,
  maxRows = 100
): Promise<SheetTable[]> {
  const XLSX = await import('xlsx');
  const ab = toArrayBuffer(buf);
  const wb = XLSX.read(new Uint8Array(ab), { type: 'array' });
  const out: SheetTable[] = [];
  for (const name of wb.SheetNames.slice(0, 8)) {
    const sheet = wb.Sheets[name];
    if (!sheet) continue;
    const aoa = XLSX.utils.sheet_to_json(sheet, {
      header: 1,
      defval: '',
      raw: false,
    }) as unknown[][];
    const rows = aoa.slice(0, maxRows).map((r) =>
      (Array.isArray(r) ? r : []).map((c) => (c == null ? '' : String(c)))
    );
    out.push({ name, rows: rows.length ? rows : [['(empty)']] });
  }
  return out;
}

export async function loadDocxHtml(buf: ArrayBuffer | ArrayBufferView): Promise<string> {
  const ab = toArrayBuffer(buf);
  const u8 = new Uint8Array(ab);

  // 1) mammoth（浏览器应走 browser/unzip 的 arrayBuffer；Node 走 buffer）
  try {
    const mod = await import('mammoth');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mammoth: any = (mod as any).default ?? mod;
    const input: Record<string, unknown> = { arrayBuffer: ab };
    if (typeof Buffer !== 'undefined') {
      input.buffer = Buffer.from(u8);
    }
    // 浏览器优先 arrayBuffer-only，避免 Node 版 unzip 收到无效 buffer polyfill
    if (typeof window !== 'undefined') {
      try {
        const r = await mammoth.convertToHtml({ arrayBuffer: ab });
        if (r?.value != null) return r.value || '<p>(empty)</p>';
      } catch {
        /* fallthrough */
      }
    }
    const result = await mammoth.convertToHtml(input);
    if (result?.value != null) return result.value || '<p>(empty)</p>';
  } catch {
    /* fallthrough to zip fallback */
  }

  // 2) JSZip 抽取 document.xml 文本（无版式，但可预览）
  return loadDocxHtmlViaZip(ab);
}

async function loadDocxHtmlViaZip(ab: ArrayBuffer): Promise<string> {
  const JSZip = (await import('jszip')).default;
  const zip = await JSZip.loadAsync(ab);
  const docXml =
    (await zip.file('word/document.xml')?.async('string')) ||
    (await zip.file('word/document2.xml')?.async('string'));
  if (!docXml) throw new Error('docx: missing word/document.xml');
  const texts: string[] = [];
  const re = /<w:t[^>]*>([\s\S]*?)<\/w:t>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(docXml)) !== null) {
    const t = m[1]
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&')
      .replace(/&quot;/g, '"')
      .trim();
    if (t) texts.push(t);
  }
  // 粗略按段落
  const paras = docXml.split(/<\/w:p>/i).map((chunk) => {
    const parts: string[] = [];
    const r2 = /<w:t[^>]*>([\s\S]*?)<\/w:t>/gi;
    let mm: RegExpExecArray | null;
    while ((mm = r2.exec(chunk)) !== null) {
      const t = mm[1]
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&')
        .trim();
      if (t) parts.push(t);
    }
    return parts.join('');
  }).filter(Boolean);
  const body = (paras.length ? paras : texts).map((p) => `<p>${escapeHtml(p)}</p>`).join('');
  return body || '<p>(empty)</p>';
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function stripXml(s: string): string {
  return s
    .replace(/<a:t[^>]*>/gi, '')
    .replace(/<\/a:t>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
}

export async function loadPptxSlides(
  buf: ArrayBuffer | ArrayBufferView,
  maxSlides = 30
): Promise<string[]> {
  const JSZip = (await import('jszip')).default;
  const zip = await JSZip.loadAsync(toArrayBuffer(buf));
  const names = Object.keys(zip.files)
    .filter((n) => /^ppt\/slides\/slide\d+\.xml$/i.test(n))
    .sort((a, b) => {
      const na = parseInt(a.match(/slide(\d+)/i)?.[1] || '0', 10);
      const nb = parseInt(b.match(/slide(\d+)/i)?.[1] || '0', 10);
      return na - nb;
    })
    .slice(0, maxSlides);

  const slides: string[] = [];
  for (const n of names) {
    const xml = await zip.files[n].async('string');
    // 优先 a:t 文本 run
    const parts: string[] = [];
    const re = /<a:t[^>]*>([\s\S]*?)<\/a:t>/gi;
    let m: RegExpExecArray | null;
    while ((m = re.exec(xml)) !== null) {
      const t = m[1].replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim();
      if (t) parts.push(t);
    }
    const body = parts.length ? parts.join(' ') : stripXml(xml);
    slides.push(body || '(empty slide)');
  }
  if (slides.length === 0) {
    slides.push('(no slides found — file may be ppt legacy or empty)');
  }
  return slides;
}

export function sanitizeHtmlForPreview(html: string): string {
  // 审计 P0-F3：DOMPurify 主路径；失败时多趟正则 + 实体解码后再剥 javascript:
  const purifyOpts = {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'form', 'base', 'object', 'embed', 'iframe', 'script'],
    FORBID_ATTR: ['srcdoc', 'formaction', 'style'],
  } as const;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const DOMPurify = require('dompurify') as typeof import('dompurify');
    if (typeof window !== 'undefined' && DOMPurify?.sanitize) {
      return DOMPurify.sanitize(html || '', purifyOpts);
    }
  } catch {
    /* fall through */
  }
  // SSR / 无 DOM 回落：先解一层 HTML 实体再多趟剥离（防 javas&#x63;ript:）
  let h = html || '';
  const decodeEntities = (s: string) =>
    s
      .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => {
        try {
          return String.fromCodePoint(parseInt(hex, 16));
        } catch {
          return '';
        }
      })
      .replace(/&#(\d+);/g, (_, n) => {
        try {
          return String.fromCodePoint(parseInt(n, 10));
        } catch {
          return '';
        }
      })
      .replace(/&colon;/gi, ':')
      .replace(/&Tab;/gi, '')
      .replace(/&NewLine;/gi, '');
  for (let i = 0; i < 3; i++) {
    h = decodeEntities(h);
    h = h.replace(/<script[\s\S]*?<\/script>/gi, '');
    h = h.replace(/<iframe[\s\S]*?>[\s\S]*?<\/iframe>/gi, '');
    h = h.replace(/<iframe[\s\S]*?>/gi, '');
    h = h.replace(/\son\w+\s*=\s*(['"]).*?\1/gi, '');
    h = h.replace(/\son\w+\s*=\s*[^\s>]+/gi, '');
    h = h.replace(/javascript\s*:/gi, '');
    h = h.replace(/vbscript\s*:/gi, '');
    h = h.replace(/data\s*:\s*text\s*\/\s*html/gi, '');
  }
  return h;
}
