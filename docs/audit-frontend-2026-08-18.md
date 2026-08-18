# Frontend audit — Electron / Next.js (read-only)

**Audited SHA:** `cfccbbb9ed3a8a489097aa3a6e42ee0049a66e43` (`cfccbbb` on `main`)  
**Date:** 2026-08-18  
**Scope:** Desktop Electron shell + `frontend/` Next.js UI. No product or CI files were changed.  
**Method:** Independent code review of preload/IPC, chat stop/end, session delete + drafts, markdown/HTML rendering, knowledge paging, settings props, and GitHub Actions `frontend-ci` logs. Root cause was not assumed.

Neighbors on `main` that matter for this report:

| SHA | Title | frontend-ci |
|---|---|---|
| `4133b5f` | Unify Rust court authority and make chat stop/end UX match mainstream agents | **green** |
| `698ebe5` | …land chat inspector composer | **red** |
| `cfccbbb` | Fix audit P1/P2 chat runtime gaps: session delete, draft restore, tool stop… | **red** |

---

## frontend-ci: why latest main is red

**Gate that fails:** `frontend` job `ESLint (gate)` (`npm run lint` in `frontend/`). Typecheck and Next build never run.

**Latest failed run (this SHA):**  
https://github.com/wu1w/tevarn/actions/runs/32089209073  
(`cfccbbb`, 2026-08-18T01:43Z, ~1m2s)

**Previous failed run (first red after a green main):**  
https://github.com/wu1w/tevarn/actions/runs/32037918795  
(`698ebe5`, 2026-08-17T14:07Z)

**Last green `frontend-ci` on main:**  
https://github.com/wu1w/tevarn/actions/runs/32004377076  
(`4133b5f`, 2026-08-17T07:06Z)

### Exact blocking error

`eslint-plugin-react-hooks` **7.1.1** (pulled by `eslint-config-next` 16.2.10) reports **1 error, 133 warnings**. Warnings do not fail the job. The single error does:

```
frontend/hooks/useColResize.ts:23
  error  Cannot access refs during render / Cannot update ref during render
  react-hooks/refs

  const widthRef = useRef(width);
  widthRef.current = width;
```

`useColResize.ts` was **added in `698ebe5`** (composer / inspector column resize). `cfccbbb` did not touch this file. That is why `4133b5f` was green and the next two main pushes are red.

`frontend/eslint.config.mjs` downgrades several React Compiler rules (`set-state-in-effect`, `static-components`, `immutability`, `purity`) to **warn**. It does **not** downgrade `react-hooks/refs`, so the new hook is a hard gate.

### Suggested fix (do not apply in this PR)

In `frontend/hooks/useColResize.ts`, stop writing the ref during render. Either:

1. Sync in an effect: `useEffect(() => { widthRef.current = width; }, [width]);` (one extra frame of staleness is fine — `onStart` reads the ref only on pointer down), or
2. Read `width` from a state updater / pass it into `onStart` so the ref is unnecessary.

Do **not** silence `react-hooks/refs` globally. The 133 warnings are noise, not the outage.

---

## Findings

### Critical

None observed that are exploitable as a remote, unauthenticated RCE from the renderer **given** `contextIsolation: true`, `nodeIntegration: false`, and `sandbox: true`. Remaining IPC and HTML-rendering issues are High/Medium.

---

### High

#### H1 — `sync_response` idle path drops the in-flight partial (stop/end race)

**Files:** `frontend/app/chat/page.tsx` (`handleSyncResponse` ~1044–1053 vs `keepPartialAssistantOnIdle` ~89–126 and status `idle` ~813–853)

`status: idle` and the local Stop 8s fallback call `keepPartialAssistantOnIdle`, which reloads history and, if the leftover is not yet in the store, inserts a local assistant bubble (ChatGPT/Cursor-style).

`handleSyncResponse` when `agent_running` is false does **not**. It clears `streamingContent` / `liveToolCalls` and only `loadMessages`. If the WS sync wins the race before the backend has persisted the assistant row, the user sees an empty stop: no partial, no in-flight tools.

