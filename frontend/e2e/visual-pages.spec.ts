/**
 * Visual QA with auth: login via API, inject token, screenshot each page.
 */
import { test, expect, type BrowserContext, type Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const BASE = process.env.TAKTON_BASE_URL || 'http://127.0.0.1:3000';
const API = process.env.TAKTON_API_URL || 'http://127.0.0.1:8000/api';
const OUT = path.resolve(__dirname, 'screenshots/visual-qa');

const PAGES: { route: string; name: string; scrolls?: number }[] = [
  { route: '/', name: '01-home-chat', scrolls: 1 },
  { route: '/profiles', name: '02-profiles-subagents', scrolls: 2 },
  { route: '/workflows', name: '03-workflows', scrolls: 2 },
  { route: '/tasks', name: '04-tasks', scrolls: 1 },
  { route: '/context', name: '05-context', scrolls: 3 },
  { route: '/knowledge', name: '06-knowledge', scrolls: 2 },
  { route: '/wiki', name: '07-wiki', scrolls: 1 },
  { route: '/cron', name: '08-cron', scrolls: 2 },
  { route: '/tools', name: '09-tools', scrolls: 2 },
  { route: '/skills', name: '10-skills', scrolls: 2 },
  { route: '/mcp', name: '11-mcp', scrolls: 1 },
  { route: '/settings', name: '12-settings', scrolls: 4 },
  { route: '/channels', name: '13-channels', scrolls: 2 },
  { route: '/devices', name: '14-devices', scrolls: 1 },
  { route: '/config', name: '15-config', scrolls: 1 },
];

async function authBootstrap(context: BrowserContext) {
  const res = await fetch(`${API}/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!res.ok) throw new Error(`auto-login failed ${res.status}`);
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

  // zustand persist shape
  const authState = {
    state: {
      user: data.user,
      token,
      isAuthenticated: true,
      hasHydrated: true,
    },
    version: 0,
  };

  await context.addInitScript((payload) => {
    localStorage.setItem('takton-auth', JSON.stringify(payload.authState));
    document.cookie = `takton-auth=${payload.token}; path=/; max-age=${payload.expires}; SameSite=Lax`;
  }, { authState, token, expires });

  return token;
}

async function scrollMain(page: Page) {
  await page.evaluate(() => {
    const candidates = Array.from(
      document.querySelectorAll<HTMLElement>(
        '[class*="overflow-y-auto"], main, [class*="overflow-auto"]'
      )
    );
    let scrolled = false;
    for (const el of candidates) {
      if (el.scrollHeight > el.clientHeight + 40) {
        el.scrollBy(0, Math.floor(el.clientHeight * 0.9));
        scrolled = true;
        break;
      }
    }
    if (!scrolled) {
      window.scrollBy(0, Math.floor(window.innerHeight * 0.85));
    }
  });
}

test.describe('visual page capture (authenticated)', () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    fs.mkdirSync(OUT, { recursive: true });
  });

  for (const def of PAGES) {
    test(`capture ${def.name}`, async ({ browser }) => {
      const context = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        colorScheme: 'dark',
      });
      await authBootstrap(context);
      const page = await context.newPage();

      const url = `${BASE}${def.route}`;
      const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 });
      await page.waitForTimeout(2200);
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(400);

      // fail clearly if still on login
      const isLogin = page.url().includes('/login');
      if (isLogin) {
        await page.screenshot({ path: path.join(OUT, `${def.name}-LOGIN-REDIRECT.png`) });
      }
      expect(isLogin, `${def.route} should not redirect to login`).toBeFalsy();

      const status = resp?.status() ?? 0;
      await page.screenshot({ path: path.join(OUT, `${def.name}-top.png`), fullPage: false });
      try {
        await page.screenshot({ path: path.join(OUT, `${def.name}-full.png`), fullPage: true });
      } catch {
        /* overflow layouts */
      }

      const scrolls = def.scrolls ?? 1;
      for (let i = 1; i <= scrolls; i++) {
        await scrollMain(page);
        await page.waitForTimeout(600);
        await page.screenshot({
          path: path.join(OUT, `${def.name}-scroll${i}.png`),
          fullPage: false,
        });
      }

      fs.writeFileSync(
        path.join(OUT, `${def.name}.meta.json`),
        JSON.stringify(
          {
            route: def.route,
            url: page.url(),
            status,
            title: await page.title(),
            isLogin,
          },
          null,
          2
        )
      );

      expect(status).toBeLessThan(500);
      await context.close();
    });
  }
});
