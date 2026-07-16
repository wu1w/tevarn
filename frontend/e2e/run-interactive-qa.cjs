const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://127.0.0.1:3000';
const API = 'http://127.0.0.1:8000/api';
const OUT = path.resolve(__dirname, 'screenshots/interactive-qa');
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'dark',
  });
  const res = await fetch(`${API}/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  const data = await res.json();
  const token = data.access_token;
  await context.addCookies([
    {
      name: 'takton-auth',
      value: token,
      domain: '127.0.0.1',
      path: '/',
      sameSite: 'Lax',
    },
  ]);
  await context.addInitScript((payload) => {
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
    document.cookie = `takton-auth=${payload.token}; path=/; max-age=604800; SameSite=Lax`;
  }, { user: data.user, token });

  const page = await context.newPage();
  const bugs = [];
  const log = [];
  const note = (pageName, sev, kind, detail, url) =>
    bugs.push({ page: pageName, severity: sev, kind, detail, url });

  page.on('pageerror', (e) => note('runtime', 'P0', 'pageerror', e.message));
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const t = msg.text();
    if (/favicon|net::ERR|Failed to load resource/i.test(t)) return;
    note('runtime', 'P1', 'console-error', t.slice(0, 200));
  });
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 500) {
      note('api', 'P0', 'api-5xx', `${r.status()} ${r.url()}`);
    }
  });

  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(1500);

  const links = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a[href]')).map((a) => ({
      href: a.getAttribute('href'),
      text: (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      inNav: !!a.closest('nav'),
    }))
  );
  fs.writeFileSync(path.join(OUT, 'dom-links.json'), JSON.stringify(links, null, 2));
  console.log(
    'route links',
    links.filter((l) => l.href && l.href.startsWith('/')).map((l) => `${l.href}|${l.text}|nav=${l.inNav}`)
  );

  const routes = [
    ['任务', '/tasks'],
    ['设备', '/devices'],
    ['工作流', '/workflows'],
    ['心智配置', '/config'],
    ['工具', '/tools'],
    ['MCP', '/mcp'],
    ['子代理', '/profiles'],
    ['上下文', '/context'],
    ['定时任务', '/cron'],
    ['知识库', '/knowledge'],
    ['Wiki', '/wiki'],
    ['消息通道', '/channels'],
    ['设置', '/settings'],
  ];

  for (const [label, href] of routes) {
    let clicked = false;
    const byRole = page.getByRole('link', { name: new RegExp(label) });
    if (await byRole.count()) {
      await byRole.first().click({ timeout: 4000 }).catch(() => {});
      clicked = true;
    } else {
      const byText = page.locator(`a:has-text("${label}")`).first();
      if (await byText.count()) {
        await byText.click({ timeout: 4000 }).catch(() => {});
        clicked = true;
      }
    }
    await page.waitForTimeout(700);
    const u = page.url();
    if (!clicked) {
      note('sidebar', 'P1', 'missing-nav', `找不到链接「${label}」-> ${href}`);
      continue;
    }
    if (u.includes('/login')) {
      note('sidebar', 'P0', 'nav-to-login', `${label} 跳登录`, u);
      await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(800);
      continue;
    }
    if (!u.includes(href)) {
      note('sidebar', 'P1', 'nav-wrong', `${label}: 期望 ${href} 实际 ${u}`, u);
    } else {
      log.push({ kind: 'nav-ok', label, href, url: u });
    }
  }

  const pages = [
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
  ];

  async function clickName(route, name, re) {
    const loc = page.getByRole('button', { name: re });
    if (!(await loc.count())) return false;
    try {
      await loc.first().click({ timeout: 2500 });
      await page.waitForTimeout(300);
      const cancel = page.getByRole('button', { name: /^取消$/ });
      if (await cancel.isVisible().catch(() => false)) await cancel.click();
      await page.keyboard.press('Escape').catch(() => {});
      if (page.url().includes('/login')) note(route, 'P0', 'click-to-login', name);
      return true;
    } catch {
      return false;
    }
  }

  for (const route of pages) {
    await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(900);
    if (page.url().includes('/login')) {
      note(route, 'P0', 'auth-redirect', '未登录跳转', page.url());
      continue;
    }
    const bodyText = await page.locator('body').innerText().catch(() => '');
    if (/出了点问题|应用遇到了一个意外错误/.test(bodyText)) {
      note(route, 'P0', 'error-boundary', '渲染错误边界');
    }
    const probes = [];

    if (route === '/') {
      if (await clickName(route, '新对话', /新对话/)) probes.push('new-chat');
      const sess = page.locator('text=历史会话').first();
      if (await sess.count()) {
        await sess.click().catch(() => {});
        probes.push('sessions');
      }
    }
    if (route === '/profiles' && (await clickName(route, '新建', /新建/))) probes.push('create');
    if (route === '/workflows') {
      const ai = await page.getByRole('button', { name: /AI 生成|生成并打开/ }).count();
      if (!ai) note(route, 'P1', 'missing-ui', '无 AI 生成按钮');
      else probes.push('ai-bar');
      if (await clickName(route, '新建', /新建/)) probes.push('create');
    }
    if (route === '/context') {
      if (await clickName(route, '刷新', /^刷新$/)) probes.push('refresh');
      const attach = page.getByRole('button', { name: /^挂载$/ }).first();
      if (await attach.isVisible().catch(() => false)) {
        await attach.click().catch(() => {});
        probes.push('attach');
      }
    }
    if (route === '/wiki') {
      for (const n of ['宽松', '标准', '紧凑', '边标签', '重排']) {
        if (await clickName(route, n, new RegExp(`^${n}$`))) probes.push(n);
      }
    }
    if (route === '/settings') {
      await page.evaluate(() => {
        document.querySelectorAll('*').forEach((el) => {
          const s = getComputedStyle(el);
          if (
            (s.overflowY === 'auto' || s.overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 20
          ) {
            el.scrollTop = el.scrollHeight;
          }
        });
      });
      await page.waitForTimeout(400);
      const t = await page.locator('body').innerText();
      if (!t.includes('备用模型')) note(route, 'P1', 'missing-ui', '未见备用模型（滚动后）');
      else probes.push('fallback-ok');
      if (!t.includes('上下文压缩')) note(route, 'P1', 'missing-ui', '未见上下文压缩（滚动后）');
      else probes.push('compress-ok');
      if (t.includes('工具集配置')) note(route, 'P1', 'stale-ui', '仍有工具集配置');
      if (t.includes('快捷入口')) note(route, 'P1', 'stale-ui', '仍有快捷入口');
    }
    if (route === '/cron' && (await clickName(route, '新建', /新建/))) probes.push('create');
    if (route === '/channels' && (await clickName(route, '添加', /添加/))) probes.push('add');
    if (route === '/knowledge') {
      if (await page.getByRole('button', { name: /清理重复/ }).count()) probes.push('dedupe');
      if (await clickName(route, '新建文档', /新建文档/)) probes.push('new-doc');
    }
    if (route === '/tools') {
      const sw = page.locator('[role="switch"]').first();
      if (await sw.isVisible().catch(() => false)) {
        await sw.click().catch(() => {});
        await page.waitForTimeout(200);
        await sw.click().catch(() => {});
        probes.push('toggle');
      }
    }
    if (route === '/skills' && (await clickName(route, 'tab', /内置|自定义|社区/))) probes.push('tab');

    await page
      .screenshot({
        path: path.join(OUT, `p${route.replace(/\//g, '_') || 'home'}.png`),
        timeout: 5000,
      })
      .catch(() => {});
    log.push({ route, url: page.url(), probes });
  }

  const map = new Map();
  for (const b of bugs) map.set(`${b.severity}|${b.kind}|${b.detail}`, b);
  const finalBugs = [...map.values()];
  const report = {
    generated_at: new Date().toISOString(),
    summary: {
      bugs: finalBugs.length,
      p0: finalBugs.filter((b) => b.severity === 'P0').length,
      p1: finalBugs.filter((b) => b.severity === 'P1').length,
      p2: finalBugs.filter((b) => b.severity === 'P2').length,
      nav_ok: log.filter((x) => x.kind === 'nav-ok').length,
      pages: pages.length,
      links: links.length,
    },
    bugs: finalBugs,
    log,
  };
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report.summary, null, 2));
  for (const b of finalBugs) console.log(`[${b.severity}] ${b.page} | ${b.kind} | ${b.detail}`);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
