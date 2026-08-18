# Tevarn Python / FastAPI backend audit

- **Date:** 2026-08-18
- **Repo:** `wu1w/tevarn`
- **Audited SHA (origin/main):** `cfccbbb9ed3a8a489097aa3a6e42ee0049a66e43`
- **Commit message:** `Fix audit P1/P2 chat runtime gaps: session delete, draft restore, tool stop, and static stale checks.`
- **Method:** Read-only review of current `main` plus GitHub Actions logs. No product, test, or CI files were changed for this report.
- **Threat model used:** Local-first, single-user desktop (Electron + FastAPI + Rust kernel), as stated in `docs/THREAT_MODEL.md` and `backend/core/config.py` (`single_user_mode=True`, bind `127.0.0.1`). Multi-user / LAN / VPS findings are labeled as such and are not scored as default-desktop Critical.

This report does **not** assume a single root cause for “backend-ci is red.” The job has failed for **three different reasons** on recent `main` commits (Ruff, version sync, pytest assertion drift). Those are documented separately below.

---

## Executive summary

Recent commits `698ebe5` and `cfccbbb` did fix the **primary chat-agent** regressions they named (Windows `python` tool on SelectorEventLoop, LLM integer-string schema, `configure_tevarn` `topic=status`, prefetch cancel, session-delete UX, draft keep-on-failed-send, `backend/static` stale gate). Those paths look real in code and have targeted tests.

They did **not** close the full surfaces:

- Tool **stop** still lets prefetched readonly tools commit, and serial tools are not cancelled mid-flight.
- Session **delete** still auto-recreates the same UUID after tombstone TTL, and does not clear control-inbox spill.
- Static stale detection only treats a directory named `static` as stale when `version.json` is missing.
- `backend-ci` on current `main` never reaches pytest: **Ruff F541** fails first.
- The last time pytest actually ran on `main` (`81581cc`, 2026-08-16) it failed ~50 tests, mostly assertion/i18n and kernel/workforce semantic drift — not import/collection failure.

No default-desktop **Critical** (unauthenticated remote takeover, or a path-traversal that reads `~/.tevarn/secrets.json` from the knowledge indexer) was confirmed. The highest-severity items are **High**: incomplete stop, CI permanently skipping tests, multi-user IDOR on traces, and a few event-loop / outbound-HTTP holes on hot paths.

---

## backend-ci: actual failure reasons

Workflow: `.github/workflows/backend-ci.yml`. Job `test` on `ubuntu-latest`, Python 3.12. Gates, in order: Ruff → mypy → `scripts/sync_version.py --check` → pytest collect → security suite → full `backend/tests`.

**This is not one bug.** Recent `main` failures:

| SHA | When | Duration | Failed step | Concrete reason |
|-----|------|----------|-------------|-----------------|
| `cfccbbb` (HEAD) | 2026-08-18 | 37s | **Ruff lint (gate)** | `F541` unused f-string prefix |
| `698ebe5` | 2026-08-17 | 35s | **Ruff lint (gate)** | same `F541` |
| `4133b5f` | 2026-08-17 | 52s | **Ruff lint (gate)** | same `F541` (**introduced here**) |
| `81581cc` | 2026-08-16 | 2m15s | **Run tests (backend/tests)** | ~50 pytest failures (see below) |
| `2a8348f` / `23daba7` / `7fb4a38` | 2026-08-16 | ~54s | **Version sync check** | `pyproject.toml` `0.4.2` ≠ `VERSION` `0.5.0-alpha` |
| `0679671` | 2026-08-16 | 2m37s | **Run tests (backend/tests)** | same pytest family as `81581cc` |

Latest run: https://github.com/wu1w/tevarn/actions/runs/32089209028  
Last pytest-on-main run: https://github.com/wu1w/tevarn/actions/runs/31954142981

### Current HEAD blocker (reproduced from logs)

Ruff `F541` in `backend/agent/loop.py`, introduced by `4133b5f` (`Unify Rust court authority…`):

```text
backend/agent/loop.py:393:17  f"正在等待继续…"
backend/agent/loop.py:3477:25 f"步数已用完，正在给出答复…"
```

Both are f-strings with no placeholders. `ruff check backend` is a hard gate, so **mypy, version sync, collection, security suite, and pytest are skipped** on the last three `main` pushes.

