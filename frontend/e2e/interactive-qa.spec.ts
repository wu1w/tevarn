/**
 * Faster interactive QA: per-page probes + limited safe clicks.
 */
import { test, expect, type Page, type BrowserContext } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const BASE = process.env.TAKTON_BASE_URL || 'http://127.0.0.1:3000';
const API = process.env.TAKTON_API_URL || 'http://127.0.0.1:8000/api';
const OUT = path.resolve(__dirname, 'screenshots/interactive-qa');

type Bug = {
  page: string;
  severity: 'P0' | 'P1' | 'P2' | 'info';
  kind: string;
  detail: string;
  url?: string;
};

const bugs: Bug[] = [];
const pageLog: Array<Record<string, unknown>> = [];

function bug(page: string, severity: Bug['severity'], kind: string, detail: string, url?: string) {
  bugs.push({ page, severity, kind, detail, url });
}

async function authBootstrap(context: BrowserContext) {
  const res = await fetch(`${API}/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!res.ok) throw new Error(`auto-login ${res.status}`);
  const data = (await res.json()) as {
    access_token: string;
    expires_in?: number;
    user: unknown;
  };
  const token = data.access_token;
  const expires = data.expires_in || 604800;
  await context.addCookies([
    {
      name: 'takton-auth',
      value: token,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: false,
      secure: false,
      sameSite: 'Lax',
    },
  ]);
  await context.addInitScript(
    (payload) => {
      localStorage.setItem(
        'takton-auth',
        JSON.stringify({
          state: {
            user: payload.user,
            token: payload.token,
            isAuthenticated: true,
            hasHydrated: true,
          },
          version: 0,
        })
      );
      document.cookie = `takton-auth=${payload.token}; path=/; max-age=${payload.expires}; SameSite=Lax`;
    },
    { user: data.user, token, expires }
  );
}

async function open(page: Page, route: string) {
  const resp = await page.goto(`${BASE}${route}`, {
    waitUntil: 'domcontentloaded',
    timeout: 20000,
  });
  await page.waitForTimeout(800);
  const url = page.url();
  if (url.includes('/login')) {
    bug(route, 'P0', 'auth-redirect', `打开 ${route} 跳登录`, url);
    return { ok: false as const, status: resp?.status() ?? 0, url };
  }
  return { ok: true as const, status: resp?.status() ?? 0, url };
}

async function safeClick(page: Page, locator: ReturnType<Page['locator']>, name: string, route: string) {
  try {
    if ((await locator.count()) === 0) return false;
    if (!(await locator.first().isVisible().catch(() => false))) return false;
    await locator.first().click({ timeout: 2500 });
    await page.waitForTimeout(350);
    if (page.url().includes('/login') && route !== '/login') {
      bug(route, 'P0', 'click-to-login', `点击「${name}」后掉登录`, page.url());
      return false;
    }
    // cancel dialogs
    const cancel = page.getByRole('button', { name: /^取消$/ });
    if (await cancel.isVisible().catch(() => false)) {
      await cancel.click().catch(() => {});
    }
    await page.keyboard.press('Escape').catch(() => {});
    return true;
  } catch (e) {
    bug(route, 'P2', 'click-fail', `${name}: ${e instanceof Error ? e.message.slice(0, 120) : e}`);
    return false;
  }
}

const NAV = [
  ['/tasks', '任务'],
  ['/devices', '设备'],
  ['/workflows', '工作流'],
  ['/config', '心智配置'],
  ['/tools', '工具'],
  ['/mcp', 'MCP'],
  ['/profiles', '子代理'],
  ['/context', '上下文'],
  ['/cron', '定时任务'],
  ['/knowledge', '知识库'],
  ['/wiki', 'Wiki'],
  ['/channels', '消息通道'],
  ['/settings', '设置'],
] as const;

const PAGES = [
  '/',
  '/profiles',
  '/workflows',
  '/tasks',
  '/context',
  '/knowledge',
  '/wiki',
  '/cron',
  '/tools',
  '/skills',
  '/mcp',
  '/settings',
  '/channels',
  '/devices',
  '/config',
] as const;

test.describe('interactive QA fast', () => {
  test.setTimeout(180_000);

  test('probe all pages + nav + key controls', async ({ browser }) => {
    fs.mkdirSync(OUT, { recursive: true });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: 'dark',
    });
    await authBootstrap(context);
    const page = await context.newPage();

    page.on('pageerror', (e) => bug(page.url(), 'P0', 'pageerror', e.message));
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const t = msg.text();
      if (/favicon|net::ERR|Failed to load resource/i.test(t)) return;
      bug(page.url(), 'P1', 'console-error', t.slice(0, 240));
    });
    page.on('response', (res) => {
      if (!res.url().includes('/api/')) return;
      if (res.status() >= 500) bug(page.url(), 'P0', 'api-5xx', `${res.status()} ${res.url()}`);
    });

    // API health
    try {
      const h = await fetch(`${API}/health`);
      if (!h.ok) bug('api', 'P0', 'health', String(h.status));
    } catch (e) {
      bug('api', 'P0', 'health', String(e));
    }

    // --- Sidebar navigation ---
    await open(page, '/');
    for (const [href, label] of NAV) {
      const link = page.locator(`nav a[href="${href}"]`).first();
      if ((await link.count()) === 0) {
        bug('sidebar', 'P1', 'missing-nav', `缺少 ${label} (${href})`);
        continue;
      }
      await link.click({ timeout: 5000 });
      await page.waitForTimeout(700);
      const u = page.url();
      if (u.includes('/login')) {
        bug('sidebar', 'P0', 'nav-to-login', `${label} -> login`, u);
        // re-open home with cookie still present
        await open(page, '/');
        continue;
      }
      if (!u.includes(href)) {
        bug('sidebar', 'P1', 'nav-wrong', `${label}: 期望 ${href} 实际 ${u}`, u);
      } else {
        pageLog.push({ kind: 'nav-ok', href, label });
      }
    }

    // --- Per page probes ---
    for (const route of PAGES) {
      const r = await open(page, route);
      const entry: Record<string, unknown> = { route, ...r, actions: [] as string[] };
      const actions = entry.actions as string[];
      if (!r.ok) {
        pageLog.push(entry);
        continue;
      }
      if (r.status >= 400) bug(route, 'P0', 'http', `status ${r.status}`);

      // structural: sidebar + main
      const hasNav = (await page.locator('nav a[href]').count()) > 0;
      if (!hasNav) bug(route, 'P1', 'no-sidebar-nav', '页面无侧栏导航链接');

      // targeted actions
      if (route === '/') {
        if (await safeClick(page, page.getByRole('button', { name: /新对话/ }), '新对话', route))
          actions.push('new-chat');
        if (await safeClick(page, page.locator('button[title*="主题"]'), '主题切换', route))
          actions.push('theme');
        // expand history once
        if (await safeClick(page, page.getByText('历史会话', { exact: false }), '历史会话折叠头', route))
          actions.push('sessions-toggle');
      }

      if (route === '/profiles') {
        if (await safeClick(page, page.getByRole('button', { name: /新建/ }), '新建子代理', route))
          actions.push('create-modal');
        await page.keyboard.press('Escape');
      }

      if (route === '/workflows') {
        const ai = page.getByRole('button', { name: /AI 生成|生成并打开/ });
        actions.push((await ai.count()) ? 'ai-bar-ok' : 'ai-bar-missing');
        if ((await ai.count()) === 0) bug(route, 'P1', 'missing-ui', '缺少 AI 生成工作流按钮');
        if (await safeClick(page, page.getByRole('button', { name: /新建工作流|新建/ }), '新建工作流', route))
          actions.push('create');
        await page.keyboard.press('Escape');
      }

      if (route === '/context') {
        if (await safeClick(page, page.getByRole('button', { name: /^刷新$/ }), '刷新分层', route))
          actions.push('refresh-layers');
        const attach = page.getByRole('button', { name: /^挂载$/ }).first();
        if (await attach.isVisible().catch(() => false)) {
          await attach.click({ timeout: 2000 }).catch(() => {});
          actions.push('attach-package');
          await page.waitForTimeout(400);
        }
        // expand a layer card
        if (await safeClick(page, page.getByText('Stable 核心', { exact: false }), '展开 core 层', route))
          actions.push('expand-layer');
      }

      if (route === '/wiki') {
        for (const name of ['宽松', '标准', '紧凑', '边标签', '重排', '列表', '图谱'] as const) {
          if (await safeClick(page, page.getByRole('button', { name }), name, route)) actions.push(name);
        }
      }

      if (route === '/settings') {
        // scroll main
        await page.evaluate(() => {
          const el =
            document.querySelector('main [class*="overflow-y-auto"]') ||
            document.querySelector('[class*="overflow-y-auto"]');
          if (el) (el as HTMLElement).scrollTop = (el as HTMLElement).scrollHeight;
        });
        await page.waitForTimeout(300);
        const hasFallback = await page.getByText('备用模型', { exact: false }).isVisible().catch(() => false);
        const hasCompress = await page.getByText('上下文压缩', { exact: false }).isVisible().catch(() => false);
        actions.push(hasFallback ? 'fallback-ok' : 'fallback-missing');
        actions.push(hasCompress ? 'compress-ok' : 'compress-missing');
        if (!hasFallback) bug(route, 'P1', 'missing-ui', '设置页未见「备用模型」');
        if (!hasCompress) bug(route, 'P1', 'missing-ui', '设置页未见「上下文压缩」');
        // toolset should NOT be present
        if (await page.getByText('工具集配置', { exact: false }).isVisible().catch(() => false)) {
          bug(route, 'P1', 'stale-ui', '设置页仍出现「工具集配置」');
        }
      }

      if (route === '/cron') {
        if (await safeClick(page, page.getByRole('button', { name: /新建/ }), '新建定时', route))
          actions.push('create');
        if (await page.getByRole('button', { name: /^取消$/ }).isVisible().catch(() => false)) {
          await page.getByRole('button', { name: /^取消$/ }).click();
        }
      }

      if (route === '/channels') {
        if (await safeClick(page, page.getByRole('button', { name: /添加/ }), '添加通道', route))
          actions.push('add');
        await page.keyboard.press('Escape');
      }

      if (route === '/tools') {
        const sw = page.locator('[role="switch"]').first();
        if (await sw.isVisible().catch(() => false)) {
          await sw.click({ timeout: 2000 }).catch(() => {});
          await page.waitForTimeout(300);
          await sw.click({ timeout: 2000 }).catch(() => {});
          actions.push('toggle-tool');
        }
        // name contrast proxy: tool names should not be gray-900 class in DOM text-foreground preferred - skip
      }

      if (route === '/skills') {
        const sw = page.locator('button[class*="rounded-full"]').first();
        // just open custom tab if exists
        if (await safeClick(page, page.getByRole('button', { name: /自定义|内置|社区/ }), 'skills-tab', route))
          actions.push('tab');
      }

      if (route === '/knowledge') {
        const clean = page.getByRole('button', { name: /清理重复/ });
        if ((await clean.count()) > 0) actions.push('dedupe-ui');
        if (await safeClick(page, page.getByRole('button', { name: /新建文档/ }), '新建文档', route))
          actions.push('new-doc');
        await page.keyboard.press('Escape');
      }

      if (route === '/tasks') {
        // open create if any
        if (await safeClick(page, page.getByRole('button', { name: /新建|创建/ }), '新建任务', route))
          actions.push('create');
        await page.keyboard.press('Escape');
      }

      if (route === '/mcp' || route === '/devices' || route === '/config') {
        // light click first non-destructive button
        const btn = page.getByRole('button').filter({ hasNotText: /删除|清空|退出/ }).first();
        if (await btn.isVisible().catch(() => false)) {
          await btn.click({ timeout: 2000 }).catch(() => {});
          actions.push('generic-btn');
          await page.keyboard.press('Escape');
        }
      }

      // screenshot
      try {
        await page.screenshot({
          path: path.join(OUT, `${route.replace(/\//g, '_') || 'home'}.png`),
          timeout: 5000,
        });
        actions.push('shot');
      } catch (e) {
        bug(route, 'P2', 'screenshot', String(e).slice(0, 100));
      }

      pageLog.push(entry);
    }

    // dedupe bugs
    const uniq = new Map<string, Bug>();
    for (const b of bugs) {
      const k = `${b.severity}|${b.kind}|${b.detail.slice(0, 120)}`;
      if (!uniq.has(k)) uniq.set(k, b);
    }
    const finalBugs = [...uniq.values()];

    const report = {
      generated_at: new Date().toISOString(),
      base: BASE,
      summary: {
        pages: PAGES.length,
        bugs: finalBugs.length,
        p0: finalBugs.filter((b) => b.severity === 'P0').length,
        p1: finalBugs.filter((b) => b.severity === 'P1').length,
        p2: finalBugs.filter((b) => b.severity === 'P2').length,
        info: finalBugs.filter((b) => b.severity === 'info').length,
      },
      bugs: finalBugs,
      pageLog,
    };
    fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));

    console.log('\n==== INTERACTIVE QA ====');
    console.log(JSON.stringify(report.summary, null, 2));
    for (const b of finalBugs) {
      console.log(`[${b.severity}] ${b.page} | ${b.kind} | ${b.detail}`);
    }
    console.log('report path:', path.join(OUT, 'report.json'));

    // Soft assert: always pass file write; hard assert only if catastrophic health
    const healthP0 = finalBugs.filter((b) => b.kind === 'health');
    expect(healthP0, JSON.stringify(healthP0)).toHaveLength(0);

    await context.close();
  });
});
