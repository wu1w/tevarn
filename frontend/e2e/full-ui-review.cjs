/**
 * Full visual QA: every route × dark/light × top/bottom scroll.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://127.0.0.1:3000';
const API = 'http://127.0.0.1:8000/api';
const OUT = path.join('E:/项目/taktonl-0.1.0/frontend/e2e/screenshots/full-ui-review');

const ROUTES = [
  { id: 'home', path: '/' },
  { id: 'tasks', path: '/tasks/' },
  { id: 'devices', path: '/devices/' },
  { id: 'workflows', path: '/workflows/' },
  { id: 'config', path: '/config/' },
  { id: 'tools', path: '/tools/' },
  { id: 'skills', path: '/skills/' },
  { id: 'mcp', path: '/mcp/' },
  { id: 'profiles', path: '/profiles/' },
  { id: 'context', path: '/context/' },
  { id: 'cron', path: '/cron/' },
  { id: 'knowledge', path: '/knowledge/' },
  { id: 'wiki', path: '/wiki/' },
  { id: 'channels', path: '/channels/' },
  { id: 'settings', path: '/settings/' },
];

function ensureDir(d) {
  fs.mkdirSync(d, { recursive: true });
}

async function setTheme(page, theme) {
  // themeStore: preference system|light|dark, storage key takton-theme
  await page.addInitScript((t) => {
    try {
      localStorage.setItem(
        'takton-theme',
        JSON.stringify({ state: { preference: t }, version: 0 })
      );
    } catch (_) {}
    document.documentElement.classList.remove('dark', 'light');
    if (t === 'dark') document.documentElement.classList.add('dark');
    else document.documentElement.classList.remove('dark');
    document.documentElement.style.colorScheme = t;
  }, theme);
}

async function forceTheme(page, theme) {
  await page.evaluate((t) => {
    try {
      localStorage.setItem(
        'takton-theme',
        JSON.stringify({ state: { preference: t }, version: 0 })
      );
    } catch (_) {}
    const root = document.documentElement;
    if (t === 'dark') {
      root.classList.add('dark');
      root.style.colorScheme = 'dark';
    } else {
      root.classList.remove('dark');
      root.style.colorScheme = 'light';
    }
    // zustand persist may rehydrate later
    window.dispatchEvent(new Event('storage'));
  }, theme);
  await page.waitForTimeout(200);
}

async function shot(page, file) {
  await page.screenshot({ path: file, fullPage: false });
}

async function scrollMain(page, direction) {
  await page.evaluate((dir) => {
    const candidates = [
      document.querySelector('main'),
      document.querySelector('.main-workbench'),
      document.querySelector('[class*="overflow-y-auto"]'),
      document.scrollingElement,
    ].filter(Boolean);
    for (const el of candidates) {
      if (el.scrollHeight > el.clientHeight + 40) {
        el.scrollTop = dir === 'bottom' ? el.scrollHeight : 0;
        return;
      }
    }
    window.scrollTo(0, dir === 'bottom' ? document.body.scrollHeight : 0);
  }, direction);
  await page.waitForTimeout(350);
}

(async () => {
  ensureDir(OUT);
  const manifest = [];
  const browser = await chromium.launch({ headless: true });

  for (const theme of ['dark', 'light']) {
    const themeDir = path.join(OUT, theme);
    ensureDir(themeDir);

    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: theme === 'dark' ? 'dark' : 'light',
    });

    // auth
    const login = await (
      await fetch(`${API}/auth/auto-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
    ).json();

    await context.addCookies([
      {
        name: 'takton-auth',
        value: login.access_token,
        domain: '127.0.0.1',
        path: '/',
        sameSite: 'Lax',
      },
    ]);

    await context.addInitScript(
      (p) => {
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
        document.cookie =
          'takton-auth=' + p.token + '; path=/; max-age=604800; SameSite=Lax';
        localStorage.setItem(
          'takton-theme',
          JSON.stringify({ state: { preference: p.theme }, version: 0 })
        );
      },
      { user: login.user, token: login.access_token, theme }
    );

    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    for (const route of ROUTES) {
      const entry = {
        theme,
        id: route.id,
        path: route.path,
        files: [],
        errors: [],
        scrollable: false,
      };
      try {
        await page.goto(BASE + route.path, {
          waitUntil: 'domcontentloaded',
          timeout: 25000,
        });
        await forceTheme(page, theme);
        await page.waitForTimeout(700);

        // expand collapsed sidebar groups so nav visible in shots
        await page.evaluate(() => {
          document.querySelectorAll('nav button').forEach((b) => {
            const t = (b.textContent || '').trim();
            if (/Agent|记忆|系统|工作区/.test(t) && t.includes('+')) {
              (b).click();
            }
          });
        });
        await page.waitForTimeout(200);

        const topPath = path.join(themeDir, `${route.id}__top.png`);
        await shot(page, topPath);
        entry.files.push(topPath);

        const metrics = await page.evaluate(() => {
          const els = Array.from(
            document.querySelectorAll('main, .main-workbench, [class*="overflow-y"]')
          );
          let max = 0;
          for (const el of els) {
            max = Math.max(max, el.scrollHeight - el.clientHeight);
          }
          return {
            scrollRange: max,
            bodyH: document.body.scrollHeight,
            viewH: window.innerHeight,
            title: document.title,
          };
        });
        entry.scrollable = metrics.scrollRange > 80 || metrics.bodyH > metrics.viewH + 80;

        if (entry.scrollable) {
          await scrollMain(page, 'bottom');
          const botPath = path.join(themeDir, `${route.id}__bottom.png`);
          await shot(page, botPath);
          entry.files.push(botPath);
          await scrollMain(page, 'top');
        }

        // full page long capture for settings/knowledge etc.
        if (entry.scrollable) {
          const fullPath = path.join(themeDir, `${route.id}__full.png`);
          await page.screenshot({ path: fullPath, fullPage: true });
          entry.files.push(fullPath);
        }

        entry.metrics = metrics;
      } catch (e) {
        entry.errors.push(String(e));
      }
      entry.pageErrors = errors.splice(0);
      manifest.push(entry);
      console.log(
        theme,
        route.id,
        entry.files.length,
        entry.scrollable ? 'scroll' : 'fit',
        entry.errors[0] || 'ok'
      );
    }

    await context.close();
  }

  await browser.close();
  fs.writeFileSync(
    path.join(OUT, 'manifest.json'),
    JSON.stringify(manifest, null, 2),
    'utf8'
  );
  // flat list of all png for delivery
  const all = [];
  for (const theme of ['dark', 'light']) {
    const dir = path.join(OUT, theme);
    for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.png'))) {
      all.push(path.join(dir, f));
    }
  }
  fs.writeFileSync(path.join(OUT, 'all-files.txt'), all.join('\n'), 'utf8');
  console.log('TOTAL', all.length, 'OUT', OUT);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