Suggested fix (product PR, not this one): drop the `f` prefix, or add `{reason}` / `{used}` if interpolation was intended. Two-character change; `--fix` would do it.

Local `scripts/sync_version.py --check` on this SHA: **OK, `0.4.3`**. Version drift from the 0.5.0-alpha UX commits is already resolved on HEAD.

### Last real pytest failure (`81581cc`) — themes, not one root cause

CI env on that run was the documented Python-kernel isolation (`TEVARN_AGENT_KERNEL_BACKEND=python`, `TEVARN_AGENT_DISPATCHER_ENABLED=false`, `TEVARN_TEST_MODE=1`). Several failures are **test/product drift**, not “CI cannot import the app”:

1. **i18n / controller copy** — tests still assert Chinese tokens that the implementation now emits in English:
   - `test_sanitize_tool_error_has_next_step` expects `'下一步'` but gets `'Next: glob/list to confirm the path…'`
   - `test_force_final_message_wording` / `test_force_final_messages` expect `'工具轮'` / `'轮次'` but get `'[Controller] Write final answer only; tools blocked this round.'`
   - Same pattern: `'硬顶'`, `'禁止'`, `'熔断'`, `'NEXT:'` vs current English / mixed strings
2. **Workforce / inbox / claim** — `test_workforce_06`, `test_inbox_dead_letter`, `test_audit_bugfix_loop`, `test_stop_concurrency_report`, `test_product_spine_046`: `assert None`, `0 == 1`, missing `.instruction`. These tests construct `WorkforceDispatcher` themselves, so the CI `DISPATCHER_ENABLED=false` flag is **not** the obvious cause; claim/dead-letter/budget semantics likely changed under the Python kernel.
3. **Budget semantics** — some tests expect `BudgetExceededError` and do not get it; `test_soft_renew_*` get `BudgetExceededError` when they expect soft renew (`ceo_floor` vs `ceo`).
4. **Tool policy snapshots** — `test_tool_policy.py` still expects old allow-lists (`manage_cron` absent from core, `file_read` in an explicit list, knowledge tier `rich` not `minimal`).
5. **Packages** — `code-review-lite` not found (`test_package_market`, `test_packages_system_layers`).
6. **Misc** — `test_phase5_zero_deps` `NameError: product_version`; `test_usage_normalize` `3 == 2`; MCP risk `low` vs `medium`; `test_next_round_10` `No module named 'scripts.tevarn_sdk_pack'`.
7. **`test_kernel_package_has_no_fastapi_or_api_imports`** failed on `81581cc`. On **this** SHA, `backend/kernel/*.py` has no static `fastapi` / `backend.api` imports. That one may already be fixed; CI has not been able to confirm because Ruff dies first.

**CI config note (do not change in this PR):** `.github/workflows/backend-ci.yml` and `backend/tests/conftest.py` both force `TEVARN_AGENT_DISPATCHER_ENABLED=false` and Python kernel so the job does not attach to a host. That is intentional isolation (see comments + `kernel-ci.yml`). It does mean rust-host / live-dispatcher behavior is not what `backend-ci` proves. The current red on HEAD is still Ruff, not that isolation.

---

## Findings

Severity is for **default desktop** unless marked **(multi-user)** or **(Windows)**.

### Critical

None confirmed for the default single-user, loopback-bound desktop product.

If `TEVARN_SINGLE_USER_MODE=false` and the API is reachable by more than one principal, treat **H-AUTH-1** (trace IDOR) and **H-AUTH-2** (cluster WS is authenticated-but-not-authorized) as release blockers.

---

### High

#### H-STOP-1 — Prefetched tools ignore user stop in the serial loop

- **Where:** `backend/agent/phases/tool_round.py` ~794–796
- **Evidence:** After `_prefetch_readonly_calls`, the serial loop skips only tools that are **not** already in `prefetched`:

```python
if getattr(loop, "_should_stop", False) and ... and getattr(tc, "id", None) not in prefetched:
```

- `cfccbbb` added a prefetch watcher and cancel-on-`CancelledError`, which is real. The serial commit path still emits start/end, writes task rows, and appends tool messages for cached readonly results after the user stopped.
- **Suggested fix:** Treat `_should_stop` as authoritative for **all** remaining tool_calls, including prefetched ids. Do not emit `phase="start"` after stop. Add a test that stop is set during prefetch and the serial loop does not commit cached results.

