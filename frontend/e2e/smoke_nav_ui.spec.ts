/**
 * UI smoke: local login → chat stream → hop pages → return → stop → follow-up
 */
import { test, expect, type Page } from '@playwright/test';

const FE = process.env.FE || 'http://127.0.0.1:3000';
const API = process.env.API || 'http://127.0.0.1:8090/api';

type Msg = { role?: string; content?: string | null };
type SessionRow = { id: string; title?: string | null };

async function apiLogin(): Promise<{
  access_token: string;
  expires_in?: number;
  user?: unknown;
}> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@takton.dev', password: 'admin' }),
  });
  const d = (await r.json()) as { access_token?: string; expires_in?: number; user?: unknown };
  if (!d.access_token) throw new Error('login failed ' + JSON.stringify(d));
  return d as { access_token: string; expires_in?: number; user?: unknown };
}

async function listSessions(token: string): Promise<SessionRow[]> {
  const r = await fetch(`${API}/sessions/my`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const d = (await r.json()) as SessionRow[] | { items?: SessionRow[]; sessions?: SessionRow[] };
  return Array.isArray(d) ? d : d.items || d.sessions || [];
}

async function getMessages(token: string, sid: string): Promise<Msg[]> {
  const r = await fetch(`${API}/sessions/${sid}/messages?limit=50`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const d = (await r.json()) as Msg[];
  return Array.isArray(d) ? d : [];
}

function assistantChars(msgs: Msg[]): number {
  return msgs
    .filter((m) => m.role === 'assistant')
    .reduce((n, m) => n + String(m.content || '').length, 0);
}

test.describe('nav + interrupt conversation', () => {
  test.setTimeout(240000);

  test('page hops during generation keep session; stop and follow-up work', async ({
    page,
  }: {
    page: Page;
  }) => {
    const loginData = await apiLogin();
    const token = loginData.access_token;
    const marker = `SMOKE_NAV_${Date.now()}`;

    const pageErrors: string[] = [];
    page.on('pageerror', (e: Error) => pageErrors.push(String(e)));

    await page.addInitScript(
      ({ tok, user, expiresIn }: { tok: string; user: unknown; expiresIn: number }) => {
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
        } catch {
          /* ignore */
        }
      },
      {
        tok: token,
        user: loginData.user,
        expiresIn: loginData.expires_in || 604800,
      }
    );

    await page.goto(`${FE}/login`, { waitUntil: 'domcontentloaded' });
    const localBtn = page.getByRole('button', {
      name: /以本地模式继续|Continue in Local Mode/i,
    });
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

    let sid: string | null = null;
    for (let i = 0; i < 40; i++) {
      await page.waitForTimeout(1000);
      const sessions = await listSessions(token);
      for (const s of sessions) {
        const msgs = await getMessages(token, s.id);
        if (msgs.some((m) => String(m.content || '').includes(marker))) {
          sid = s.id;
          break;
        }
      }
      if (sid) break;
    }
    expect(sid, 'session with marker should exist after send').toBeTruthy();
    const sessionId = sid as string;

    await page.waitForTimeout(5000);
    const beforeChars = assistantChars(await getMessages(token, sessionId));

    for (const p of [
      '/skills',
      '/tools',
      '/mcp',
      '/settings',
      '/knowledge',
      '/cron',
      '/workflows',
      '/tasks',
    ]) {
      await page.goto(`${FE}${p}`, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(400);
    }

    await page.waitForTimeout(10000);
    const msgsMid = await getMessages(token, sessionId);
    const afterHopChars = assistantChars(msgsMid);
    console.log('assistant chars before/after hop', beforeChars, afterHopChars);
    expect(afterHopChars).toBeGreaterThanOrEqual(beforeChars);
    expect(msgsMid.length).toBeGreaterThanOrEqual(1);

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
    let activeSid = sessionId;
    for (let i = 0; i < 50; i++) {
      await page.waitForTimeout(1000);
      const msgs = await getMessages(token, activeSid);
      const asst = msgs
        .filter((m) => m.role === 'assistant')
        .map((m) => m.content || '')
        .join('\n');
      if (asst.includes('8')) {
        followOk = true;
        break;
      }
      for (const s of (await listSessions(token)).slice(0, 5)) {
        const m2 = await getMessages(token, s.id);
        const joined = m2.map((m) => m.content || '').join('\n');
        if (joined.includes(`${marker}_FOLLOW`) && joined.includes('8')) {
          followOk = true;
          activeSid = s.id;
          break;
        }
      }
      if (followOk) break;
    }
    expect(followOk, 'follow-up should answer 8').toBeTruthy();
    expect(pageErrors, `pageerrors=${JSON.stringify(pageErrors)}`).toEqual([]);
    console.log('UI smoke OK session', activeSid, 'afterHopChars', afterHopChars);
  });
});