This is the most likely “I hit Stop and the reply vanished” path after `4133b5f` / `cfccbbb`. Those commits hardened the **status** path, not the **sync** path.

**Suggested fix:** On sync-idle, reuse `keepPartialAssistantOnIdle(sid, leftover, loadMessages, addMessage)` before clearing refs. Treat sync as another idle signal, not a hard wipe.

#### H2 — File-preview markdown skips the chat URL allow-list

**Files:** `frontend/components/chat/FilePreviewHost.tsx` (~480–483), vs `frontend/components/chat/MarkdownContent.tsx` (`safeUrlTransform`, ~10–18, ~123–128)

Chat bubbles use `react-markdown` **without** `rehype-raw`, plus `urlTransform={safeUrlTransform}` (only `http(s)`, `mailto:`, `data:image/`, relative `./#?`). e2e `frontend/e2e/brutal-ui.spec.ts` probes `javascript:` and `<script>` on chat markdown.

The **same** `ReactMarkdown` in `FilePreviewHost` for `kind: markdown` has **no** `urlTransform`. Default `react-markdown` v10 is conservative, but this is the one place untrusted tool/file markdown is rendered with a weaker contract than the chat path. Docx preview uses `DOMPurify` + click-intercept; markdown preview does not.

**Suggested fix:** Pass the same `safeUrlTransform` (or a shared helper) into every `ReactMarkdown` site. Keep `rehype-raw` off.

#### H3 — `openExternal` / window-open allow any `http:` or `https:` URL

**Files:** `electron/main.ts` (`isAllowedExternalUrl` ~78–85, `open-external` ~2173–2178, `setWindowOpenHandler` ~2144–2148); callers `frontend/components/settings/ModelSettingsPanel.tsx` (~633–636, ~748–751), `frontend/components/chat/FilePreviewHost.tsx` (~469–474)

Protocol check only. A compromised renderer, or a crafted OAuth/`authorization_url` / docx `href`, can open any remote page in the system browser (phishing, token-fixup pages). `will-navigate` / `will-redirect` correctly keep the **BrowserWindow** on `http://127.0.0.1:3000`, but `shell.openExternal` is the bypass.

**Suggested fix:** Allow-list hosts for OAuth (known IdP domains) and require `https:` except loopback. Reject userinfo, credentials, and non-default ports except 80/443. Do not pass tool-output hrefs to `openExternal` without the same allow-list.

#### H4 — Several IPC handlers skip `assertTrustedIpc`

**Files:** `electron/preload.ts` (exposed API), `electron/main.ts` (~2168–2238)

Handlers that **do** check `event.senderFrame` origin (`http://127.0.0.1:3000`): `open-external`, `open-path`, `get-dropped-files`, `grant-desktop-permission`, `select-directory`, `open-tevarn-code`, `install-update`.

Handlers that **do not**:

| Channel | Risk if a non-trusted frame can invoke |
|---|---|
| `get-user-data-path` | Leak `userData` path |
| `get-backend-url` / `get-ws-url` + sync variants | Leak bind/port |
| `show-notification` | Spoofed OS notifications |
| `minimize-window` / `maximize-window` / `close-window` | UX / close-to-tray |
| `get-platform` / `get-app-version` | Fingerprinting (low) |

Sandbox + `contextIsolation` make a random iframe less likely to get `electronAPI`, but the fail page loaded via `data:text/html` (`did-fail-load`, ~2079–2088) is **not** a trusted origin. Any future preload exposure or isolated world leak would hit the unchecked channels first.

**Suggested fix:** Call `assertTrustedIpc` on every `ipcMain.handle` / `ipcMain.on`. For the data: fail page, do not need window controls.

---

### Medium

#### M1 — Session delete does not drop composer drafts or stream cache