#### H-STOP-2 — Serial (non-prefetch) tools are not cancelled mid-execution

- **Where:** `backend/agent/phases/tool_round.py` ~86–101, ~930–960
- **Evidence:** `_await_with_timeout_cleanup` only cancels on **timeout** (`asyncio.wait_for` + `shield`). Default `agent_tool_timeout_seconds` is 180s. There is a between-tool `_should_stop` check, but `command` / `python` / MCP can run to completion after WS `{type:"stop"}`.
- Frontend also force-idles after 8s (`frontend/app/chat/page.tsx`) while the backend may still be working — UX desync, not a substitute for cancellation.
- **Suggested fix:** Same stop-watcher pattern as prefetch around `_execute_registered_tool`; pass a cancellation token into `safe_subprocess` / MCP. Retry `sendStop()` before the 8s local idle.

#### H-CI-1 — `backend-ci` Ruff gate hides all later failures

- **Where:** `backend/agent/loop.py:393`, `:3477`; `.github/workflows/backend-ci.yml` Ruff step
- **Evidence:** GitHub run `32089209028` (HEAD) and the two previous `main` pushes. `git log -S '正在等待继续'` points at `4133b5f`.
- **Suggested fix:** Remove unused `f` prefixes. Then re-run the full job — expect the `81581cc` pytest family to reappear until those assertions / kernel semantics are updated. Do not treat a green Ruff as a green backend.

#### H-AUTH-1 — Trace list and delete are IDOR **(multi-user)**

- **Where:** `backend/api/routes/traces.py:50–73` (list), `:146–157` (delete)
- **Evidence:** `list_traces_by_session` never loads the session or calls `assert_session_owner`. `delete_trace` deletes by id after auth only. Contrast `get_latest_trace` (82–88) and `get_trace` (122–127), which **do** check ownership.
- **Impact:** Any authenticated user can list another user’s traces (tool counts, input summaries) and delete them. Harmless on default single-user; a real isolation bug if multi-user is enabled.
- **Suggested fix:** Load session → `assert_session_owner` on list and delete, same as `get_trace`.

#### H-AUTH-2 — Cluster WebSocket: any valid JWT can watch any `task_id` **(multi-user)**

- **Where:** `backend/api/routes/cluster.py:750–766`
- **Evidence:** Authorization is “JWT decodes and has `sub`” or loopback single-user. No check that `sub` owns `task_id`.
- **Suggested fix:** Persist `user_id` on cluster runs; compare on WS connect.

#### H-WIN-1 — Workflow engine still uses raw `asyncio.create_subprocess_exec` **(Windows)**

- **Where:** `backend/services/workflow_engine.py:920–925`
- **Evidence:** `698ebe5` correctly routed the **chat** `python` / `command` tools through `backend/core/safe_subprocess.py` (`create_process_exec` + SelectorEventLoop → threaded Popen). Workflow nodes were not migrated. On Windows SelectorEventLoop this raises `NotImplementedError` (the original “dead python tool” failure mode).
- Same leftover pattern: `backend/tools/builtins/capability_tools.py` (`GithubTool._run`, mermaid-cli) ~417, ~703.
- **Suggested fix:** Route all argv spawns through `create_process_exec`. Optionally fail CI if `asyncio.create_subprocess_exec` is reintroduced outside `safe_subprocess.py`.

#### H-IO-1 — Agent hot path does sync file I/O on the event loop

- **Where:**
  - `backend/agent/run_events.py:188–217`, `:259` — file lock + append + full-file trim inside `async def emit_run_event`
  - `backend/services/tools/executors.py:1798–1800` (`execute_file_write`) and `:2289–2321` (`execute_file_edit`) — sync open/read/write; `execute_file_read` already uses `asyncio.to_thread`
  - `backend/agent/tool_hooks.py` → `file_checkpoint.snapshot_path_for_tool` / `FileHistory.create_point` — `shutil.copy2` + JSON on every write tool
  - `backend/kernel/permission_court.py:311–377` — sync `k._call("decide_tool")` on every permission check (`tool_hooks` already uses `_acall` for checkpoint)
