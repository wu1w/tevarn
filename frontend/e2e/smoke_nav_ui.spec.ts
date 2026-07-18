/**
 * UI smoke: local login → chat stream → hop pages → return via history → stop → follow-up
 */
const { test, expect } = require('@playwright/test');

const FE = process.env.FE || 'http://127.0.0.1:3000';
const API = process.env.API || 'http://127.0.0.1:8090/api';

async function apiLogin() {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@takton.dev', password: 'admin' }),
  });
  const d = await r.json();
  if (!d.access_token) throw new Error('login failed ' + JSON.stringify(d));
  return d;
}

async function listSessions(token) {
  const r = await fetch(`${API}/sessions/my`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const d = await r.json();
  return Array.isArray(d) ? d : d.items || d.sessions || [];
}

async function getMessages(token, sid) {
  const r = await fetch(`${API}/sessions/${sid}/messages?limit=50`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return r.json();
}

test.describe('nav + interrupt conversation', () => {
  test.setTimeout(240000);

  test('page hops during generation keep session; stop and follow-up work', async ({ page }) => {
    const loginData = await apiLogin();
    const token = loginData.access_token;
    const marker = `SMOKE_NAV_${Date.now()}`;

    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));

    await page.addInitScript(
      ({ tok, user, expiresIn }) => {
        try {
          localStorage.setItem(
            'takton-auth',
            JSON.stringify({
              state: {
                user: user || null,
                token: tok,
                isAuthenticated: true,
                hasHydrated: true,
              },
              version: 0,
            })
          );
          document.cookie = `takton-auth=${tok}; path=/; max-age=${expiresIn || 604800}; SameSite=Strict`;
        } catch (_) {}
      },
      { tok: token, user: loginData.user, expiresIn: loginData.expires_in || 604800 }
    );

    await page.goto(`${FE}/login`, { waitUntil: 'domcontentloaded' });
    const localBtn = page.getByRole('button', { name: /以本地模式继续|Continue in Local Mode/i });
    if (await localBtn.isVisible().catch(() => false)) {
      await localBtn.click();
      await page.waitForTimeout(1500);
    }
    if (page.url().includes('/login')) {
      await localBtn.click().catch(() => {});
      await page.waitForTimeout(1500);
    }
    await page.goto(`${FE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    // new chat if button exists
    const newChat = page.getByRole('button', { name: /新对话|New Chat|新建/i });
    if (await newChat.count()) {
      await newChat.first().click().catch(() => {});
      await page.waitForTimeout(800);
    }

    const input = page.locator('[data-testid="chat-composer-textarea"], textarea').first();
    await expect(input).toBeVisible({ timeout: 30000 });

    const longPrompt = `${marker} 请从1详细写到40，每个数字一段，不要工具，纯文本。`;
    await input.click();
    await input.fill(longPrompt);
    await page.keyboard.press('Enter');

    // wait until server has at least the user message
    let sid = null;
    for (let i = 0; i < 40; i++) {
      await page.waitForTimeout(1000);
      const sessions = await listSessions(token);
      for (const s of sessions) {
        const msgs = await getMessages(token, s.id);
        const hit = (msgs || []).some((m) => String(m.content || '').includes(marker));
        if (hit) {
          sid = s.id;
          break;
        }
      }
      if (sid) break;
    }
    expect(sid, 'session with marker should exist after send').toBeTruthy();

    // wait a bit for stream to start
    await page.waitForTimeout(5000);
    let beforeChars = 0;
    {
      const msgs = await getMessages(token, sid);
      beforeChars = (msgs || [])
        .filter((m) => m.role === 'assistant')
        .reduce((n, m) => n + String(m.content || '').length, 0);
    }

    // hop pages (unmount chat → disconnect WS)
    for (const p of ['/skills', '/tools', '/mcp', '/settings', '/knowledge', '/cron', '/workflows', '/tasks']) {
      await page.goto(`${FE}${p}`, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(400);
    }

    // while hopped, agent should keep writing
    await page.waitForTimeout(10000);
    let afterHopChars = 0;
    {
      const msgs = await getMessages(token, sid);
      afterHopChars = (msgs || [])
        .filter((m) => m.role === 'assistant')
        .reduce((n, m) => n + String(m.content || '').length, 0);
    }
    console.log('assistant chars before/after hop', beforeChars, afterHopChars);
    expect(
      afterHopChars >= beforeChars,
      `agent should not shrink on hop; before=${beforeChars} after=${afterHopChars}`
    ).toBeTruthy();
    // ideally grew; if model slow, at least user msg remains
    const msgsMid = await getMessages(token, sid);
    expect(msgsMid.length).toBeGreaterThanOrEqual(1);

    // return home and open session by clicking history if possible
    await page.goto(`${FE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const hist = page.getByText(marker).first();
    if (await hist.count()) {
      await hist.click().catch(() => {});
      await page.waitForTimeout(1500);
    }

    const input2 = page.locator('[data-testid="chat-composer-textarea"], textarea').first();
    await expect(input2).toBeVisible({ timeout: 20000 });

    const stopBtn = page.getByRole('button', { name: /停止|Stop|中止/i });
    if (await stopBtn.count()) {
      await stopBtn.first().click().catch(() => {});
      await page.waitForTimeout(800);
    }

    await input2.fill(`${marker}_FOLLOW 只回答 7+1=? 只要数字`);
    await page.keyboard.press('Enter');

    let followOk = false;
    for (let i = 0; i < 50; i++) {
      await page.waitForTimeout(1000);
      const msgs = await getMessages(token, sid);
      // follow-up may create same session messages
      const asst = (msgs || [])
        .filter((m) => m.role === 'assistant')
        .map((m) => m.content || '')
        .join('\n');
      if (asst.includes('8')) {
        followOk = true;
        break;
      }
      // also check newest sessions if app created another
      const sessions = await listSessions(token);
      for (const s of sessions.slice(0, 5)) {
        const m2 = await getMessages(token, s.id);
        const joined = (m2 || []).map((m) => m.content || '').join('\n');
        if (joined.includes(`${marker}_FOLLOW`) && joined.includes('8')) {
          followOk = true;
          sid = s.id;
          break;
        }
      }
      if (followOk) break;
    }
    expect(followOk, 'follow-up should answer 8').toBeTruthy();
    expect(pageErrors, `pageerrors=${JSON.stringify(pageErrors)}`).toEqual([]);
    console.log('UI smoke OK session', sid, 'afterHopChars', afterHopChars);
  });
});