**Files:** `frontend/components/chat/ContactSessionPicker.tsx` (~172–229), `frontend/hooks/useSession.ts` (`discardEmptySession` ~102–191), `frontend/components/chat/MessageInput.tsx` (~146–169), `frontend/stores/streamSessionStore.ts`, `frontend/stores/sessionStore.ts` persist (~531–537)

What **is** cleaned on explicit delete (`deleteSession(id, true)`): titles, stars, `lastSessionByContact`, picker rows, `tevarn:session-invalid` if it was the current session.

What **is not**:

- `localStorage['tevarn-chat-draft:' + sessionId]` — orphaned forever; if the same UUID were ever reused (it should not be) the draft would resurrect.
- `streamSessionStore.bySession[id]` — `clear()` exists but delete paths only `markIdle` / leave the entry. A later switch to a stale id (persist `currentSession` until 404) can show a ghost “Resuming…” from cache (`chat/page.tsx` ~359–364) before `getSession` 404s.
- Persisted `currentSession` in `tevarn-session` — reload after delete-current-with-no-remaining is OK (`setCurrentSession(null)`). Delete-while-another-tab still has the id: 404 handler (~397–413) and `session-invalid` (~499–508) do clear. Auto-discard (~311–323) waits 1.2s and re-checks the server; that is the right shape.

**UI vs backend desync (yes, possible):**

1. Force-delete succeeds; other windows keep `currentSession` until the next `getSession` / `session-invalid`. Messages can 404; composer still looks live.
2. Auto-discard fails (409 active / network) and is swallowed (`useSession.ts` ~188–190). Sidebar may still list a session the UI thought was empty, or the opposite after a later GC.
3. Draft restore is **per remount** (`MessageInput` `key={`${sessionId}:${editingContent}`}` in `chat/page.tsx` ~2232–2233). That remount is what makes per-session drafts work. Switching **without** remount would keep the previous textarea (the empty-deps restore effect would not re-run). Do not remove that `key`.
4. Debounced draft write (500ms) is cancelled on unmount — last keystrokes in a session can be lost on a fast switch. Opposite of (3): isolation is correct, durability is not.

**Suggested fix:** On any successful `deleteSession`, `localStorage.removeItem('tevarn-chat-draft:'+id)`, `streamSessionApi().clear(id)`, and drop the id from persisted `currentSession` / titles in every listener of `tevarn:session-invalid`. Flush draft on `MessageInput` unmount.

#### M2 — Stop leaves in-flight tools looking “done”, then deletes them

**Files:** `frontend/app/chat/page.tsx` (~833–841, ~1675–1702), `frontend/components/chat/ToolCallPanel.tsx` (`resolveToolCallStatus` ~25–36), `frontend/components/chat/ComposerContextStrip.tsx`, `frontend/components/chat/ActivityPanel.tsx`

On idle/stop, running tools are mapped to `completed` (not `failed` / cancelled) and then `setLiveToolCalls([])` on the next tick. `resolveToolCallStatus(..., pending=false)` also forces `completed` when there is no result.

Mainstream agents (ChatGPT, Cursor, Claude Desktop) keep the last tool row on the stopped bubble as **cancelled / interrupted**, and keep the partial text. Tevarn now keeps the **text** (status path) but the tool strip **vanishes**, and any tool that never landed in history looks successful for one frame.

The 8s Stop fallback (`handleStopStreaming`) marks the session locally idle and ignores later deltas (`locallyStoppedRef`). If the WS stop never reached the backend, the agent can still run while the UI is idle (composer unlocked). Steer/queue then fights the live run.

**Suggested fix:** Persist live tools onto the last assistant message (or a local “stopped” bubble) with status `cancelled`. Only clear the live strip after history contains those ids. If `sendStop()` is false or idle does not arrive, keep `isStopping` and do not unlock send except steer-to-stop.

#### M3 — Knowledge list still silently caps (now 500, no pager)

**Files:** `frontend/lib/api.ts` (`getDocuments` ~947–958), `frontend/components/knowledge/KnowledgeCenter.tsx` (`load` ~620–626)