- **Impact:** One slow disk or a large spill file stalls **all** sessions on the uvicorn loop (chat WS, HTTP, other tools).
- **Suggested fix:** `await asyncio.to_thread(...)` for emit/spill, file write/edit, checkpoint, and Rust `decide_tool` (or `_acall` everywhere).

#### H-HTTP-1 — Image generation `aiohttp` sessions have no timeout

- **Where:** `backend/services/image/openai.py:65–66`, `backend/services/image/local.py:52–53`
- **Evidence:** `async with aiohttp.ClientSession() as session:` then `session.post(...)` with no `ClientTimeout`. aiohttp default total timeout is 300s per request.
- **Contrast:** LLM stack uses `http_session.request_timeout()` / `stream_timeout()`; `backend/core/outbound_http.py` defaults to 60s; search/RAG/Qdrant pass explicit timeouts.
- **Suggested fix:** `ClientTimeout(total=120, connect=10)` on the session or the `post`.

---

### Medium

#### M-SESS-1 — Deleted session IDs can be resurrected after tombstone TTL

- **Where:** `backend/api/websocket.py:1164–1168` (auto-create), `:1180–1184` (tombstone check after create path), `backend/api/routes/sessions.py:281–341`
- **Evidence:** `delete_session` tombstones, kicks WS (4004), `force_stop_agent`, then deletes the row. Tombstone is in-memory (~600s). After expiry, a client reconnect with the old UUID hits “session missing → `session_repo.create({id: session_id, ...})`”.
- Not a cross-user leak (new owner is the connecting user). It can replay disk spill keyed by session id (see M-SESS-2) and surprise users whose `localStorage` still holds the id.
- HTTP `/chat/completions` does not consult the tombstone; it only 404s if the row is gone (`backend/api/routes/chat.py:60–62`).
- **Suggested fix:** Do not auto-create sessions from a client-supplied UUID on reconnect; require REST create. Persist a deleted-id denylist or refuse unknown ids. Check tombstone on HTTP chat too.

#### M-SESS-2 — `delete_session` does not clear control-inbox spill

- **Where:** `backend/api/routes/sessions.py:321–330` vs `backend/api/websocket.py:1591–1593` (stop path **does** `get_inbox(session_id).clear_queue()`)
- **Evidence:** Delete clears grants and run snapshot, not `~/.tevarn/control_inbox/{session_id}`. Combined with M-SESS-1, queued steers can inject into a “new” conversation with the same id.
- Delete also proceeds when `agent_idle` is still false after 15s of cancel (logs and returns `agent_idle: false` but removes the row).
- **Suggested fix:** In `delete_session`, `clear_queue()` + delete spill file; optional 409 unless `force` when the agent is still running.

#### M-STATIC-1 — Stale check for missing `version.json` only applies to a folder named `static`

- **Where:** `backend/static_frontend.py:119–122`
- **Evidence:**

```python
if not found and root.name == "static" and prod:
    stale = True
    reason = "legacy_static_no_version_json"
```

- `cfccbbb` added `backend/tests/test_static_frontend.py` and `scripts/sync-backend-static.mjs` (requires `version.json` on copy). A `frontend/out` or `frontend/dist` tree without `version.json` and without the `0.5.4-alpha` HTML marker is **not** marked stale.
- Current repo `backend/static` has `index.html` and no `version.json` → correctly stale → API-only unless `TEVARN_ALLOW_STALE_STATIC=1`.
- **Suggested fix:** Require `version.json` for every candidate root in production. Extend tests to `out/` and `dist/`.

#### M-TOOL-1 — Schema integer-string coercion is agent-loop only

- **Where:** `backend/agent/loop_cluster.py:17–36`, `:381–384` (`_try_coerce_int` + `_coerce_tool_args` + clamp). Tests in `backend/tests/test_config_intent.py`.
- **Evidence:** `698ebe5` fixed LLM `"3"` vs `integer` for chat tool rounds. `ToolRegistry.execute` (`backend/tools/registry.py`) and `backend/api/routes/bridge.py` / `tools.py` do not run that pipeline. Nested / `oneOf` schemas are not walked. `execute_search` uses bare `int(...)` (`executors.py` ~2190).
- **Suggested fix:** Share `_validate_tool_args` with registry/bridge; wrap `int()` in executors; accept both `num_results` and `max_results` on search.

