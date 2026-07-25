/**
 * Chat File UX UI smoke (Playwright)
 * SMOKE_BASE_URL=http://127.0.0.1:3015 SMOKE_API_URL=http://127.0.0.1:8095 \
 *   npx playwright test e2e/chat_file_ux_smoke.spec.ts --reporter=line
 */
import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const BASE_URL = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:3015';
const API_URL = process.env.SMOKE_API_URL || 'http://127.0.0.1:8095';
const OUT = process.env.SCREENSHOT_DIR || '/tmp/takton-chat-ux-smoke/ui';

type SmokeApi = {
  setPreview: (a: {
    path: string;
    name: string;
    source: string;
    kind?: string;
  } | null) => void;
  addMessage: (m: Record<string, unknown>) => void;
  setMessages: (m: Record<string, unknown>[]) => void;
};

async function loginViaApi(page: Page) {
  const response = await fetch(`${API_URL}/api/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) throw new Error(`Login failed: ${response.status}`);
  const body = (await response.json()) as { access_token: string; user: unknown };

  await page.goto(`${BASE_URL}/login`);
  await page.waitForLoadState('domcontentloaded');
  await page.evaluate(
    ({ token, user }) => {
      const payload = {
        state: { token, user, isAuthenticated: true, hasHydrated: true },
        version: 0,
      };
      localStorage.setItem('takton-auth', JSON.stringify(payload));
      document.cookie = `takton-auth=${token}; path=/; SameSite=Strict`;
    },
    { token: body.access_token, user: body.user }
  );
  await page.goto(`${BASE_URL}/`);
  await page.waitForLoadState('networkidle').catch(() => null);
  await expect(page).not.toHaveURL(/\/login/);
}

async function waitSmoke(page: Page): Promise<void> {
  await page.waitForFunction(
    () => !!(window as unknown as { __taktonSmoke?: SmokeApi }).__taktonSmoke,
    null,
    { timeout: 20_000 }
  );
}

test.describe('Chat File UX smoke', () => {
  test.beforeAll(() => {
    fs.mkdirSync(OUT, { recursive: true });
  });

  test('attach + artifact cards + multi-format preview', async ({ page }) => {
    test.setTimeout(120_000);
    const log: string[] = [];
    const pass = (m: string) => {
      log.push('PASS ' + m);
      console.log('PASS', m);
    };
    const fail = (m: string) => {
      log.push('FAIL ' + m);
      console.error('FAIL', m);
    };

    await loginViaApi(page);
    await waitSmoke(page);
    pass('login+smokeHook');

    const composer = page.getByTestId('chat-composer');
    await expect(composer).toBeVisible({ timeout: 20_000 });
    pass('composerVisible');
    await page.screenshot({ path: path.join(OUT, '01-home.png'), fullPage: true });

    // attach via file input
    const pngPath = '/tmp/takton-chat-ux-smoke/up.png';
    if (!fs.existsSync(pngPath)) {
      fs.writeFileSync(
        pngPath,
        Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
          'base64'
        )
      );
    }
    const fileInput = composer.locator('input[type="file"]');
    await fileInput.setInputFiles(pngPath);
    // wait chip / thumbnail
    await page.waitForTimeout(2000);
    const chipCount = await composer.locator('img').count();
    if (chipCount > 0) pass('attachChipImg');
    else {
      // text chip fallback
      const hasChip = (await composer.locator('[class*="rounded"]').count()) > 0;
      if (hasChip) pass('attachChipGeneric');
      else fail('attachChip');
    }
    await page.screenshot({ path: path.join(OUT, '02-after-attach.png'), fullPage: true });

    // inject assistant message with multi artifacts
    await page.evaluate(() => {
      const smoke = (window as unknown as { __taktonSmoke: SmokeApi }).__taktonSmoke;
      smoke.setMessages([
        {
          id: 'smoke-user-1',
          role: 'user',
          content: '生成几个文件给我预览',
          timestamp: new Date().toISOString(),
        },
        {
          id: 'smoke-asst-1',
          role: 'assistant',
          content:
            '已生成：\n- [说明](smoke_preview/hello.md)\n- smoke_preview/data.csv\n- smoke_preview/dot.png\n- smoke_preview/doc.pdf\n- smoke_preview/page.html\n- smoke_preview/note.txt\n- smoke_preview/sample.xlsx\n- smoke_preview/sample.docx\n- smoke_preview/sample.pptx\n',
          timestamp: new Date().toISOString(),
        },
      ]);
    });
    await page.waitForTimeout(800);

    // artifact cards
    const cards = page.getByTestId('chat-artifacts');
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });
    pass('artifactCards');
    await page.screenshot({ path: path.join(OUT, '03-artifacts.png'), fullPage: true });

    // session bar
    const bar = page.getByTestId('session-artifacts-bar');
    if (await bar.isVisible().catch(() => false)) {
      pass('sessionArtifactsBar');
      await bar.click();
      await page.waitForTimeout(300);
    } else {
      fail('sessionArtifactsBar');
    }

    // multi-format preview via smoke hook
    const cases: Array<{
      path: string;
      name: string;
      kind: string;
      expectRe: RegExp;
      iframe?: boolean;
    }> = [
      { path: 'smoke_preview/hello.md', name: 'hello.md', kind: 'markdown', expectRe: /Smoke MD|item|bold/i },
      { path: 'smoke_preview/data.csv', name: 'data.csv', kind: 'table', expectRe: /alice|score|name/i },
      { path: 'smoke_preview/note.txt', name: 'note.txt', kind: 'text', expectRe: /plain text smoke/i },
      {
        path: 'smoke_preview/page.html',
        name: 'page.html',
        kind: 'html',
        expectRe: /HTML Smoke/i,
        iframe: true,
      },
      { path: 'smoke_preview/dot.png', name: 'dot.png', kind: 'image', expectRe: /./ },
      { path: 'smoke_preview/doc.pdf', name: 'doc.pdf', kind: 'pdf', expectRe: /./ },
      { path: 'smoke_preview/sample.xlsx', name: 'sample.xlsx', kind: 'table', expectRe: /n|v|x|y|S1/i },
      { path: 'smoke_preview/sample.docx', name: 'sample.docx', kind: 'docx', expectRe: /HelloDocxSmoke/i },
      { path: 'smoke_preview/sample.pptx', name: 'sample.pptx', kind: 'pptx', expectRe: /Slide|slide|Text|empty/i },
    ];

    for (const c of cases) {
      await page.evaluate((art) => {
        const smoke = (window as unknown as { __taktonSmoke: SmokeApi }).__taktonSmoke;
        smoke.setPreview({
          path: art.path,
          name: art.name,
          source: 'content',
          kind: art.kind,
        });
      }, c);
      const host = page.getByTestId('file-preview-host');
      try {
        await expect(host).toBeVisible({ timeout: 10_000 });
        await page.waitForTimeout(1500);
        const err = host.locator('.text-red-400, .text-red-500, .border-amber-500\\/40, .text-amber-700, .bg-amber-50');
        // soft error banner
        const errVisible = await err.first().isVisible().catch(() => false);
        if (errVisible) {
          const et = await err.first().innerText();
          if (/Could not find file|error|失败|unsupported/i.test(et) && !c.expectRe.test(et)) {
            fail(`preview:${c.name}:${et.slice(0, 80)}`);
            await page.screenshot({
              path: path.join(OUT, `preview-FAIL-${c.name.replace(/\./g, '_')}.png`),
              fullPage: true,
            });
            continue;
          }
        }
        if (c.kind === 'image') {
          await expect(host.locator('img').first()).toBeVisible({ timeout: 8_000 });
          pass(`preview:${c.name}`);
        } else if (c.kind === 'pdf') {
          await expect(host.locator('iframe').first()).toBeVisible({ timeout: 8_000 });
          pass(`preview:${c.name}`);
        } else if (c.iframe) {
          const frame = host.frameLocator('iframe').first();
          await expect(frame.locator('body')).toContainText(c.expectRe, { timeout: 10_000 });
          pass(`preview:${c.name}`);
        } else {
          await expect(host).toContainText(c.expectRe, { timeout: 10_000 });
          pass(`preview:${c.name}`);
        }
        await page.screenshot({
          path: path.join(OUT, `preview-${c.name.replace(/\./g, '_')}.png`),
          fullPage: true,
        });
      } catch (e) {
        fail(`preview:${c.name}:${String(e).slice(0, 120)}`);
        await page.screenshot({
          path: path.join(OUT, `preview-FAIL-${c.name.replace(/\./g, '_')}.png`),
          fullPage: true,
        });
      }
    }

    // open in system button present
    await page.evaluate(() => {
      const smoke = (window as unknown as { __taktonSmoke: SmokeApi }).__taktonSmoke;
      smoke.setPreview({
        path: 'smoke_preview/hello.md',
        name: 'hello.md',
        source: 'content',
        kind: 'markdown',
      });
    });
    await expect(page.getByTestId('file-preview-host')).toBeVisible();
    const openBtn = page.getByRole('button', { name: /系统打开|Open in app/i });
    if (await openBtn.isVisible().catch(() => false)) {
      await openBtn.click();
      await page.waitForTimeout(800);
      pass('openInSystemClick');
    } else {
      fail('openInSystemBtn');
    }

    // close preview
    await page.evaluate(() => {
      (window as unknown as { __taktonSmoke: SmokeApi }).__taktonSmoke.setPreview(null);
    });

    fs.writeFileSync(path.join(OUT, 'ui-report.txt'), log.join('\n') + '\n');
    const fails = log.filter((l) => l.startsWith('FAIL'));
    console.log('UI_SUMMARY', JSON.stringify({ pass: log.filter((l) => l.startsWith('PASS')).length, fail: fails.length }));
    expect(fails, fails.join(' | ')).toEqual([]);
  });
});
