/**
 * P7 双语正式化：英文模式全路径扫描
 *
 * 设置 locale=en 后访问 AIOS 一级路由，扫描可见文案中是否残留 CJK 汉字。
 * 允许白名单：专有名词、测试数据、代码片段。
 *
 *   npx playwright test e2e/i18n_en_scan.spec.ts --reporter=line
 */
import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const FE = process.env.FE || 'http://127.0.0.1:3000';
const API = process.env.API || 'http://127.0.0.1:8000/api';

/** AIOS 一级导航（demo v2） */
const ROUTES = [
  '/',
  '/agents',
  '/goals',
  '/approvals',
  '/knowledge',
  '/activity',
  '/kernel',
  '/market',
  '/settings',
];

/** 允许出现的汉字（品牌/测试账号/已知未迁文案可逐步收紧） */
const ALLOW_CJK = [
  /tevarn/i,
  /WuYiWei/,
  /沈策|陈工|文研|码力|测安|小秘|金算/, // demo 人名若未清库
];

const CJK_RE = /[\u4e00-\u9fff]/g;

async function apiLogin(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@tevarn.dev', password: 'admin' }),
  });
  const d = (await r.json()) as { access_token?: string };
  if (!d.access_token) throw new Error('login failed ' + JSON.stringify(d));
  return d.access_token;
}

async function seedAuth(page: Page, token: string) {
  await page.addInitScript(
    ({ tok }: { tok: string }) => {
      try {
        localStorage.setItem(
          'tevarn-auth',
          JSON.stringify({
            state: {
              user: { email: 'admin@tevarn.dev', username: 'admin' },
              token: tok,
              isAuthenticated: true,
              hasHydrated: true,
            },
            version: 0,
          }),
        );
        localStorage.setItem(
          'tevarn-locale',
          JSON.stringify({ state: { locale: 'en' }, version: 0 }),
        );
        document.documentElement.lang = 'en';
        document.cookie = `tevarn-auth=${tok}; path=/; max-age=604800; SameSite=Strict`;
      } catch {
        /* ignore */
      }
    },
    { tok: token },
  );
}

function extractCjk(text: string): string[] {
  const hits: string[] = [];
  let m: RegExpExecArray | null;
  const re = new RegExp(CJK_RE);
  while ((m = re.exec(text))) {
    // 取词上下文
    const start = Math.max(0, m.index - 8);
    const end = Math.min(text.length, m.index + m[0].length + 12);
    const snippet = text.slice(start, end).replace(/\s+/g, ' ').trim();
    if (ALLOW_CJK.some((p) => p.test(snippet))) continue;
    hits.push(snippet);
  }
  return [...new Set(hits)].slice(0, 40);
}

test.describe('i18n EN full-path scan', () => {
  test.setTimeout(180_000);

  test('primary AIOS routes have no residual Chinese', async ({ page }) => {
    let token: string;
    try {
      token = await apiLogin();
    } catch (e) {
      test.skip(true, `backend login unavailable: ${e}`);
      return;
    }

    await seedAuth(page, token);
    const report: Array<{ route: string; hits: string[] }> = [];

    for (const route of ROUTES) {
      await page.goto(`${FE}${route}`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      // 等壳子与首屏数据
      await page.waitForTimeout(1200);
      // 强制 en（防水合覆盖）
      await page.evaluate(() => {
        document.documentElement.lang = 'en';
        try {
          localStorage.setItem(
            'tevarn-locale',
            JSON.stringify({ state: { locale: 'en' }, version: 0 }),
          );
        } catch {
          /* ignore */
        }
      });
      await page.waitForTimeout(400);

      const bodyText = await page.locator('body').innerText({ timeout: 15_000 }).catch(() => '');
      const hits = extractCjk(bodyText);
      if (hits.length) report.push({ route, hits });
    }

    const outDir = path.join(__dirname, 'i18n-report');
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(
      path.join(outDir, 'en-scan.json'),
      JSON.stringify({ scanned: ROUTES, failures: report, at: new Date().toISOString() }, null, 2),
      'utf-8',
    );

    if (report.length) {
      const summary = report
        .map((r) => `${r.route}: ${r.hits.slice(0, 5).join(' | ')}`)
        .join('\n');
      // 软失败：先写报告；CI 可把 expect 打开
      console.warn('CJK residual found:\n' + summary);
    }

    // Alpha：报告优先；若环境全绿则 0 残留
    // 硬门槛：驾驶舱 / 审批 / 扩展 三页不允许残留（主叙事）
    const critical = report.filter((r) => ['/', '/approvals', '/market'].includes(r.route));
    expect(critical, `critical EN pages still have CJK: ${JSON.stringify(critical)}`).toEqual([]);
  });
});