#### M-TOOL-2 — `configure_tevarn` Chinese status still falls through to overview

- **Where:** `backend/skills/builtins/configure_tevarn_skill.py:105–110`; `backend/content/product_handbook.py:570–576`, `TOPIC_ALIASES` ~482+
- **Evidence:** `action=guide` + `topic=status` is remapped to `action=status` (tested). `topic="系统状态"` / `"状态"` are not in `_ACTION_NAMES` and have no alias → `resolve_topic` → `"overview"`. The handbook footer even tells the user to say 「系统状态」.
- **Suggested fix:** Map `系统状态` / `状态` / `system status` to `action=status` (or add `TOPIC_ALIASES`).

#### M-WIN-2 — Dev / fallback uvicorn boots omit the Windows selector loop factory

- **Where:** `backend/cli.py:158+`, `backend/scripts/start_dev.py`, `backend/main.py` `__main__`, `scripts/run_uvicorn_win.py` fallback
- **Evidence:** Packaged path (`backend/win_boot.py`) sets policy **and** loop factory. `uvicorn.run(...)` without that factory can still get Proactor on Windows (logged as fatal-class in `main.py` ~410–414).
- **Suggested fix:** Reuse `win_boot.selector_loop_factory` on every Windows entrypoint.

#### M-AUTH-3 — `TEVARN_API_KEY` / `verify_api_key` is never used

- **Where:** `backend/api/dependencies.py:79–86` (only definition)
- **Evidence:** Grep of `backend/` shows no route `Depends(verify_api_key)`. `settings.api_key` is generated into `~/.tevarn/secrets.json` and rejected if weak, but it does not protect HTTP.
- **Suggested fix:** Wire it to the automation/webhook/bridge routes that operators would expect, or document it as unused and stop generating it.

#### M-AUTH-4 — WebSocket JWT skips HTTP `pwc` (password-change) check

- **Where:** `backend/api/websocket.py` connect path vs `backend/core/security.py:101–112`; also `backend/api/routes/domain_stream.py:64–75`
- **Evidence:** HTTP `get_current_user` calls `token_password_matches`. WS only decodes the token and checks the user exists. Tokens without `pwc` are accepted (7-day legacy window on HTTP too).
- **Suggested fix:** Share one “accept this access token” helper for HTTP and all WS routes.

#### M-AUTH-5 — Mobile pair: 6-digit code, no claim rate limit **(LAN / multi-user)**

- **Where:** `backend/services/mobile_pair_service.py:455`, `backend/api/routes/mobile_pair.py`
- **Evidence:** `secrets.randbelow(1_000_000)` → 6 digits, 300s TTL, unauthenticated `/claim`. Session mint is `admin@tevarn.dev` superuser. Fine on a locked-down desktop; brute-forceable if the API is on a LAN.
- **Suggested fix:** Higher-entropy code, per-IP rate limit, bind the paired device to a non-superuser in multi-user mode.

#### M-AUTH-6 — `TEVARN_CORS_ALLOWED_ORIGINS=*` + single-user = browser CSRF to admin

- **Where:** `backend/core/simple_cors.py:86–96`; documented in `backend/core/config.py:851–852`
- **Evidence:** Default CORS list is **empty** (loopback only). `*` is an explicit operator choice and the comment already says it disables cross-origin protection. Combined with loopback-peer auto-admin, a malicious page can drive `http://127.0.0.1:8090`.
- **Suggested fix:** Fail startup if `*` is set while `single_user_mode=True`.

#### M-SEC-1 — Settings mask only keys ending in `_api_key`

- **Where:** `backend/core/encryption.py:145–219`; `backend/schemas/setting.py:35–38`; `GET /api/settings` is admin-only (`settings.py:2479–2488`)
- **Evidence:** `encrypt_setting` / `mask_setting` key off `key.endswith("_api_key")`. `bridge_token` and nested `llm_model_catalog` credentials are stored and (on generic settings GET) returned in the clear. Dedicated catalog routes **do** call `mask_catalog_for_client`.
- On default desktop, the reader is the local admin (same trust as `secrets.json`). Still a backup / `tevarn.db` exfil problem, and a footgun if a second admin exists.
- **Suggested fix:** Encrypt/mask `*_token`, `*_secret`, `*_password`, `bridge_token`; persist catalog secrets in encrypted `*_api_key` rows or encrypt the blob. Never return a full `bridge_token` on GET after the generate-once response.