`2a8348f` raised the client default from 100 → 500 and documented “page via `{ limit, offset }` when UI grows a load more.” `KnowledgeCenter` still calls `getDocuments()` with no offset and no “showing 500 of N”. Users with a larger corpus see a **full** list that is not full.

**Suggested fix:** Return `{ items, total }` from the API (or a `Content-Range` / `X-Total-Count`). Render “Load more” / virtualize. Do not pretend 500 is complete.

#### M4 — Composer overflow was the `698ebe5` UX bug; portal is the fix, clip remains on the column

**Files:** `frontend/app/globals.css` (`.chat-main-column` / `.chat-composer` `overflow: hidden`, ~763–769, ~1163–1170), `frontend/components/chat/ComposerMenuPortal.tsx`, `frontend/components/chat/MessageInput.tsx` (slash / tools / more menus)

`698ebe5` is the commit that “keep[s] composer menus and the inspector out of overflow clipping.” `ComposerMenuPortal` `createPortal`s to `document.body` with `position: fixed`. That matches ChatGPT/Cursor. Residual: if `anchorRef.current` is null on first layout, the menu does not open (`style` stays null). Tools/more menus are portaled; confirm any remaining popover (model picker, mention list) still clips.

**Suggested fix:** Audit every composer popover for `ComposerMenuPortal` (or equivalent). Keep `.chat-main-column { overflow: hidden }` — that is what forces the portal.

#### M5 — Mermaid SVG is `dangerouslySetInnerHTML` (strict mode, still a sink)

**Files:** `frontend/components/chat/MarkdownContent.tsx` (`MermaidBlock` ~220–365)

`mermaid.initialize({ securityLevel: 'strict' })` and parse-before-render are the right controls. The output SVG is still assigned via `dangerouslySetInnerHTML`. A Mermaid generator bug or a future `securityLevel` regression is a stored-XSS sink on **assistant** content (tool-influenced).

**Suggested fix:** Keep `strict`. Consider rendering into a closed shadow root or sanitizing the SVG with DOMPurify’s SVG profile. Do not switch to `securityLevel: 'loose'`.

#### M6 — `FileDownloadLink` will treat almost any dotted relative href as a workspace file

**Files:** `frontend/components/chat/FileDownloadLink.tsx` (`isWorkspaceFileLink` ~8–18, `extractRelPath` ~22–34)

Any non-`http(s)`/`mailto`/`#` href with a 1–10 char extension becomes a download click to `GET /api/files/download?path=...`. Backend `backend/api/routes/files.py` `_resolve_path` + `_check_access` + `resolve()` is the real sandbox (relative `..` is contained). Frontend does not reject `..` itself. If `FILE_BROWSER_LOCAL=1`, `mode=local` is a different story — the UI always uses default sandbox mode today.

**Suggested fix:** Reject `..` and absolute paths in `extractRelPath`. Only treat `workspace/` / `sandbox:` / `./` prefixes as file links.

#### M7 — Preload hard-codes WS to `ws://127.0.0.1:3000/api`

**Files:** `electron/preload.ts` ~16–30, ~91–103

REST is correctly forced to same-origin `/api`. WS ignores the main-process URL and always writes `127.0.0.1:3000`. Fine for the default `FRONTEND_PORT = 3000` (`electron/main.ts`). A port override or IPv6-only bind would leave the renderer “connecting” forever. `__TEVARN_WS_URL_DIRECT__` is debug-only.

**Suggested fix:** Derive WS from `location.host` (same-origin upgrade) instead of a literal port.

#### M8 — IPC surface the renderer can invoke (inventory)

Exposed on `window.electronAPI` (`electron/preload.ts`):

