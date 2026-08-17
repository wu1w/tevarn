/**
 * Chat File UX logic smoke — run: npx tsx scripts/smoke_chat_file_ux_logic.ts
 */
import * as fs from 'fs';
import {
  extractArtifacts,
  collectSessionArtifacts,
  artifactPreviewable,
} from '../lib/artifacts';
import {
  parseCsvText,
  sanitizeHtmlForPreview,
  loadXlsxTables,
  loadDocxHtml,
  loadPptxSlides,
} from '../lib/filePreviewLoaders';
import JSZip from 'jszip';

async function main() {
  const report: string[] = [];
  let pass = 0;
  let fail = 0;
  const ok = (m: string) => {
    pass++;
    report.push('PASS ' + m);
    console.log('PASS', m);
  };
  const ko = (m: string, e?: unknown) => {
    fail++;
    report.push('FAIL ' + m + ' ' + String(e));
    console.error('FAIL', m, e);
  };

  try {
    const arts = extractArtifacts({
      content: '写了 [表](workspace/smoke_preview/data.csv) 和 report.xlsx 还有 hello.md',
      tool_calls: [
        {
          name: 'file_write',
          arguments: { path: 'smoke_preview/hello.md' },
          result: '{"ok":true,"path":"smoke_preview/hello.md"}',
        },
      ],
    });
    if (!arts.some((a) => a.path.includes('data.csv'))) throw new Error('missing csv');
    if (!arts.some((a) => a.kind === 'markdown' || a.name.includes('hello'))) {
      throw new Error('missing md');
    }
    if (!artifactPreviewable('docx') || !artifactPreviewable('pptx') || !artifactPreviewable('html')) {
      throw new Error('previewable');
    }
    ok('extractArtifacts kinds');
    const sess = collectSessionArtifacts([
      { role: 'assistant', content: '见 smoke_preview/doc.pdf', tool_calls: [] },
      { role: 'user', content: 'ignore.png' },
    ]);
    if (!sess.some((a) => a.name.includes('doc.pdf'))) throw new Error(JSON.stringify(sess));
    const junk = extractArtifacts({
      content: '读了 .tevarn/file-history/a.json 和 _probe.py',
      tool_calls: [
        {
          name: 'file_read',
          arguments: { path: '.computers/main/home/.tevarn/process_snapshots/x.json' },
          result: '{"path":".computers/main/home/.tevarn/process_snapshots/x.json"}',
        },
      ],
    });
    if (junk.some((a) => /tevarn|_probe|process_snapshots/i.test(a.path + a.name))) {
      throw new Error('leaked process files ' + JSON.stringify(junk));
    }
    ok('collectSessionArtifacts');
    ok('reject process/scratch files');
  } catch (e) {
    ko('artifacts', e);
  }

  try {
    const rows = parseCsvText('a,b\n1,"2,3"\n');
    if (rows[1][1] !== '2,3') throw new Error(JSON.stringify(rows));
    const h = sanitizeHtmlForPreview('<p onclick=alert(1)>x</p><script>bad</script>');
    if (h.includes('script') || /onclick/i.test(h)) throw new Error(h);
    ok('csv+sanitizeHtml');
  } catch (e) {
    ko('csv/html', e);
  }

  try {
    const XLSX = await import('xlsx');
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet([
      ['n', 'v'],
      ['x', 1],
      ['y', 2],
    ]);
    XLSX.utils.book_append_sheet(wb, ws, 'S1');
    const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as Uint8Array;
    const tables = await loadXlsxTables(buf);
    if (!tables[0] || tables[0].rows[0][0] !== 'n') throw new Error(JSON.stringify(tables));
    ok('loadXlsxTables');
    fs.mkdirSync('/tmp/tevarn-chat-ux-smoke', { recursive: true });
    fs.writeFileSync('/tmp/tevarn-chat-ux-smoke/sample.xlsx', Buffer.from(buf));
  } catch (e) {
    ko('xlsx', e);
  }

  try {
    const zip = new JSZip();
    zip.file(
      '[Content_Types].xml',
      `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`
    );
    zip
      .folder('_rels')
      ?.file(
        '.rels',
        `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`
      );
    zip
      .folder('word')
      ?.file(
        'document.xml',
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>HelloDocxSmoke</w:t></w:r></w:p></w:body></w:document>`
      );
    const docxBuf = await zip.generateAsync({ type: 'arraybuffer' });
    const html = await loadDocxHtml(docxBuf);
    if (!html.includes('HelloDocxSmoke')) throw new Error(html);
    ok('loadDocxHtml');
    fs.writeFileSync('/tmp/tevarn-chat-ux-smoke/sample.docx', Buffer.from(docxBuf));
  } catch (e) {
    ko('docx', e);
  }

  try {
    const zip = new JSZip();
    zip.file(
      '[Content_Types].xml',
      `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>`
    );
    zip
      .folder('ppt')
      ?.folder('slides')
      ?.file(
        'slide1.xml',
        `<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><a:t>SlideOneText</a:t></p:sld>`
      );
    const pptxBuf = await zip.generateAsync({ type: 'arraybuffer' });
    const slides = await loadPptxSlides(pptxBuf);
    if (!slides.some((s) => s.includes('SlideOneText'))) throw new Error(JSON.stringify(slides));
    ok('loadPptxSlides');
    fs.writeFileSync('/tmp/tevarn-chat-ux-smoke/sample.pptx', Buffer.from(pptxBuf));
  } catch (e) {
    ko('pptx', e);
  }

  console.log('LOGIC_SUMMARY pass=' + pass + ' fail=' + fail);
  fs.writeFileSync(
    '/tmp/tevarn-chat-ux-smoke/logic.txt',
    report.join('\n') + '\nSUMMARY pass=' + pass + ' fail=' + fail + '\n'
  );
  if (fail) process.exit(1);
}

main();
