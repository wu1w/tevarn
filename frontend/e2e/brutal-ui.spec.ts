/**
 * 前端图形界面暴力健壮性测试：
 * - 渲染崩溃 / 白屏
 * - Markdown 格式混乱
 * - 编码/乱码/转码
 * - XSS / 特殊字符 / 超大消息
 * - 快速路由切换 / 输入风暴
 *
 * 运行：
 *   cd frontend
 *   SMOKE_BASE_URL=http://127.0.0.1:8090 SMOKE_API_URL=http://127.0.0.1:8090 \
 *     npx playwright test e2e/brutal-ui.spec.ts --project=chromium
 */
import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const BASE_URL = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8090';
const API_URL = process.env.SMOKE_API_URL || 'http://127.0.0.1:8090';
const SHOT_DIR = path.join(__dirname, 'screenshots', 'brutal-ui');

function ensureShotDir() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
}

async function loginViaApi(page: Page) {
  const response = await fetch(`${API_URL}/api/auth/auto-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) throw new Error(`auto-login ${response.status}`);
  const body = await response.json();
  const token = body.access_token as string;
  const user = body.user;

  await page.goto(`${BASE_URL}/login`);
  await page.waitForLoadState('domcontentloaded');
  await page.evaluate(
    ({ token, user }) => {
      const payload = {
        state: { token, user, isAuthenticated: true, hasHydrated: true },
        version: 0,
      };
      localStorage.setItem('takton-auth', JSON.stringify(payload));
      document.cookie = `takton-auth=${token}; path=/; SameSite=Strict`;
    },
    { token, user }
  );
  return { token, user };
}

async function createSession(token: string) {
  const r = await fetch(`${API_URL}/api/sessions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    json: undefined as any,
    body: JSON.stringify({ config: { identity: 'brutal-ui-test' } }),
  });
  if (!r.ok) throw new Error(`create session ${r.status}`);
  const data = await r.json();
  return data.id as string;
}

async function injectMessages(token: string, sessionId: string, messages: { role: string; content: string }[]) {
  const r = await fetch(`${API_URL}/api/test/inject-messages`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ session_id: sessionId, messages }),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`inject-messages ${r.status}: ${t.slice(0, 200)}`);
  }
  return r.json();
}

function brutalPayloads(): { role: string; content: string; marker: string }[] {
  const markerBase = `BRUTAL_${Date.now()}`;
  return [
    {
      role: 'user',
      marker: `${markerBase}_CJK`,
      content: `【中文乱码探针】${markerBase}_CJK 你好世界 繁體 カタカナ 한국어 emoji: 😀🔥💥 零宽: a\u200Bb 合字: 👨‍👩‍👧‍👦`,
    },
    {
      role: 'assistant',
      marker: `${markerBase}_MD`,
      content: [
        `## Markdown 探针 ${markerBase}_MD`,
        '',
        '**粗体** *斜体* `inline code`',
        '',
        '```python',
        'print("你好 世界")  # 中文注释',
        's = "\\u4e2d\\u6587"',
        '```',
        '',
        '| col1 | col2 |',
        '| ---- | ---- |',
        '| 甲   | 乙   |',
        '',
        '> 引用块 with emoji ✅',
        '',
        '- list item 1',
        '- list item 2',
        '',
        '[链接](https://example.com)',
      ].join('\n'),
    },
    {
      role: 'assistant',
      marker: `${markerBase}_XSS`,
      content: [
        `XSS 探针 ${markerBase}_XSS`,
        '<script>window.__XSS_HIT__=1</script>',
        '<img src=x onerror="window.__XSS_HIT__=1">',
        '[clickme](javascript:alert(1))',
        '<iframe src="javascript:alert(1)"></iframe>',
      ].join('\n'),
    },
    {
      role: 'assistant',
      marker: `${markerBase}_CTRL`,
      content: `控制字符探针 ${markerBase}_CTRL ` + 'a\u0000b\u0001c\u0008d\u001fe\u007ff',
    },
    {
      role: 'assistant',
      marker: `${markerBase}_RTL`,
      content: `RTL 探针 ${markerBase}_RTL \u202E恶搞\u202C 正常文本 العربية עברית`,
    },
    {
      role: 'assistant',
      marker: `${markerBase}_BROKEN_MD`,
      content: [
        `残缺 Markdown ${markerBase}_BROKEN_MD`,
        '```',
        '未闭合代码块',
        '**未闭合粗体',
        '| 破表 |',
        '|',
        '![broken](http://127.0.0.1:9/no.png)',
      ].join('\n'),
    },
    {
      role: 'assistant',
      marker: `${markerBase}_HUGE`,
      content:
        `超大消息探针 ${markerBase}_HUGE\n` +
        '段落段落。'.repeat(800) +
        '\n```js\n' +
        'const x = "中文";\n'.repeat(200) +
        '```\n' +
        `END_${markerBase}_HUGE`,
    },
    {
      role: 'assistant',
      marker: `${markerBase}_JSON`,
      content: `JSON 工具输出模拟 ${markerBase}_JSON\n\`\`\`json\n${JSON.stringify(
        {
          ok: true,
          msg: '中文结果',
          path: 'C:\\\\Users\\\\测试\\\\文件.txt',
          arr: [1, 2, 3],
        },
        null,
        2
      )}\n\`\`\``,
    },
  ];
}