- Info: `getPlatform`, `getUserDataPath`, `getAppVersion`, `getBackendUrl`, `getWsUrl`
- Window: `minimizeWindow`, `maximizeWindow`, `closeWindow` (close hides to tray unless quitting)
- Desktop: `showNotification`, `grantDesktopPermission` (native box + POST), `getDroppedFiles`, `selectDirectory`
- Spawn / FS: `openTevarnCode` (external terminal; path validated, `shell: false` on Unix; Windows writes a temp `.bat` + `cmd /k`), `openPath` (absolute + realpath + dangerous-ext deny), `openExternal`
- Update: `installUpdate` (`quitAndInstall`)
- Events (no `removeListener`): `onUpdateAvailable`, `onUpdateDownloadProgress`, `onUpdateDownloaded`

Also: sync `ipcRenderer.sendSync` for backend/WS URLs at preload time.

`frontend/electron/preload.ts` and `frontend/electron/main.ts` are **stubs**. Canonical process is repo-root `electron/`. Good — a wrong entry throws.

No `webSecurity: false`, no `allowRunningInsecureContent`. `nodeIntegration` is false. Navigation is origin-locked. That baseline is solid (see “What looks solid”).

---

### Low

#### L1 — `ModelSettingsPanel` props are consistent on this SHA

**Files:** `frontend/components/settings/ModelSettingsPanel.tsx` (`ModelSettingsPanelProps` ~65–73), `frontend/app/settings/page.tsx` ~892

`23daba7` (“ModelSettingsPanel signature + ConfirmDialog i18n”) is in history. Current call site is `settings={settings} onSettingsRefetch={async () => { await refetch(); }}`. No leftover `onSave` / arity mismatch on `main`. Residual: panel still `useEffect`-hydrates catalog into local state (`set-state-in-effect` warnings in CI). Functional, not a type error.

#### L2 — ConfirmDialog i18n is wired; some chat chrome is still hard-coded Chinese

**Files:** `frontend/components/desktop/ConfirmDialog.tsx`; `frontend/components/chat/MarkdownContent.tsx` (Mermaid “生成中…”, “图表”, “源码”)

Confirm dialog uses `t(...)`. Mermaid chrome and some stop fallbacks (`'Stopping…'`) are zh-only or English literals.

#### L3 — Session persist can briefly resurrect a deleted id

**Files:** `frontend/stores/sessionStore.ts` persist `currentSession`; `frontend/app/chat/page.tsx` ~397–413

On reload, persisted `currentSession` is shown until `getSession` 404. The 404 path is correct. Flash + one extra request only.

#### L4 — `show-notification` title/body are unsanitized

OS notifications are not HTML, but a renderer XSS could spam or spoof “Tevarn” toasts. Pair with H4.

#### L5 — `grant-desktop-permission` dialog copy is Chinese-only

**File:** `electron/main.ts` ~2292–2300. Fine for the current locale default; not a security bug.

#### L6 — Knowledge / settings `set-state-in-effect` debt

CI log is full of `react-hooks/set-state-in-effect` warnings (`RemoteConnectPanel`, `SecuritySettingsPanel`, `SkillStorePanel`, `NodePalette`, `OpenProjectModal`, `useColResize` stored-width hydrate). These are **warnings** today. If Next/eslint-config-next promotes them, `frontend-ci` will go red again the same way `react-hooks/refs` just did.

#### L7 — `__tevarnSmoke` on `window` in non-production

**File:** `frontend/app/chat/page.tsx` ~250–273. Dev/e2e only. Do not ship with `NODE_ENV` mis-set.

---

## Chat stop / end vs mainstream agents (summary)

| Behavior | ChatGPT / Cursor / Claude Desktop | Tevarn at `cfccbbb` |
|---|---|---|
| Stop keeps partial text | Yes | Yes on `status: idle` and 8s fallback; **no** on `sync_response` idle (H1) |
| In-flight tools stay visible, marked cancelled | Yes | Marked completed, then removed (M2) |
| Composer usable for steer / queue while running | Yes | Yes (`MessageInput` steer + queue) |
| Stop does not unlock a still-running backend | Mostly | 8s local idle + `locallyStoppedRef` can desync (M2) |
| Composer menus not clipped | Yes (portals) | Fixed in `698ebe5` via `ComposerMenuPortal` (M4) |
| Per-session draft | Yes | Yes **if** `MessageInput` remounts on `sessionId` (current `key=`); delete does not purge keys (M1) |

