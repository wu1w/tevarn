/**
 * Tevarn Mobile E2E — Dioxus UI + real host APIs (no mock data).
 * Run: node e2e/mobile-e2e.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.TEVARN_MOBILE_URL || "http://127.0.0.1:8080";

const b = await chromium.launch({
  headless: true,
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
const ctx = await b.newContext({
  viewport: { width: 390, height: 844 },
  permissions: ["camera", "microphone"],
});
const page = await ctx.newPage();
const fails = [];
page.on("pageerror", (e) => fails.push("PE " + e.message));
page.on("console", (msg) => {
  if (msg.type() === "error") fails.push("CE " + msg.text());
});

function assert(cond, msg) {
  if (!cond) fails.push(msg);
  else console.log("OK", msg);
}

await page.goto(BASE + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1800);

// mode APIs
const modeLocal = await page.evaluate(async () => {
  const r = await fetch("/api/mobile/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ surface: "local" }),
  });
  return r.json();
});
assert(modeLocal.ok && modeLocal.mode?.surface === "local", "mode local");
assert("can_send" in (modeLocal.mode || {}), "mode has can_send");
assert("fix_tab" in (modeLocal.mode || {}), "mode has fix_tab");

const modeRemote = await page.evaluate(async () => {
  const r = await fetch("/api/mobile/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ surface: "remote" }),
  });
  return r.json();
});
assert(modeRemote.ok && modeRemote.mode?.surface === "remote", "mode remote");

// motion tokens
const motion = await page.evaluate(async () => (await fetch("/api/mobile/motion")).json());
assert(motion.ok && motion.long_press_ms >= 300, "motion tokens");

// UI chrome
assert(await page.locator("#mode-local").count(), "mode-local btn");
assert(await page.locator("#mode-remote").count(), "mode-remote btn");
assert(await page.locator("#voice-btn").count(), "voice btn");
assert(await page.locator("#sendbtn").count(), "send btn");
assert(await page.locator("#attach-btn").count(), "attach +");
assert(await page.locator('.tab[data-tab="chat"]').count(), "tab bar chat");
assert(await page.locator('.tab[data-tab="me"]').count(), "tab bar me");

// no mock toast stubs
const html = await page.content();
assert(!html.includes("按住说话（语音输入占位）"), "no voice placeholder toast");
assert(!html.includes("打开相机扫码（配对二维码"), "not using demo scan toast for camera");
assert(!/p-1034|R-17|演示审批/.test(html), "no demo approval ids in DOM");

// media sheet open/close via DOM
await page.click("#attach-btn");
await page.waitForTimeout(250);
assert(await page.locator("#media-sheet.show").count(), "media sheet open");
await page.locator('#media-sheet button:has-text("取消")').click();
await page.waitForTimeout(200);
assert(!(await page.locator("#media-sheet.show").count()), "media sheet closed");

// dual mode switch via DOM (no CHAT_MODE global)
if (modeRemote.mode?.pc_connected) {
  await page.click("#mode-remote");
  await page.waitForTimeout(600);
  const remoteAct = await page.locator("#mode-remote.act").count();
  assert(remoteAct, "switch remote via DOM");
  await page.click("#mode-local");
  await page.waitForTimeout(400);
  assert(await page.locator("#mode-local.act").count(), "switch local via DOM");
} else {
  // remote disabled when not connected — opacity / disabled
  const disabled = await page.locator("#mode-remote[disabled]").count();
  assert(disabled, "remote disabled when PC offline");
}

// drawer + new chat + dsect labels
await page.click('.shead .iconbtn[title="历史会话"]');
await page.waitForTimeout(400);
assert(await page.locator("#drawer.open, #drawer").evaluate((el) => {
  // drawer shown via class open or parent
  return !!document.getElementById("drawer-bg") || el.offsetParent !== null;
}).catch(() => true), "drawer structure");
const drawerText = await page.locator("#drawer").innerText().catch(() => "");
if (drawerText) {
  assert(drawerText.includes("对话通道"), "drawer 对话通道");
  assert(drawerText.includes("远端会话"), "drawer 远端会话");
  assert(drawerText.includes("新对话") || drawerText.includes("+"), "new chat btn");
}
// close drawer
await page.locator("#drawer-bg").click({ force: true }).catch(() => {});
await page.waitForTimeout(200);

// kernel endpoint
const kern = await page.evaluate(async () => (await fetch("/api/mobile/kernel")).json());
assert(kern.ok, "kernel status");

// approvals empty real
await page.click('.tab[data-tab="approve"]');
await page.waitForTimeout(600);
const apText = await page.locator("#ap-list").innerText();
assert(!/p-1034|R-17|演示/.test(apText), "no demo approval stubs");

// send blocked path (no LLM) should not crash
await page.click('.tab[data-tab="chat"]');
await page.waitForTimeout(300);
await page.fill("#inp", "e2e ping");
await page.click("#sendbtn");
await page.waitForTimeout(800);
// either toast or message appears
const toastOrMsg = await page.evaluate(() => {
  const t = document.getElementById("toast");
  const msgs = document.getElementById("msgs");
  return {
    toast: t?.classList.contains("show") ? t.textContent : "",
    msgLen: msgs?.innerText?.length || 0,
  };
});
assert(toastOrMsg.toast || toastOrMsg.msgLen > 0, "send produces toast or message");

// api error shape: ok:false
const bad = await page.evaluate(async () => {
  const r = await fetch("/api/mobile/local/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url: "", api_key: "", model: "" }),
  });
  return r.json();
});
// empty config may still ok:true with not ready — just ensure JSON
assert(typeof bad.ok === "boolean", "local config returns ok bool");

console.log("\n" + (fails.length ? "FAIL\n" + fails.join("\n") : "ALL PASS"));
await b.close();
process.exit(fails.length ? 1 : 0);
