const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = 'http://127.0.0.1:3000';
const API = 'http://127.0.0.1:8000/api';
const OUT = path.resolve(__dirname, 'screenshots/interactive-qa');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'dark',
  });
  const data = await (
    await fetch(API + '/auth/auto-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
  ).json();
  const token = data.access_token;
  await context.addCookies([
    { name: 'takton-auth', value: token, domain: '127.0.0.1', path: '/', sameSite: 'Lax' },
  ]);
  await context.addInitScript((p) => {
    localStorage.setItem(
      'takton-auth',
      JSON.stringify({
        state: {
          user: p.user,
          token: p.token,
          isAuthenticated: true,
          hasHydrated: true,
        },
        version: 0,
      })
    );
    document.cookie = `takton-auth=${p.token}; path=/; max-age=604800; SameSite=Lax`;
  }, { user: data.user, token });

  const page = await context.newPage();
  const bugs = [];
  const note = (p, s, k, d, u) => bugs.push({ page: p, severity: s, kind: k, detail: d, url: u });
  page.on('pageerror', (e) => note('runtime', 'P0', 'pageerror', e.message));
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 500) note('api', 'P0', 'api-5xx', r.status() + ' ' + r.url());
    if (r.url().includes('/api/') && r.status() === 404) note('api', 'P1', 'api-404', r.url());
  });

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  const hasSkillsLink = (await page.locator('a[href*="skills"]').count()) > 0;
  if (!hasSkillsLink) note('sidebar', 'P1', 'ia', '侧栏无 Skills 入口（/skills 只能直达）');

  for (const r of ['/tasks', '/tasks/', '/settings', '/settings/']) {
    await page.goto(BASE + r, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);
    if (page.url().includes('/login')) note(r, 'P0', 'auth', '跳登录');
    const broken = await page.locator('body').innerText();
    if (/出了点问题/.test(broken)) note(r, 'P0', 'error-boundary', broken.slice(0, 80));
  }

  await page.goto(BASE + '/workflows/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  const ex = page.getByRole('button', { name: /加载示例/ });
  if (await ex.count()) {
    await ex.click();
    await page.waitForTimeout(1500);
  }

  await page.goto(BASE + '/settings/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  await page.evaluate(() => {
    document.querySelectorAll('*').forEach((el) => {
      const s = getComputedStyle(el);
      if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
        el.scrollTop = el.scrollHeight;
      }
    });
  });
  await page.waitForTimeout(400);
  const selects = await page.locator('select').count();
  if (selects < 1) note('/settings', 'P1', 'fallback-select', '底部备用/压缩下拉未找到 select 数量=' + selects);
  else {
    const sel = page.locator('select').first();
    const opts = await sel.locator('option').count();
    if (opts > 1) await sel.selectOption({ index: 1 }).catch(() => {});
  }

  await page.goto(BASE + '/tools/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);
  const contrast = await page.evaluate(() => {
    const el = document.querySelector('span.font-medium, .text-sm.font-medium');
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { color: cs.color, text: (el.textContent || '').slice(0, 30), className: el.className };
  });
  if (contrast && /rgb\(17,\s*24,\s*39\)|rgb\(0,\s*0,\s*0\)|rgb\(31,\s*41,\s*55\)/.test(contrast.color)) {
    note('/tools', 'P1', 'contrast', '工具名颜色仍偏黑: ' + contrast.color + ' class=' + contrast.className);
  }

  await page.goto(BASE + '/context/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  const needSession = await page.getByText('请先选择一个会话').isVisible().catch(() => false);
  if (needSession) {
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(700);
    await page.getByRole('button', { name: /新对话/ }).click().catch(() => {});
    await page.waitForTimeout(700);
    await page.goto(BASE + '/context/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(900);
  }
  const attach = page.getByRole('button', { name: /^挂载$/ }).first();
  if (await attach.isVisible().catch(() => false)) {
    await attach.click();
    await page.waitForTimeout(700);
    const detach = page.getByRole('button', { name: /^卸载$/ }).first();
    if (!(await detach.isVisible().catch(() => false))) {
      note('/context', 'P1', 'package-attach', '挂载后未见卸载按钮');
    } else {
      await detach.click();
      await page.waitForTimeout(400);
    }
  }

  await page.goto(BASE + '/wiki/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  for (const n of ['紧凑', '标准', '宽松']) {
    if (!(await page.getByRole('button', { name: n }).count())) {
      note('/wiki', 'P1', 'missing-ui', '缺密度按钮 ' + n);
    }
  }

  await page.goto(BASE + '/skills/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  if (page.url().includes('/login')) note('/skills', 'P0', 'auth', 'skills 跳登录');

  const uniq = new Map();
  for (const b of bugs) uniq.set(b.severity + '|' + b.kind + '|' + b.detail, b);
  const finalBugs = [...uniq.values()];
  const report = {
    generated_at: new Date().toISOString(),
    summary: {
      bugs: finalBugs.length,
      p0: finalBugs.filter((b) => b.severity === 'P0').length,
      p1: finalBugs.filter((b) => b.severity === 'P1').length,
      p2: finalBugs.filter((b) => b.severity === 'P2').length,
    },
    bugs: finalBugs,
    notes: { hasSkillsLink, contrast, selects },
  };
  fs.writeFileSync(path.join(OUT, 'report-deep.json'), JSON.stringify(report, null, 2));

  const main = path.join(OUT, 'report.json');
  let base = {};
  try {
    base = JSON.parse(fs.readFileSync(main, 'utf8'));
  } catch {}
  base.deep = report;
  base.bugs = [...(base.bugs || []), ...finalBugs];
  const u2 = new Map();
  for (const b of base.bugs) u2.set(b.severity + '|' + b.kind + '|' + b.detail, b);
  base.bugs = [...u2.values()];
  base.summary = base.summary || {};
  base.summary.bugs = base.bugs.length;
  base.summary.p0 = base.bugs.filter((b) => b.severity === 'P0').length;
  base.summary.p1 = base.bugs.filter((b) => b.severity === 'P1').length;
  fs.writeFileSync(main, JSON.stringify(base, null, 2));

  console.log(JSON.stringify(report.summary, null, 2));
  for (const b of finalBugs) console.log(`[${b.severity}] ${b.page} | ${b.kind} | ${b.detail}`);
  console.log('skillsLink', hasSkillsLink, 'contrast', contrast, 'selects', selects);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