#### M-SEC-2 — Ephemeral admin password logged in plaintext

- **Where:** `backend/core/config.py:129–136`
- **Evidence:** If `~/.tevarn/initial_admin_password` cannot be written, the random password is interpolated into the warning log. The success path only logs the path (correct).
- **Suggested fix:** Log the path/error only; never the password.

#### M-ERR-1 — Routes raise `HTTPException(500, detail=str(e))` and bypass the generic 500 handler

- **Where (sample):** `backend/api/routes/files.py` (~311, 404, 610), `desktop.py`, `chat.py:101`, `cluster.py:558`, `knowledge.py:475`, `bridge.py:263`, `kernel.py` (several)
- **Evidence:** `backend/core/exceptions.py:190–204` hides internals (`"An unexpected error occurred"`). Explicit `HTTPException(500, f"...{e}")` is returned as-is.
- **Suggested fix:** Generic client message + `logger.exception`. Prefer `BizException` (defined, almost unused by routes).

#### M-KNOW-1 — Knowledge list includes `user_id IS NULL` rows **(multi-user)**

- **Where:** `backend/repositories/knowledge_repo.py:140–143`
- **Evidence:** `list_by_user` is `user_id == me OR user_id IS NULL`. Per-doc get/update/delete/index use `_is_doc_owner_or_admin` (`knowledge.py:53–56`, 194+). NULL rows are intended as seed/handbook, but any create path that forgets `user_id` becomes globally visible.
- Same NULL-is-global pattern: wiki (`wiki.py` `list_all`), context items, memory graph, cron (`*_repo.py`).
- **Suggested fix:** Treat NULL as admin-only seed; always set `user_id` on create; scope wiki/context.

#### M-IO-2 — Sync git / filesystem in async API handlers

- **Where:** `backend/api/routes/git.py` (`subprocess.run` in `async def`); `files.py` tree walk / `read_text`; `upload.py` sync write after `await file.read()`; `smoke_test.py` log tail
- **Suggested fix:** `asyncio.to_thread` for those handlers (same as glob/grep/sqlite executors).

---

### Low

| ID | Where | Note |
|----|--------|------|
| L-SESS-3 | `sessions.py:264–278` | Active-session 409 is checked before ownership. Authenticated caller can distinguish “exists and active” vs 404. Check owner first. |
| L-SESS-4 | `dependencies.py` `assert_session_owner` | If `request is None` and `single_user_mode`, ownership is skipped (“兼容旧调用”). `delete_session` does not pass `Request`. |
| L-DRAFT-1 | `frontend/components/chat/MessageInput.tsx` | Draft restore is mount-only; isolation depends on `key={sessionId}`. Image-send path still clears the box before async result. Backend-adjacent; `cfccbbb` fixed the failed-text-send case. |
| L-TOOL-3 | `loop_cluster.py` | Coercion is top-level properties only; no nested `items` / `properties`. |
| L-TOOL-4 | `web_search` vs `search` | Skill uses `num_results`, builtin uses `max_results`; whitelist can drop the other name. |
| L-AUTH-7 | `auth.py:36–68` | Open `/auth/register`; first user becomes superuser. Normal for desktop bootstrap; race if a multi-user host is exposed before the owner registers. |
| L-AUTH-8 | `files.py:134–145` | `FILE_BROWSER_LOCAL=1` + `mode=local` + absolute path → full host FS for any authenticated user (default **off**). |
| L-AUTH-9 | `main.py` `/uploads` StaticFiles | Unauthenticated; names are `uuid4`-prefixed. HTML/SVG upload already blocked. |
| L-AUTH-10 | `openai_codex_proxy.py` | No Tevarn JWT; relies on upstream ChatGPT bearer (commented in `routes/__init__.py`). |
| L-AUTH-11 | `runtime_status.py` | Unauthenticated base payload; extra metrics only on loopback. |
| L-KNOW-2 | `knowledge.py:234–262` | Indexer path: `Path.resolve()` + `relative_to(workspace\|uploads)` + suffix allow-list. Blocks `../` and symlink-out. Residual TOCTOU if a symlink is swapped between check and read. |
| L-PYD-1 | `api/routes/channels.py` | Last `class Config:` (v1 style); rest of backend is `model_config` / `model_validate` / `model_dump`. No `parse_obj` / `@validator` in production backend. Request schemas in `backend/schemas/` do not set `extra="forbid"`. |
| L-ERR-2 | `goals.py` | Errors returned as `{"error": "..."}` with HTTP 200. |
| L-ERR-3 | `rate_limit.py`, `simple_cors.py` | `{detail}` only; no `error.code` envelope. |
| L-HTTP-2 | `endpoint_probe.py` | Relies on caller session timeout; add a per-request default so a future bare `ClientSession()` cannot hang 300s × N URLs. |
| L-HTTP-3 | Channel adapters (`slack.py`, etc.) | Long-lived WS `ClientSession()` without connect timeout. |
| L-SEC-3 | `security_check.py` | Startup fail-checks JWT length; `api_key` only gets the known-weak-value reject, not a length floor in the security report. |

