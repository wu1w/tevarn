import { test, expect } from '@playwright/test';
import * as path from 'path';

const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';
const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR || path.join(__dirname, 'screenshots');

test('debug login', async ({ page }) => {
  await page.goto(BASE_URL);
  await page.waitForLoadState('networkidle');
  console.log('initial url:', page.url());

  const email = page.locator('input[type="email"]').first();
  const password = page.locator('input[type="password"]').first();
  const form = page.locator('form').first();

  if (await email.isVisible().catch(() => false) && await password.isVisible().catch(() => false)) {
    console.log('found login form');
    await email.fill(process.env.TEST_EMAIL || 'admin@example.com');
    await password.fill(process.env.TEST_PASSWORD || 'admin123');
    await form.evaluate((f: HTMLFormElement) => f.submit());
    await page.waitForTimeout(5000);
    console.log('after login url:', page.url());
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'debug-after-login.png'), fullPage: true });
  } else {
    console.log('no login form');
  }
});
