import * as path from 'path';
import { test, expect, Page, BrowserContext } from '@playwright/test';

const BASE_URL = process.env.SMOKE_BASE_URL || 'http://localhost:3002';
const API_URL = process.env.SMOKE_API_URL || 'http://localhost:8090';
const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR || path.join(__dirname, 'screenshots');

let sharedToken: string | null = null;
let sharedUser: unknown | null = null;

async function ensureToken() {
  if (sharedToken) return { token: sharedToken, user: sharedUser };
  const response = await fetch(`${API_URL}/api/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`);
  }
  const body = await response.json();
  sharedToken = body.access_token;
  sharedUser = body.user;
  return { token: sharedToken, user: sharedUser };
}

async function loginViaApi(page: Page) {
  const { token, user } = await ensureToken();

  await page.goto(`${BASE_URL}/login`);
  await page.waitForLoadState('networkidle');

  await page.evaluate(
    ({ token, user }) => {
      const payload = { state: { token, user, isAuthenticated: true, hasHydrated: true }, version: 0 };
      localStorage.setItem('takton-auth', JSON.stringify(payload));
      document.cookie = `takton-auth=${token}; path=/; SameSite=Strict`;
    },
    { token, user }
  );

  await page.goto(`${BASE_URL}/`);
  await page.waitForLoadState('networkidle');
  await expect(page).not.toHaveURL(/\/login/);
}

async function capturePage(page: Page, name: string) {
  await page.waitForLoadState('networkidle');
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/${name}-initial.png`,
    fullPage: true,
  });
  await page.evaluate(() => window.scrollBy(0, 300));
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/${name}-scrolled.png`,
    fullPage: true,
  });
}

async function clickInteractiveElements(page: Page, name: string) {
  const buttons = await page.locator('button').all();
  let clicked = 0;
  for (const btn of buttons.slice(0, 5)) {
    if (await btn.isVisible().catch(() => false) && await btn.isEnabled().catch(() => false)) {
      await btn.click({ force: true }).catch(() => {});
      clicked += 1;
      await page.waitForTimeout(300);
    }
  }
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/${name}-after-click.png`,
    fullPage: true,
  });
}

const routes = [
  { path: '/', name: 'home' },
  { path: '/tasks', name: 'tasks' },
  { path: '/knowledge', name: 'knowledge' },
  { path: '/settings', name: 'settings' },
  { path: '/cron', name: 'cron' },
  { path: '/workflows', name: 'workflow' },
  { path: '/context', name: 'context' },
  { path: '/tools', name: 'tools' },
];

test.describe('Takton smoke E2E', () => {
  for (const route of routes) {
    test(`${route.name} ${route.path}`, async ({ page }) => {
      await loginViaApi(page);
      await page.goto(`${BASE_URL}${route.path}`);
      await capturePage(page, route.name);
      await clickInteractiveElements(page, route.name);

      if (route.name === 'home') {
        const newChatBtn = page.locator('[data-testid="new-chat-button"], button:has-text("新建会话"), button:has-text("New Chat")').first();
        if (await newChatBtn.isVisible().catch(() => false)) {
          await newChatBtn.click().catch(() => {});
          await page.waitForTimeout(500);
          await page.screenshot({
            path: `${SCREENSHOT_DIR}/home-after-new-session.png`,
            fullPage: true,
          });
        }
      }
    });
  }

  test('login /login', async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.waitForLoadState('networkidle');
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/login-initial.png`,
      fullPage: true,
    });
  });
});