---

## Area notes (requested coverage)

### API correctness (session delete, draft restore, tool stop, static stale)

`cfccbbb` itself did **not** change `sessions.py`, `chat.py`, `tools.py`, or `static_frontend.py`. Backend session-delete logic is from earlier (`33084bf`-era). This commit: prefetch stop + tests, static stale **test** + sync script, frontend 4004 / draft-keep / local stop timeout, plus permission-court digest noise reduction.

| Claim | On HEAD |
|-------|---------|
| Session delete | Three-layer defense is real: active-ids, 409, `force=true`, tombstone, kick 4004, `force_stop_agent`. Gaps: M-SESS-1, M-SESS-2. |
| Draft restore | Frontend: per-session `localStorage` + remount key; failed text send keeps draft. Backend n/a. Residual: image path, mount-only effect. |
| Tool stop | Prefetch cancel is real and tested. H-STOP-1 / H-STOP-2 remain. `api/routes/tools.py` is registry CRUD, not chat stop (WS `{type:"stop"}` in `websocket.py`). |
| Static stale | `backend/static` without `version.json` is stale. M-STATIC-1 for other export roots. |

### `python` / `web_search` / `configure_tevarn`

| Claim (`698ebe5`) | On HEAD |
|-------------------|---------|
| Windows SelectorEventLoop killed `python` | **Fixed** on the chat local path via `safe_subprocess.create_process_exec` + tests in `test_platform_tools_win.py` / `test_silk_runtime_fixes.py`. **Not** fixed for workflow engine or capability-tool spawns. Dev uvicorn may still pick Proactor. |
| LLM integer strings failed schema | **Fixed** in `_validate_tool_args` (coerce + clamp) for agent-loop tools. **Not** on bridge/REST registry execute. |
| `topic=status` → overview | **Fixed** for English `topic=status`. Chinese 「系统状态」 still overview. |

### Auth, isolation, knowledge/docs path traversal

Default desktop: loopback auto-admin, CORS deny-by-default, relay header blocks fake loopback, startup fail-closed if `0.0.0.0` + `single_user_mode`, session CRUD uses `assert_session_owner`, knowledge doc mutations check owner/admin, RAG tenant filter fail-closed (`qdrant_impl.py`), upload names sanitized.

Knowledge indexer LFI: `doc.source` is resolved and must sit under `workspace/` or `uploads/` with an allow-listed suffix before `read_text`. That is the right pattern; not a confirmed traversal into `~/.tevarn`.

File sandbox: `(base / rel).resolve()` + `relative_to(base)` (and extra allowed roots). `FILE_BROWSER_LOCAL` is opt-in.

Multi-user holes that matter if that mode is turned on: traces IDOR, cluster WS, NULL `user_id` globals, unused API key, WS `pwc`, mobile pair entropy.

### Blocking I/O and outbound HTTP

Solid: LLM HTTP timeouts, `outbound_http.outbound_session` (60s), Qdrant/RAG/search timeouts, glob/grep/sqlite/`file_read` via `to_thread`, kernel RPC in `api/routes/kernel.py` via `to_thread`, Codex isolate worker in a thread.

Not solid: `emit_run_event` spill, `file_write`/`file_edit`, tool before-hooks, sync `decide_tool`, image aiohttp, some API-route git/fs, `endpoint_probe` inherited timeouts.

