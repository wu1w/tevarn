/**
 * Capture polished UI pages for review
 * SMOKE_BASE_URL=http://127.0.0.1:3015 SMOKE_API_URL=http://127.0.0.1:8095 npx playwright test e2e/ui_v2_pages_shot.spec.ts
 */
import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const BASE = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:3015';
const API = process.env.SMOKE_API_URL || 'http://127.0.0.1:8095';
const OUT = process.env.SCREENSHOT_DIR || '/tmp/takton-ui-v2-shots';

const PAGES: { path: string; name: string }[] = [
  { path: '/', name: '01-chat' },
  { path: '/tasks', name: '02-tasks' },
  { path: '/devices', name: '03-devices' },
  { path: '/workflows', name: '04-workflows' },
  { path: '/config', name: '05-config' },
  { path: '/tools', name: '06-tools' },
  { path: '/skills', name: '07-skills' },
  { path: '/evolution', name: '08-evolution' },
  { path: '/mcp', name: '09-mcp' },
  { path: '/profiles', name: '10-profiles' },
  { path: '/context', name: '11-context' },
  { path: '/cron', name: '12-cron' },
  { path: '/knowledge', name: '13-knowledge' },
  { path: '/memory', name: '14-memory' },
  { path: '/wiki', name: '15-wiki' },
  { path: '/channels', name: '16-channels' },
  { path: '/settings', name: '17-settings' },
  { path: '/profile', name: '18-profile' },
];

async function login(page: Page) {
  const response = await fetch(`${API}/api/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!response.ok) throw new Error(`login ${response.status}`);
  const body = (await response.json()) as { access_token: string; user: unknown };
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState('domcontentloaded');
  await page.evaluate(
    ({ token, user }) => {
      localStorage.setItem(
        'takton-auth',
        JSON.stringify({
          state: { token, user, isAuthenticated: true, hasHydrated: true },
          version: 0,
        })
      );
      localStorage.setItem('takton-sidebar-open', '1');
      document.cookie = `takton-auth=${token}; path=/; SameSite=Strict`;
      // force dark theme for consistent shots
      document.documentElement.setAttribute('data-theme', 'dark');
      localStorage.setItem('takton-theme', JSON.stringify({ state: { theme: 'dark' }, version: 0 }));
    },
    { token: body.access_token, user: body.user }
  );
}

test('ui v2 page gallery', async ({ page }) => {
  test.setTimeout(180_000);
  fs.mkdirSync(OUT, { recursive: true });
  await login(page);

  for (const pg of PAGES) {
    await page.goto(`${BASE}${pg.path}`);
    await page.waitForLoadState('networkidle').catch(() => null);
    await page.waitForTimeout(900);
    // ensure theme
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'dark');
    });
    await page.screenshot({
      path: path.join(OUT, `${pg.name}-dark.png`),
      fullPage: false,
    });
    // light
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'light');
    });
    await page.waitForTimeout(350);
    await page.screenshot({
      path: path.join(OUT, `${pg.name}-light.png`),
      fullPage: false,
    });
    console.log('shot', pg.name);
  }

  // rail collapsed dark on chat
  await page.goto(`${BASE}/`);
  await page.evaluate(() => {
    document.documentElement.setAttribute('data-theme', 'dark');
    localStorage.setItem('takton-sidebar-open', '0');
  });
  await page.reload();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, '19-chat-sidebar-collapsed-dark.png') });
});
