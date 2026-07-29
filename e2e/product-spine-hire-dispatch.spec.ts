/**
 * 0.4.6 Product Spine — Playwright 骨架：招人 → 派活 UI
 *
 * 默认依赖本地已启动：
 *   SMOKE_BASE_URL (FE, default http://localhost:3002)
 *   SMOKE_API_URL  (BE, default http://localhost:8090)
 *
 * 后端不可达时 skip，避免 CI 无服务硬红。
 * 真 LLM 不在此测；派活只验证 UI 提交与 API 回执。
 */
import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.SMOKE_BASE_URL || 'http://localhost:3002';
const API_URL = process.env.SMOKE_API_URL || 'http://localhost:8090';

async function apiHealthy(): Promise<boolean> {
  try {
    const r = await fetch(`${API_URL}/api/health`, { cache: 'no-store' });
    return r.ok;
  } catch {
    return false;
  }
}

async function ensureToken(): Promise<string> {
  const response = await fetch(`${API_URL}/api/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(`auto-login failed: ${response.status}`);
  }
  const body = await response.json();
  return body.access_token as string;
}

async function loginViaApi(page: Page, token: string) {
  await page.goto(`${BASE_URL}/login`);
  await page.waitForLoadState('domcontentloaded');
  await page.evaluate((tok) => {
    const payload = {
      state: { token: tok, user: { email: 'admin@takton.dev' }, isAuthenticated: true, hasHydrated: true },
      version: 0,
    };
    localStorage.setItem('takton-auth', JSON.stringify(payload));
    document.cookie = `takton-auth=${tok}; path=/; SameSite=Strict`;
  }, token);
}

test.describe('0.4.6 product spine — hire + dispatch', () => {
  test.beforeEach(async () => {
    if (!(await apiHealthy())) {
      test.skip(true, `backend not reachable at ${API_URL}`);
    }
  });

  test('API: hire identity + enqueue inbox (mock path, no LLM)', async () => {
    const token = await ensureToken();
    const name = `PW员工${Date.now().toString(36).slice(-5)}`;

    const hire = await fetch(`${API_URL}/api/kernel/identities`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        name,
        role: 'e2e spine',
        capabilities: ['file_read'],
        default_token_budget: 5000,
        create_skill_pack: true,
        persona: '简洁',
        duty: '烟雾测试',
        initial_memory: 'product spine e2e',
      }),
    });
    expect(hire.ok, await hire.text()).toBeTruthy();
    const ident = await hire.json();
    expect(ident.name).toBe(name);
    expect(ident.id).toBeTruthy();

    const enq = await fetch(`${API_URL}/api/kernel/inbox`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        identity_id: ident.id,
        instruction: 'e2e：主路径派活烟雾',
        source: 'manual',
        priority: 1,
      }),
    });
    const enqBody = await enq.json().catch(() => ({}));
    if (enq.status === 503) {
      test.skip(true, `inbox not enabled: ${enqBody.detail || enq.status}`);
    }
    expect(enq.status, JSON.stringify(enqBody)).toBe(200);
    expect(enqBody.status).toBe('pending');
    expect(enqBody.message || '').toContain(name);
  });

  test('UI: agents page shows hire CTA and inbox panel', async ({ page }) => {
    const token = await ensureToken();
    await loginViaApi(page, token);
    await page.goto(`${BASE_URL}/agents`);
    await page.waitForLoadState('networkidle');

    // 主路径文案或新建入口
    const hireBtn = page.getByRole('button', { name: /新建员工|Hire|New Agent|新建 Agent/i });
    await expect(hireBtn.first()).toBeVisible({ timeout: 15_000 });

    // 收件箱区域
    await expect(page.getByText(/收件箱|Inbox/i).first()).toBeVisible({ timeout: 10_000 });

    // 打开招聘向导
    await hireBtn.first().click();
    await expect(page.getByText(/新建员工|Hire employee|新建 Agent/i).first()).toBeVisible({
      timeout: 8_000,
    });
  });

  test('API: empty instruction returns human 400', async () => {
    const token = await ensureToken();
    const r = await fetch(`${API_URL}/api/kernel/inbox`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ identity_id: '00000000-0000-0000-0000-000000000001', instruction: '  ' }),
    });
    if (r.status === 503) {
      test.skip(true, 'inbox not enabled');
    }
    expect(r.status).toBe(400);
    const body = await r.json();
    expect(String(body.detail || '')).toMatch(/指令|instruction/i);
  });
});