### Error handling, Pydantic, settings/secrets

- Unhandled exceptions are generic 500s. Many routes still leak `str(e)` via `HTTPException(500)`.
- `BizException` hierarchy exists and is almost unused.
- Pydantic v2 is the house style (`model_validate` / `model_dump` / `field_validator`). One leftover v1 `class Config` on channel read. No production `parse_obj`.
- Secrets: no hardcoded JWT/API key; `_load_or_generate_secret` + `_KNOWN_WEAK_SECRETS`; packaged builds skip cwd `.env`; `*_api_key` Fernet at rest; catalog/bridge_token are the main plaintext leftovers. `verify_api_key` is dead code.

---

## What looks solid

1. **Secret bootstrap** — Random JWT/API keys in `~/.tevarn/secrets.json` (0600); known weak values rejected; packaged dotenv guard (`TEVARN_PACKAGED=1`).
2. **Loopback single-user gate** — Peer address only (not `X-Forwarded-For`); `x-tevarn-relay` disables free-login; non-loopback + single-user fails closed at startup (`security_check.py`).
3. **CORS default** — Empty allow-list = loopback origins only; `*` is explicit and documented.
4. **Session HTTP isolation** — Session/message routes use `assert_session_owner` inside the unit of work. HTTP chat 403/404, no auto-create.
5. **Knowledge doc ACL + indexer roots** — Owner/admin on get/update/delete/index; 16 MiB cap; `resolve` + `relative_to` + suffix allow-list before reading `doc.source`.
6. **RAG tenant filter** — Missing `user_id` → impossible match (`qdrant_impl.py`).
7. **Windows chat `python`/`command`** — Central `safe_subprocess` with Selector → threaded Popen and `NotImplementedError` fallback; production `win_boot` sets the loop factory.
8. **Agent-loop tool args** — strip forbidden keys → whitelist → coerce ints/bools → clamp → jsonschema. The right LLM-tolerance stack.
9. **LLM / search HTTP** — Timeouts on the main outbound stacks; glob/grep/sqlite/`file_read` already off the loop.
10. **Session-delete happy path** — Tombstone + 4004 + `force_stop` + grant cleanup; frontend handles 4004 and stops reconnect (`cfccbbb`).
11. **Static `backend/static` without `version.json`** — Treated as stale; sync script refuses to copy an unstamped export.
12. **Pydantic v2** — Schemas and settings are overwhelmingly v2; `Settings.extra = "ignore"` is appropriate for env-heavy config.
13. **Generic 500 handler** — Uncaught exceptions do not dump traces to clients (`exceptions.py`).
14. **Security regression job** — `backend/tests/security` is a separate CI step (currently skipped only because Ruff fails first).

---

## Suggested fix order (separate PRs; not this one)

1. Unblock CI: Ruff F541 (`loop.py` two f-strings). Then look at the `81581cc` pytest list — start with i18n assertions (`下一步` / `轮次` / `熔断`) and `test_tool_policy` snapshots so the suite can be a signal again.
2. Tool stop: drop the `not in prefetched` carve-out; cancel serial execution on `_should_stop`.
3. Multi-user (if that mode is real): traces list/delete ownership; cluster WS ownership.
4. Event loop: `to_thread` for `emit_run_event`, `file_write`/`file_edit`, checkpoint hooks, `decide_tool`.
5. Image (and any other bare `ClientSession`) timeouts; workflow/capability subprocess via `safe_subprocess`.
6. Session delete lifecycle: no UUID auto-create; clear inbox spill; HTTP tombstone.
7. Settings mask/encrypt `bridge_token` + catalog; stop logging ephemeral admin passwords.
8. `configure_tevarn` Chinese status aliases; share schema coercion with bridge/registry.

---

## What this audit did not do

- Did not run the full pytest suite in this environment (Ruff would fail first; installing the CI dep set was out of scope for a report-only PR).
- Did not dynamically exploit IDOR, CORS, or mobile pairing.
- Did not audit Electron, the Rust kernel host, or the Next frontend except where they touch the named API bugs.
- Did not change CI, tests, or product code.

**Main SHA audited:** `cfccbbb9ed3a8a489097aa3a6e42ee0049a66e43`