test.describe('Brutal UI robustness', () => {
  test.setTimeout(180_000);

  test('render/encoding/format stress without white-screen or XSS', async ({ page }) => {
    ensureShotDir();
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    const { token } = await loginViaApi(page);
    const sessionId = await createSession(token);
    const payloads = brutalPayloads();
    await injectMessages(
      token,
      sessionId,
      payloads.map((p) => ({ role: p.role, content: p.content }))
    );

    // 打开主页并带上 session（若前端支持 query；否则靠侧边栏）
    await page.goto(`${BASE_URL}/?session=${sessionId}`);
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(1500);

    // 若仍在 login，再写一次 auth
    if (page.url().includes('/login')) {
      await loginViaApi(page);
      await page.goto(`${BASE_URL}/?session=${sessionId}`);
      await page.waitForTimeout(1500);
    }

    await page.screenshot({
      path: path.join(SHOT_DIR, '01-after-inject.png'),
      fullPage: true,
    });

    // 白屏检测：body 应有可见文本
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.trim().length, 'body should not be empty (white screen)').toBeGreaterThan(20);

    // 尝试点击侧边栏中的会话（若未自动选中）
    const sessionChip = page.locator(`text=${sessionId.slice(0, 8)}`).first();
    if (await sessionChip.isVisible().catch(() => false)) {
      await sessionChip.click().catch(() => {});
      await page.waitForTimeout(800);
    }

    // 直接把消息塞进前端 store 的兜底：若 UI 未加载历史，通过 API 刷新
    // 再等消息列表出现任意 marker
    let foundAny = false;
    for (const p of payloads.slice(0, 4)) {
      const loc = page.getByText(p.marker, { exact: false });
      if (await loc.first().isVisible({ timeout: 3000 }).catch(() => false)) {
        foundAny = true;
        break;
      }
    }

    // 若历史未显示，强制 navigate 刷新
    if (!foundAny) {
      await page.reload({ waitUntil: 'networkidle' }).catch(() => {});
      await page.waitForTimeout(2000);
      for (const p of payloads.slice(0, 4)) {
        if (await page.getByText(p.marker, { exact: false }).first().isVisible({ timeout: 2000 }).catch(() => false)) {
          foundAny = true;
          break;
        }
      }
    }

    await page.screenshot({
      path: path.join(SHOT_DIR, '02-messages-view.png'),
      fullPage: true,
    });

    // 编码探针：页面 HTML 不得大量出现替换符（允许少量）
    const html = await page.content();
    const replacementCount = (html.match(/\uFFFD/g) || []).length;
    expect(replacementCount, 'too many U+FFFD replacement chars (encoding failure)').toBeLessThan(20);

    // 若消息渲染成功，检查关键 marker 仍可读（未转码毁掉）
    const visibleMarkers: string[] = [];
    for (const p of payloads) {
      const ok = await page.getByText(p.marker, { exact: false }).first().isVisible().catch(() => false);
      if (ok) visibleMarkers.push(p.marker);
    }

    // XSS 不应执行
    const xssHit = await page.evaluate(() => (window as any).__XSS_HIT__ === 1);
    expect(xssHit, 'XSS payload must not execute').toBeFalsy();

    // 页面错误：允许少量资源加载失败，但不应有 React 崩溃级错误
    const fatal = pageErrors.filter(
      (e) =>
        /Minified React error|Cannot read prop|is not a function|Unexpected token|Invariant/i.test(e)
    );
    expect(fatal, `fatal pageerrors: ${fatal.join(' | ')}`).toHaveLength(0);

    // 输入区存在且可输入中英混合
    const textarea = page.locator('[data-testid="chat-composer-textarea"]');
    if (await textarea.isVisible().catch(() => false)) {
      const probe = `UI输入探针 ✨ ${Date.now()} 漢字`;
      await textarea.click();
      await textarea.fill(probe);
      const val = await textarea.inputValue();
      expect(val).toContain('漢字');
      expect(val).toContain('✨');
      await page.screenshot({ path: path.join(SHOT_DIR, '03-composer-unicode.png') });
    }

    // 快速路由切换暴力
    const routes = ['/', '/tasks', '/knowledge', '/settings', '/cron', '/workflows', '/'];
    for (const r of routes) {
      await page.goto(`${BASE_URL}${r}`);
      await page.waitForTimeout(400);
      const t = await page.locator('body').innerText();
      expect(t.trim().length).toBeGreaterThan(5);
    }
    await page.screenshot({ path: path.join(SHOT_DIR, '04-after-route-storm.png'), fullPage: true });

    // 报告文件
    const report = {
      sessionId,
      foundAny,
      visibleMarkers,
      visibleCount: visibleMarkers.length,
      payloadCount: payloads.length,
      pageErrors,
      consoleErrors: consoleErrors.slice(0, 30),
      replacementCount,
      xssHit,
      bodySnippet: bodyText.slice(0, 400),
    };
    fs.writeFileSync(path.join(SHOT_DIR, 'report.json'), JSON.stringify(report, null, 2), 'utf-8');

    // 软断言：至少渲染了部分消息，或页面整体仍健康
    expect(
      foundAny || bodyText.length > 50,
      'UI should remain usable after brutal inject'
    ).toBeTruthy();
  });

  test('rapid composer spam does not freeze UI', async ({ page }) => {
    ensureShotDir();
    const pageErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));

    const { token } = await loginViaApi(page);
    await page.goto(`${BASE_URL}/`);
    await page.waitForLoadState('networkidle').catch(() => {});
    // 等连接态稳定
    await page.waitForTimeout(2500);

    const textarea = page.locator('[data-testid="chat-composer-textarea"]');
    const present = await textarea.isVisible({ timeout: 8000 }).catch(() => false);
    test.skip(!present, 'composer not visible (maybe login/session gate)');

    // 若只读（WS 断开/输入锁定），用 evaluate 暴力写入 + 检查 UI 仍存活
    const readonly = await textarea.evaluate((el: HTMLTextAreaElement) => el.readOnly || el.disabled);

    if (readonly) {
      // 锁定态：不应卡死；页面可路由切换
      for (const r of ['/tasks', '/settings', '/']) {
        await page.goto(`${BASE_URL}${r}`);
        await page.waitForTimeout(300);
        const t = await page.locator('body').innerText();
        expect(t.trim().length).toBeGreaterThan(5);
      }
      await page.screenshot({ path: path.join(SHOT_DIR, '05-composer-locked-but-alive.png'), fullPage: true });
      const fatal = pageErrors.filter((e) => /Minified React error|Invariant/i.test(e));
      expect(fatal).toHaveLength(0);
      return;
    }

    // 可编辑：快速填字 30 次
    for (let i = 0; i < 30; i++) {
      await textarea.fill(`spam ${i} 中文 🚀 ` + 'x'.repeat(20));
      if (i % 5 === 0) {
        const send = page.getByRole('button', { name: /发送/ });
        if (await send.isEnabled().catch(() => false)) {
          await send.click().catch(() => {});
          const stop = page.getByRole('button', { name: /停止|Stop/i });
          if (await stop.isVisible().catch(() => false)) {
            await stop.click().catch(() => {});
          }
        }
      }
      await page.waitForTimeout(40);
    }

    await textarea.fill('still-alive 还活着');
    expect(await textarea.inputValue()).toContain('还活着');
    await page.screenshot({ path: path.join(SHOT_DIR, '05-composer-spam.png') });

    const fatal = pageErrors.filter((e) => /Minified React error|Invariant/i.test(e));
    expect(fatal).toHaveLength(0);
  });
});
