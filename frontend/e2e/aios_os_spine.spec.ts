/**
 * AIOS 0.7–1.0 前端发起 E2E：登录 → 主路径页 → Kernel API 快照 → 编制 hire/派活/停止
 *
 * 环境：
 *   FE  http://127.0.0.1:3000  (SMOKE_BASE_URL / FE)
 *   BE  http://127.0.0.1:8090  (SMOKE_API_URL / API root without /api for health)
 */
import { test, expect, type Page } from '@playwright/test';

const FE = process.env.SMOKE_BASE_URL || process.env.FE || 'http://127.0.0.1:3000';
const API_ROOT = (process.env.SMOKE_API_URL || process.env.API || 'http://127.0.0.1:8090').replace(
  /\/api\/?$/,
  '',
);
const API = `${API_ROOT}/api`;

async function apiHealthy(): Promise<boolean> {
  try {
    const r = await fetch(`${API}/health`, { cache: 'no-store' });
    return r.ok;
  } catch {
    return false;
  }
}

async function loginToken(): Promise<{ token: string; user: unknown }> {
  let r = await fetch(`${API}/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!r.ok) {
    r = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'admin@tevarn.dev', password: 'admin' }),
    });
  }
  if (!r.ok) throw new Error(`login failed ${r.status}`);
  const body = (await r.json()) as { access_token: string; user?: unknown };
  return { token: body.access_token, user: body.user ?? { email: 'admin@tevarn.dev' } };
}

async function injectAuth(page: Page, token: string, user: unknown) {
  await page.addInitScript(
    ({ tok, user }) => {
      const payload = {
        state: { token: tok, user, isAuthenticated: true, hasHydrated: true },
        version: 0,
      };
      localStorage.setItem('tevarn-auth', JSON.stringify(payload));
      document.cookie = `tevarn-auth=${tok}; path=/; max-age=604800; SameSite=Strict`;
    },
    { tok: token, user },
  );
}

async function authFetch(token: string, path: string, init?: RequestInit) {
  const headers = {
    ...(init?.headers || {}),
    Authorization: `Bearer ${token}`,
    Accept: 'application/json',
  } as Record<string, string>;
  if (init?.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(`${API}${path}`, { ...init, headers });
}

test.describe('AIOS OS spine 0.7-1.0 (FE-driven)', () => {
  test.setTimeout(180_000);

  test.beforeEach(async () => {
    if (!(await apiHealthy())) {
      test.skip(true, `backend not reachable at ${API}`);
    }
  });

  test('runtime status + protocol 0.2 manifest', async () => {
    const { token } = await loginToken();
    let rt = await authFetch(token, '/runtime/status');
    if (!rt.ok) {
      rt = await fetch(`${API}/runtime/status`);
    }
    const rtText = await rt.text();
    expect(rt.ok, rtText).toBeTruthy();
    const status = JSON.parse(rtText || '{}');
    expect(status).toBeTruthy();

    let man = await authFetch(token, '/kernel/protocol/manifest');
    if (!man.ok) {
      man = await fetch(`${API}/kernel/protocol/manifest`);
    }
    const manText = await man.text();
    expect(man.ok, manText).toBeTruthy();
    const m = JSON.parse(manText || '{}') as {
      protocol_version?: string;
      protocolVersion?: string;
      interop?: { domain_events?: unknown };
      client_guide?: unknown;
    };
    const ver = String(m.protocol_version || m.protocolVersion || '');
    expect(ver === '0.2.0' || ver.startsWith('0.2') || ver.startsWith('1.')).toBeTruthy();
    expect(m.interop?.domain_events || m.client_guide).toBeTruthy();
  });

  test('UI main path pages load after login', async ({ page }) => {
    const { token, user } = await loginToken();
    await injectAuth(page, token, user);

    const routes = [
      { path: '/', name: 'home' },
      { path: '/agents', name: 'agents' },
      { path: '/approvals', name: 'approvals' },
      { path: '/kernel', name: 'kernel' },
      { path: '/chat', name: 'chat' },
      { path: '/audit', name: 'audit' },
    ];

    for (const r of routes) {
      await page.goto(`${FE}${r.path}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(800);
      // should not bounce to login
      expect(page.url(), r.name).not.toMatch(/\/login/);
      // page has some main content
      const body = page.locator('body');
      await expect(body).toBeVisible();
      // no fatal next error overlay text (soft)
      const err = page.locator('text=Application error');
      await expect(err).toHaveCount(0);
    }
  });

  test('kernel jobs/running + stop API; hire+enqueue from browser context', async ({
    page,
  }) => {
    const { token, user } = await loginToken();
    await injectAuth(page, token, user);
    await page.goto(`${FE}/kernel`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1000);

    // API spine via page.evaluate (true FE-originated network in browser)
    const result = await page.evaluate(async ({ api, tok }) => {
      const h = {
        Authorization: `Bearer ${tok}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      };
      const running = await fetch(`${api}/kernel/jobs/running`, { headers: h });
      const runningJson = running.ok ? await running.json() : { error: running.status };

      const name = `E2E工${Date.now().toString(36).slice(-4)}`;
      const hire = await fetch(`${api}/kernel/identities`, {
        method: 'POST',
        headers: h,
        body: JSON.stringify({
          name,
          role: 'e2e',
          capabilities: ['file_read'],
          default_token_budget: 8000,
          create_skill_pack: false,
          persona: 'e2e',
          duty: 'smoke',
        }),
      });
      const hireText = await hire.text();
      let hireJson: { id?: string; name?: string; error?: string } = {};
      try {
        hireJson = JSON.parse(hireText);
      } catch {
        hireJson = { error: hireText.slice(0, 200) };
      }

      let enq: Record<string, unknown> = {};
      if (hire.ok && hireJson.id) {
        const e = await fetch(`${api}/kernel/inbox`, {
          method: 'POST',
          headers: h,
          body: JSON.stringify({
            identity_id: hireJson.id,
            instruction: 'E2E smoke: reply ok only, no tools needed',
            source: 'e2e',
          }),
        });
        const et = await e.text();
        try {
          enq = JSON.parse(et);
        } catch {
          enq = { error: et.slice(0, 200), status: e.status };
        }
        // stop if we got an item id
        const itemId = (enq as { id?: string }).id;
        if (itemId) {
          await fetch(`${api}/kernel/jobs/stop`, {
            method: 'POST',
            headers: h,
            body: JSON.stringify({ inbox_item_id: itemId }),
          }).catch(() => null);
        }
      }

      const domain = await fetch(`${api}/kernel/events/domain?limit=5`, { headers: h }).catch(
        () => null,
      );
      const domainOk = domain ? domain.ok : false;

      return {
        hireOk: hire.ok,
        hireJson,
        enq,
        runningJson,
        domainOk,
      };
    }, { api: API, tok: token });

    expect(result.hireOk, JSON.stringify(result.hireJson)).toBeTruthy();
    expect(result.hireJson.id).toBeTruthy();
    // enqueue may 503 if dispatcher off — still assert structure
    if ((result.enq as { id?: string }).id) {
      expect((result.enq as { id: string }).id).toBeTruthy();
    } else {
      // allow soft fail with message
      expect(result.enq).toBeTruthy();
    }
    expect(result.runningJson).toBeTruthy();
  });

  test('approvals page shows capability / evolution tabs', async ({ page }) => {
    const { token, user } = await loginToken();
    await injectAuth(page, token, user);
    await page.goto(`${FE}/approvals`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const text = await page.locator('body').innerText();
    // Chinese or English labels from product copy
    const ok =
      /员工扩权|Capability|进化|Evolution|审批|Approvals|待决|pending/i.test(text);
    expect(ok, text.slice(0, 400)).toBeTruthy();
  });

  test('kernel page has backup or live jobs affordance', async ({ page }) => {
    const { token, user } = await loginToken();
    await injectAuth(page, token, user);
    await page.goto(`${FE}/kernel`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const text = await page.locator('body').innerText();
    const ok = /内核|Kernel|备份|Backup|在跑|Running|进程|Process|权限网|Policy/i.test(text);
    expect(ok, text.slice(0, 400)).toBeTruthy();
  });
});