`4133b5f` + `cfccbbb` are the right direction. The remaining holes are the **second** idle signal (sync) and **tool row lifetime**, not the Stop button itself.

---

## State-management races (short list)

1. **Sync idle vs status idle** — H1.  
2. **Stop 8s vs backend still running** — M2.  
3. **Session switch saves stream cache, then 1.2s delayed auto-delete** — protected by `getActiveSessionIds` + content check; 409 swallowed.  
4. **Persist `currentSession` vs 404** — L3.  
5. **Draft debounce vs remount** — last 500ms can drop (M1).  
6. **`handleStreamDelta` ignored while `isStoppingSid` / `locallyStoppedRef`** — correct for stop, wrong if stop never arrived.  
7. **Knowledge `getDocuments()` one-shot 500** — M3.  
8. **Column width ref written during render** — CI red, not a user-facing race.

---

## What looks solid

- **Electron process isolation:** `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` (`electron/main.ts` ~2045–2051). Canonical preload uses `contextBridge` only; renderer cannot `require('electron')`.
- **Navigation lock:** `will-navigate` / `will-redirect` require `http://127.0.0.1:3000`. `window.open` is always `deny` after optional `openExternal`.
- **Dangerous IPC that *is* checked:** `open-path` (absolute, realpath, executable-ext deny), `get-dropped-files` (absolute + realpath), `open-tevarn-code` (absolute dir, metachar deny, `shell: false` on Unix), `grant-desktop-permission` (operation allow-list + native dialog + token).
- **Stub `frontend/electron/*`:** cannot be launched by mistake.
- **Chat markdown XSS baseline:** no `rehype-raw`; `urlTransform` strips `javascript:` / `file:`; e2e brutal suite includes an XSS probe. Error bodies render as text, not markdown.
- **HTML / docx preview:** `DOMPurify` + iframe `sandbox=""` (no scripts, no same-origin). Docx links must be `https?:` before `openExternal`.
- **Mermaid:** `securityLevel: 'strict'`, parse-first, no render while `streaming`.
- **Session auto-delete:** contact sessions, 90s recent activity, optimistic user bubbles, active-id probe, 60s young-session grace, and “active fetch failed → do not delete” are conservative. This is why empty-session GC is unlikely to murder a live IM thread.
- **Draft isolation:** `tevarn-chat-draft:${sessionId}` plus remount `key` — the `cfccbbb` / earlier audit-fix comment is accurate **for switch**, as long as that `key` stays.
- **Stream cache:** per-session `streamSessionStore` + BroadcastChannel peer occupancy + kicked-by-peer banner is closer to multi-window agents than a single global `isStreaming`.
- **Settings panel:** `ModelSettingsPanel` signature matches the only call site; independent preset/catalog `Promise.allSettled` avoids wiping providers on one failure.
- **CI config hygiene:** lint/typecheck/build are all gates; the red is a **new hook vs hooks v7**, not a secret infra flake. Last green SHA is known (`4133b5f`).

---

## Suggested fix priority

1. **Unblock `frontend-ci`:** move `widthRef.current = width` out of render (`useColResize.ts`).  
2. **H1:** `keepPartialAssistantOnIdle` on sync-idle.  
3. **H2 / H3:** shared URL allow-list for markdown + `openExternal`.  
4. **H4:** `assertTrustedIpc` on the remaining channels.  
5. **M1 / M2:** delete-path cleanup + cancelled tool rows.  
6. **M3:** knowledge pager.

---

## Out of scope / not claimed

- Backend stop/idle persistence (only the UI contract was reviewed).  
- Kernel / court / capability token path (threat model is in `docs/THREAT_MODEL.md`).  
- Mobile Flutter UI.  
- No product, ESLint, or workflow file was modified in the PR that carries this document.
