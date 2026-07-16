const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const OUT = path.join('E:/项目/taktonl-0.1.0/frontend/e2e/screenshots/ux-review');
fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const b = await chromium.launch({ headless: true });
  const c = await b.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const d = await (
    await fetch('http://127.0.0.1:8000/api/auth/auto-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
  ).json();
  await c.addCookies([
    { name: 'takton-auth', value: d.access_token, domain: '127.0.0.1', path: '/', sameSite: 'Lax' },
  ]);
  await c.addInitScript(
    (p) => {
      localStorage.setItem(
        'takton-auth',
        JSON.stringify({
          state: { user: p.user, token: p.token, isAuthenticated: true, hasHydrated: true },
          version: 0,
        })
      );
      document.cookie = 'takton-auth=' + p.token + '; path=/; max-age=604800; SameSite=Lax';
    },
    { user: d.user, token: d.access_token }
  );
  const page = await c.newPage();
  for (const route of ['/', '/devices/', '/channels/', '/settings/', '/tasks/']) {
    await page.goto('http://127.0.0.1:3000' + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(900);
    const name = route === '/' ? 'home' : route.replace(/\//g, '');
    await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: false });
  }
  // home: expand sessions if collapsed
  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  const body = await page.locator('body').innerText();
  fs.writeFileSync(
    path.join(OUT, 'home-text.txt'),
    body.slice(0, 2500),
    'utf8'
  );
  await b.close();
  console.log('ok', OUT);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
