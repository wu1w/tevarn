# Tevarn security audit — 2026-08-18

- **Repo:** `wu1w/tevarn`
- **Audited revision:** `cfccbbb9ed3a8a489097aa3a6e42ee0049a66e43` (`origin/main` as of 2026-08-18)
- **Tip commit:** *Fix audit P1/P2 chat runtime gaps: session delete, draft restore, tool stop, and static stale checks.*
- **Scope:** read-only review of the Agent OS stack (Electron desktop + FastAPI control plane + Rust kernel host). No product code was changed.
- **Method:** independent code review of auth, court/grants, Electron IPC, secrets, SSRF, and the recent session-lifecycle claims. Findings were checked in the live tree; earlier audit notes (`.audit-report/CODE_AUDIT_REPORT_LOCAL_AI_OS_2026-08-02.md`) were treated as history, not as current truth.
- **Threat model used:** `docs/THREAT_MODEL.md` — local-first, single-user workstation. Primary adversaries are malicious prompts / tool output, malicious or tampered skill/MCP packages, and misconfiguration. Out of scope: multi-tenant SaaS, nation-state APT, and an attacker who already has the logged-in OS user.

---

## 1. How to read this report

This is not a “remote account takeover” audit. Default bind is `127.0.0.1`, `single_user_mode=true`, and loopback clients are treated as the owner. Those choices are **intentional** for a personal Agent OS. They are called out only when they become an amplification path for the in-scope adversaries (prompt injection, XSS in the trusted renderer, a tool that can read product secrets).

Severity:

| Rank | Meaning in this product |
|------|-------------------------|
| **Critical** | Prompt / tool output can steal or mint **control-plane** credentials (JWT, desktop-permission HMAC, admin password) and persist that access. |
| **High** | Prompt / tool / MCP can **escape the stated workspace/court contract** (secret floor, extra_roots, confirmation) or persist third-party credentials. |
| **Medium** | Real gap, but needs another condition (XSS, `execution_mode=local`, LAN bind, or a claimed-fix that is only partial). |
| **Low** | Hardening / hygiene. Does not by itself break the court. |

---

## 2. Executive summary

The control plane has been hardened in the right places since the 2026-08-02 audit: Rust court is authoritative when the host is up, `_confirm_ok` / `_session_grant` are stripped before kernel RPC, detached FastAPI reuse is bound to `jwt_fp`, Electron renderer cannot talk Node directly, and desktop permission is proven by a main-process HMAC.

The remaining high-impact problems sit on the **tool execution** side of the trust boundary:

1. The **non-sandbox spawn path** for `python` / `command` inherits the backend’s product secrets (`TEVARN_JWT_SECRET`, desktop HMAC, admin password).
2. The **default Windows path** (`JobBackend`) curates env well, but has **no filesystem isolation**, so `python` still bypasses the court’s secret floor.
3. `extra_roots` and `host_data_roots()` permanently widen read scope (including `~/.tevarn`) from chat text and host layout, without a confirm.
4. The `manage_mcp` agent tool can persist MCP env/credentials without the admin REST gate.
5. Agent HTTP/browser block cloud metadata on the **first URL only**; redirects and IPv6-mapped IMDS are not normalized.

Recent commit `cfccbbb` **did** make session delete / stop / draft restore safer at the **agent-task and UI** layer. It did **not** tear down background OS processes, Playwright, or MCP stdio. Tests for that commit are mostly static string checks.

---

## 3. Findings

### Critical

#### C1 — `python` / `command` inherit control-plane secrets on the local spawn path

**Where**

- `electron/main.ts:1092–1108` injects `TEVARN_JWT_SECRET`, `TEVARN_API_KEY`, `TEVARN_DEFAULT_ADMIN_PASSWORD`, `TEVARN_DESKTOP_PERMISSION_SECRET` into the FastAPI child.
- `backend/services/tools/executors.py:2065` (`execute_python`) and `:1339` (`command`) call `create_process_exec` / `create_process` with `env=None`.
- `backend/core/safe_subprocess.py:529–557` documents that `env=None` inherits the parent environment.

**When it applies**

This is **not** the Windows Job path (see “Verified OK”). It **is** the path when:

- `agent_execution_mode=local`, or
- `auto` and no sandbox capability (Linux without bubblewrap degrades to local), or
- ComputerManager is skipped (`should_use_sandbox()` false).

The UI literally tells the user to switch to “本机直跑” when sandbox setup fails (`executors.py:2049–2051`).

**Why it matters**

A prompt-injected `python` tool can `print(os.environ["TEVARN_JWT_SECRET"])` and mint a 7-day admin JWT, impersonate the desktop-permission channel, or decrypt settings. That is a durable control-plane compromise, not “the user asked the agent to run code.” MCP stdio already uses curated `build_process_env` (`backend/mcp_hub/client.py:210–217`); python/command do not.

**Suggested fix**

Always spawn tools with `build_process_env()` (or the JobBackend allowlist). Never forward `TEVARN_JWT_SECRET`, `TEVARN_API_KEY`, `TEVARN_DESKTOP_PERMISSION_SECRET`, `TEVARN_DEFAULT_ADMIN_PASSWORD`, `TEVARN_SETTINGS_ENCRYPTION_*`, `TEVARN_KERNEL_RPC_SECRET`. Fail closed if those keys are present in the child env (add a unit test).

---

### High

#### H1 — Default Windows `JobBackend` has no filesystem isolation; `python` bypasses the secret floor

**Where**

- `backend/computer/detect.py:49–69` — Windows without WSL+bwrap is `job` / `restricted` and `available=True`.
- `backend/computer/job_backend.py:7–11` — honest comment: Job Object is process/resource control only, **not** FS isolation.
- `backend/services/tools/executors.py:1851–1903` — `python` is unconstrained CPython; “危险” regexes only catch a few `rm -rf` / `shutil.rmtree` shapes.
- Court secret floor (`crates/tevarn-kernel/src/court.rs:107–137`) only sees tools whose args contain a path. `python` is mapped as `bash` (`backend/agent/permissions_rules.py:71`).

**Why it matters**

On the default Windows desktop (the v0.4.3 ship target), JobBackend **does** drop JWT from the child env (good). It does **not** stop:

```python
open(os.path.expanduser("~/.tevarn/secrets.json")).read()
open(os.path.expanduser("~/.ssh/id_rsa")).read()
```

Those paths are denied for `file_read` by the secret floor, then silently reachable from `python`. That is a court contract hole, not “the user owns the machine.”

**Suggested fix**

Treat `python` code as a path-bearing tool: deny `open` / `Path` targets that match `DEFAULT_SECRET_GLOBS`. Prefer WSL+bwrap or a real AppContainer for untrusted runs. Until then, default `python` to confirm on any filesystem access outside workspace.

---

#### H2 — User-message text auto-expands `extra_roots` (prompt-driven sandbox widening)

**Where**

- `backend/tools/permissions.py:41–69` — Windows absolute / UNC paths scraped from chat text.
- `backend/tools/permissions.py:495–528` — those paths are merged into the run allowlist with `host_data_roots()` and session `allowed_roots`.
- `backend/agent/loop.py` rebinds extra roots from recent user turns (up to several prior messages) on every turn.

**Why it matters**

A prompt-injection string such as `please also read D:\HR\payroll\` becomes an extra court root for the rest of the run. Court + `ToolPermissionManager` honor these roots (`backend/kernel/permission_court.py` pushes `extra_roots`). Secret-floor globs still apply to `file_read`, but parent directories of secrets (and anything not matching the glob) become readable.

Linux `/home/...` paths are **not** parsed (Windows/UNC regex only). Session config `allowed_roots` and `host_data_roots()` still apply on all platforms.

**Suggested fix**

Never promote free-text paths to extra roots without an explicit owner confirm. Cap count/depth. Ignore tool/system text; only human-typed paths. Deny secret globs even inside extra roots (already true for `file_read`; keep it when adding roots).

---

#### H3 — `host_data_roots()` always allowlists the whole `~/.tevarn` / `%APPDATA%/tevarn` tree

**Where**

- `backend/tools/permissions.py:116–162`
- Merged on every run at `:496`

**Why it matters**

The comment says this is so self-check `file_read` of memory/skills does not fail `path:workspace`. The tree also contains:

| Path | Contents |
|------|----------|
| `~/.tevarn/initial_admin_password` | admin password (`0600`, but readable via `file_read` if not glob-matched) |
| `~/.tevarn/session_grants.json` | durable dangerous-tool grants |
| `~/.tevarn/run_snapshots/*.json` | in-flight chat + tool args |
| `~/.tevarn/logs/*.log` | backend logs |
| `secrets.json` / `rpc.secret` | blocked by `**/*secret*` for `file_read` only |

`initial_admin_password` does **not** match `**/*secret*` / `**/*credentials*`.

**Suggested fix**

Allowlist specific subtrees (`skills/`, `memory/`, `data/workspace`). Deny `initial_admin_password`, `session_grants.json`, `run_snapshots/`, `logs/`, `secrets.json`, `rpc.secret` even under host data roots.

---

#### H4 — `manage_mcp` agent tool can CRUD MCP servers and persist env (admin REST bypass)

**Where**

- REST create requires admin: `backend/api/routes/mcp.py` (`Depends(require_admin)`).
- Agent tool `manage_mcp` `add`/`update` writes `env` with no admin check: `backend/tools/builtins/manage_integration_tools.py:204+`.
- Credentials stored plaintext in SQL JSON: `backend/models/mcp_server.py` (`env` column).
- Engineering/coding profile grants `manage_mcp` by default: `crates/tevarn-kernel/src/coding_profile.rs`.
- `manage_mcp` is **not** in `TOOL_TO_KEY` (`backend/agent/permissions_rules.py:44–80`), so headless/CEO `local_allow` does not treat it as high-risk bash.

**Why it matters**

Prompt injection → agent adds a malicious stdio or URL MCP → persistent credentials + new `mcp_*` tools. Mounted `mcp_*` names are court-allowlisted (`crates/tevarn-kernel/src/court.rs:369–380`) with `allow_mcp_prefix` hardcoded `True` (`backend/kernel/permission_court.py`). That is the main **non-workspace egress** for the agent.

**Suggested fix**

Split read-only MCP ops for agents. Require owner confirm (or admin) for `add`/`update`/`delete`. Encrypt `env` at rest. Default `allow_mcp_prefix=false` unless the server is explicitly enabled. Interpolate `${VAR}` only from a user secret store, not `os.environ` (`backend/mcp_hub/normalize.py:142–148`).

---

#### H5 — Agent HTTP/browser: metadata block is first-URL-only; IPv6-mapped IMDS not normalized

**Where**

- Policy: `backend/core/net_safety.py:88–181` — hard-block a small IMDS set; loopback/RFC1918 **allowed** unless `agent_block_private_network=true` (default false). This local-first choice is documented and tested.
- `backend/services/tools/executors.py:1819–1844` — `execute_http` checks the first URL, then `aiohttp` follows redirects with no per-hop re-check.
- `_METADATA_HOSTS` includes `169.254.169.254` and `fd00:ec2::254` but not `::ffff:169.254.169.254`. `ipaddress` treats that IPv6-mapped address as neither link-local nor private.

**Why it matters**

`web_search` only hits Tavily / DDG / Bing / Wikipedia — low SSRF. `http` / `browser` are the problem: a prompt-injected fetch to an attacker-controlled URL can 302 to IMDS or to the local FastAPI admin surface. Package downloaders (`packages/market.py`) already re-validate hops; agent tools do not.

`python` / `command` never call `check_agent_url` at all (see H1 / C1).

**Suggested fix**

Re-validate every redirect (or disable auto-follow and pin the connecting IP). Normalize IPv6-mapped / decimal / octal forms before the metadata set. For any non-loopback bind, default `agent_block_private_network=true`.

---

#### H6 — Trusted-renderer XSS can drive `electronAPI` + loopback admin API

**Where**

- Packaged UI served **without CSP**: `electron/main.ts` HTML handler writes `Content-Type` only.
- Backend CSP in `single_user_mode` allows `'unsafe-inline' 'unsafe-eval'`: `backend/core/security_headers.py`.
- Mermaid SVG injected unsanitized: `frontend/components/chat/MarkdownContent.tsx:362–366` (`dangerouslySetInnerHTML`). File previews use DOMPurify; Mermaid does not.
- Preload surface: `electron/preload.ts:33–89` — `openTevarnCode` (spawn), `installUpdate`, `openPath`, `openExternal`, `grantDesktopPermission`.
- JWT in `localStorage` + a **non-HttpOnly** `tevarn-auth` cookie whose value is the raw JWT: `frontend/stores/authStore.ts:13–18, 54–66`.

**Why it matters**

The Node boundary is solid (`contextIsolation`, `sandbox`, no raw `ipcRenderer`). The remaining privilege domain is “script on `http://127.0.0.1:3000`.” Agent/tool output that wins an XSS (Mermaid is the clearest sink) gets same-origin `/api` (loopback admin) plus process spawn / update install. That matches the threat model’s “untrusted tool output / document” adversary.

**Suggested fix**

CSP on the Electron static HTML (nonce/hash, no `unsafe-eval`). Run Mermaid through DOMPurify or a sandboxed iframe. Require a main-process confirm for `open-tevarn-code` and `install-update`. Prefer HttpOnly session cookies.

---

### Medium

#### M1 — Session delete / stop cancel the agent task, not background tools

**Claim in `cfccbbb`:** “session delete, draft restore, tool stop.”

**What the current code actually does** (`backend/api/routes/sessions.py:254–341`, `backend/api/websocket.py:1508–1520`):

| Step | Status |
|------|--------|
| Ownership check before cancel | Yes |
| Tombstone + WS kick (`4004`) | Yes — new `user_input` / `track_agent_task` / broadcasts blocked |
| `force_stop_agent` (12s) + extra `cancel_agent` (3s) | Yes |
| `end_run_snapshot` | Yes |
| `clear_session_grants` | Yes (session grants only) |
| Foreground `python` / `command` `CancelledError` → `kill_process_tree` | Yes (`executors.py:1369–1373`, `2087–2095`) |
| Timeout→background jobs (`process_registry`) | **Not killed.** `list_processes()` is global (`process_registry.py:426–443`). No `kill_session`. |
| Playwright contexts / MCP stdio | **Not session-torn-down** |
| Identity-scope grants (`grant_agent_capability`) | **Survive** delete |
| Delete proceeds if `agent_idle` is still false | Yes — tombstone only (`sessions.py:307–311`) |

Draft restore is not a separate API. Cancel/fail persist `_persist_final_response` + run snapshots keyed by sanitized `session_id`. Isolation looks correct; I did not find a path that loads session B’s snapshot into session A. Redaction on persist is pattern-based (see M3).

Tests in `backend/tests/test_loop_stop_ux.py` and `test_silk_runtime_fixes.py` are **source-string / UX** checks, not process-kill tests. `cfccbbb` itself mostly changed frontend chat + `tool_round.py` parallel-stop; `sessions.py` was already in this shape.

**Suggested fix**

On stop/delete: `kill_process_tree` every `BgProcess` for that `session_id`; close Playwright; fail in-flight MCP calls. Fail-closed (409) if `agent_idle` is false, or persist orphan PIDs for a janitor. Add a real integration test that asserts the OS pid is gone.

---

#### M2 — Electron `secrets.json` / `initial-credentials.txt` written without `0600`

**Where:** `electron/main.ts:250–267`. Backend `~/.tevarn/secrets.json` **does** `chmod 0600` (`backend/core/config.py`). Electron `writeFileSync` follows umask (often `0644` on Linux). `initial-credentials.txt` holds the plaintext admin password and is never deleted after first login.

**Suggested fix:** `fs.chmodSync(0o600)` (and Windows ACL). Use `safeStorage` / OS keychain. Delete `initial-credentials.txt` after password change.

---

#### M3 — Log and chat redaction miss the message body and several token shapes

**Where**

- `backend/core/logging_config.py:66, 87–88` — JSON formatter masks `extra` keys only; `message` is raw. `HumanFormatter` never redacts.
- Electron copies all backend stdout to the desktop console (`electron/main.ts` backend log tail).
- `backend/services/secret_redact.py` — no JWT (`eyJ…`), Fernet (`gAAAAA…`), `tvly-`, `sk-ant-`, `xoxb-`. `_BARE_LONG_RE` only runs if the text mentions “api key / 密钥 / secret / token”.
- WS `user_message_ack` can echo unredacted enriched input (`backend/agent/loop_io.py`).

**Suggested fix**

Run `redact_secrets` on every log record message. Expand token regexes. Redact WS ack content. Never log `initial_admin_password` (`backend/core/config.py` ephemeral-log path).

---

#### M4 — Encryption at rest is only `*_api_key`

**Where:** `backend/core/encryption.py:145–156`. Plaintext in SQLite: model-catalog OAuth `refresh_token`, `mcp_servers.env`, `bridge_token`. `GET /api/settings` masks `*_api_key` only; `bridge_token` is returned in full to admin.

**Suggested fix:** Encrypt catalog blobs, MCP env, and `bridge_token`. Extend `_is_sensitive_key` to `*secret*`, `*token*`, `bridge_token`.

---

#### M5 — CEO / desktop `local_allow` silently approves court `ask`

**Where:** `backend/agent/tool_hooks.py:465–502`. When court returns `ask` and origin looks like owner chat without a live frontend, writes and non-pattern commands proceed without `_confirm_ok`. Headless `safe` still auto-allows `file_write` (`:528–553`).

**Why it matters:** Prompt injection in main chat (or cron with default `safe`) can write without a confirm. Dangerous **category** commands still hit `enforce_command_policy`.

**Suggested fix:** Narrow `local_allow` to read-only tools. Treat `edit` / `file_write` as high-risk in headless.

---

#### M6 — `command` absolute `cwd` is unbounded; product secrets are not in the exfiltration regex

**Where:** `backend/services/tools/executors.py:1078–1080`, `:96`. Exfiltration patterns cover `.ssh/id_rsa`, `.aws/credentials`, `.config/gcloud` — not `secrets.json`, `rpc.secret`, or Electron `userData`. `curl 169.254.169.254` is not classified as exfiltration.

**Suggested fix:** Reject absolute `cwd` unless under workspace / extra_roots (JobBackend already checks this; the local spawn path does not). Classify `~/.tevarn`, `userData`, and metadata IPs as deny/exfiltration.

---

#### M7 — Some IPC handlers skip `assertTrustedIpc`

**Where:** `electron/main.ts:2168–2172` — `get-user-data-path`, `get-backend-url`, `get-ws-url` have no origin check. Privileged handlers (`open-path`, `open-tevarn-code`, `install-update`, `grant-desktop-permission`) **do** check. Error-page `data:` documents keep preload, so unrestricted handlers still answer.

**Suggested fix:** Apply `assertTrustedIpc` to every invoke. Do not load `data:` in the privileged window.

---

#### M8 — Capability HMAC defaults to a derivation of the JWT secret

**Where:** `backend/kernel/signing.py` (HKDF from `jwt_secret` if `TEVARN_TOKEN_HMAC_SECRET` unset). Documented in `docs/THREAT_MODEL.md` as residual.

**Suggested fix:** Generate and persist a separate HMAC secret in `secrets.json` / Electron secrets, same as JWT.

---

### Low

#### L1 — 7-day access JWT, no refresh; legacy tokens without `pwc` survive password change

`backend/core/security.py` (`ACCESS_TOKEN_EXPIRE_DAYS = 7`; `token_password_matches` returns True if `pwc` missing).

#### L2 — WebSocket still accepts JWT in the query string

`backend/api/websocket.py`, `backend/api/routes/domain_stream.py`. Frontend prefers first-message auth (good). Query tokens land in proxy logs.

#### L3 — `/uploads` StaticFiles mount is unauthenticated

`backend/main.py`. Harmless on loopback; world-readable if the host is ever bound off-loopback.

#### L4 — `/api/runtime/status` is unauthenticated

Exposes `jwt_fp` (SHA256 prefix, not the secret), pid, role. Needed for Electron reuse; useful for local fingerprinting.

#### L5 — Open registration when `single_user_mode=false`

`backend/api/routes/auth.py` — first registrant becomes superuser. Disable by default for multi-user.

#### L6 — Mobile pair: 6-digit code, no rate limit on `/pair/claim`

Relevant only if mobile pairing is enabled on a non-loopback bind. `backend/services/mobile_pair_service.py`, `backend/api/routes/mobile_pair.py`.

#### L7 — `tevarn-agent` default bind `0.0.0.0`; `exec.run` does not jail `cwd`

`tevarn-agent/tevarn_agent/server.py`, `tevarn_agent/services/exec_service.py`. File API is jailed. Only in play if the remote agent is deployed.

#### L8 — Rust court skips workspace containment for **relative** paths

`crates/tevarn-kernel/src/court.rs:775–779`. Relies on Python `Path.resolve()` + `relative_to`. Defense-in-depth gap.

#### L9 — Rust still honors `_confirm_ok` / `_session_grant` args if a caller skips sanitizer

`crates/tevarn-kernel/src/court.rs:516–527`. Production RPC path strips them (`backend/kernel/tool_gate.py:52–66`, `backend/agent/loop_cluster.py`). Prefer deleting the arg-flag path and using only `SessionGrantStore`.

#### L10 — Electron sets `CORS_ALLOWED_ORIGINS`; Settings expects `TEVARN_CORS_ALLOWED_ORIGINS`

`electron/main.ts:1114–1119` vs `backend/core/config.py:853`. Loopback origins still allowed via `_is_loopback_origin`.

#### L11 — Rate limiting skipped for single-user loopback

`backend/core/rate_limit.py`. Auth brute-force from another local process is unthrottled.

#### L12 — `/docs` and `/openapi.json` remain reachable (rate-limit exempt)

`backend/main.py`.

---

## 4. What was verified is OK

These controls were checked in the current tree and should not be re-litigated as open bugs.

### Authn / detached reuse

- **`jwt_fp` reuse predicate is correct.** Electron will not attach to a detached FastAPI whose JWT secret was rotated, nor to a process claiming `role=kernel_host`. Mirror implementations: `backend/api/runtime_identity.py:21–63`, `electron/main.ts:376–414`. Tests: `backend/tests/test_silk_runtime_fixes.py:131–182`.
- **Invalid Bearer is 401**, never a silent fallback to anonymous admin (`backend/api/dependencies.py:192–226`).
- **`0.0.0.0` + `single_user_mode` fails startup** (`backend/core/security_check.py:61–70`).
- **VPS relay header** (`x-tevarn-relay`) blocks loopback free-login (`dependencies.py:110–139`).
- **Weak known JWT/API secrets** rejected at startup (`backend/core/config.py`).
- **CORS:** non-loopback browser origins get 403 (`backend/core/simple_cors.py`). `TEVARN_CORS_ALLOWED_ORIGINS=*` is an explicit weaken.
- **Password change** issues a new JWT with updated `pwc` (`backend/api/routes/auth.py`).
- **Electron single-instance lock** focuses the existing window instead of spawning a second backend (`electron/main.ts`).

### Court / grants / path

- **Rust is authoritative** when the host answers. Python no longer has a dual override for `extra_roots` / MCP prefix / session grants (`backend/tests/kernel/test_court_authority.py`; `backend/kernel/permission_court.py`).
- **Production Python court tail is locked** unless `TEVARN_DEV_UNSAFE` / explicit `TEVARN_KERNEL_BACKEND=python` (`docs/THREAT_MODEL.md`, `backend/kernel/production_guard.py`).
- **Kernel RPC is authenticated.** Non-public methods require `params._rpc_auth` compared in near-constant time (`crates/tevarn-kernel-host/src/main.rs:2596–2740`). Public: `ping` / `health` / `list_methods` / `abi_version` only. Secret file is `0600` / Windows ACL.
- **Internal privilege flags are stripped** before kernel RPC and model validation (`tool_gate.py:52–66`). Debug HTTP tool execute without a process is blocked unless `TEVARN_ALLOW_DEBUG_TOOL_EXECUTE=1`.
- **Tool hooks fail closed** if the machinery throws (`backend/tools/registry.py`).
- **Secret floor** denies `.env`, SSH keys, `*secret*`, kubeconfig, gcloud, etc. for path-bearing tools (`court.rs:107–137`, unit test `secret_floor_denies_env`). User deny wins over MCP prefix allow.
- **`file://` / `gopher://` / `ftp://`** rejected for `http`/`browser`. Direct `169.254.169.254` / `metadata.google.internal` blocked (first URL).
- **`web_search` does not take a user URL.**
- **Linux `bwrap` backend** `--clearenv`, does not bind host HOME (`backend/computer/bwrap_backend.py:8–10, 66–70`).
- **Windows JobBackend curated env** does not pass `TEVARN_JWT_SECRET` (`job_backend.py:268–299`).
- **Session grants** persist with TTL, rehydrate into Rust, and are cleared on session delete (`backend/agent/grant_store.py`, `crates/tevarn-kernel/src/session_grants.rs`).
- **`file_write` executor is narrower than `file_read`** (workspace only) — not an escape; an inconsistency.

### Electron IPC

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` (`electron/main.ts` BrowserWindow prefs).
- No raw `ipcRenderer` / `fs` / `child_process` on `window`. No wildcard IPC router.
- `will-navigate` / `will-redirect` / `setWindowOpenHandler` lock the main frame to the app origin; external http(s) goes to the system browser.
- No `<webview>`, no custom protocol / deep-link handlers found.
- `open-path` requires absolute path, `realpath`, and blocks executable extensions (`electron/main.ts:2181–2209`).
- Desktop permission API requires `X-Tevarn-Desktop-Permission` HMAC from main (`backend/api/routes/desktop.py:77–83`). Renderer cannot self-attest.
- Renderer cannot start/stop the backend or kernel host (spawn is main-process lifecycle only).
- Packaged mode strips packager/dev API keys from the backend env and sets `TEVARN_PACKAGED=1` (`electron/main.ts:1123–1165`).

### Session delete / draft / stop (what the recent work got right)

- Delete tombstones first, then kicks WS, then waits on the agent task, then drops the snapshot and session grants, then deletes the row.
- Stop (`control=stop`) sets `_should_stop`, `cancel_agent(wait=6)`, `end_run_snapshot`, bumps generation, clears the control inbox.
- Parallel tool batches watch `_should_stop` and cancel outstanding tasks (`backend/agent/phases/tool_round.py:135–207`).
- Draft persist is session-scoped; snapshot filenames sanitize `session_id`.
- Empty user-stop does not persist a fake “stopped” English placeholder (`backend/tests/test_loop_stop_ux.py`).

These are real fixes. They are incomplete for **OS children** (M1), not fictional.

---

## 5. Residual risk that is in-policy (not a finding)

Recorded so this report is not a fear list.

| Behavior | Why it is in-policy |
|----------|---------------------|
| Loopback client without JWT is admin when `single_user_mode=true` | Personal desktop; fail-closed if bound off-loopback. |
| Agent `http`/`browser` may call localhost / RFC1918 | Documented local-first; `agent_block_private_network` exists. |
| Owner can read their own workspace, including project `.env` if they grant it | Secret floor still blocks typical secret filenames for `file_read`. |
| Kernel host on `127.0.0.1:17890` | Same OS user + RPC secret is the intended boundary. |
| `TEVARN_DEV_UNSAFE` / `TEVARN_KERNEL_BACKEND=python` | Explicit escape hatches. |
| 7-day JWT in the owner’s browser | Single-user desktop; XSS makes this worse (H6), not a remote issue. |

---

## 6. Suggested fix order

1. **C1** — curated env for every `python`/`command` spawn (including `execution_mode=local`). Cheap, high leverage.
2. **H1 + H3** — secret-floor `python`, shrink `host_data_roots`.
3. **H4** — confirm + encrypt MCP install; do not let the agent be an admin REST bypass.
4. **H2** — confirm before extra_roots from chat text.
5. **H5** — redirect-aware URL policy; same policy for `command`/`python` network.
6. **H6** — CSP + Mermaid sanitize + native confirm for spawn/update.
7. **M1** — session-scoped process kill on stop/delete; add a pid-level test.
8. **M2–M4** — file modes, log redaction, encrypt non-`*_api_key` secrets.

---

## 7. Key files

| Area | Path |
|------|------|
| JWT / `pwc` | `backend/core/security.py` |
| Auth dependency / loopback admin | `backend/api/dependencies.py` |
| jwt_fp / reuse | `backend/api/runtime_identity.py`, `electron/main.ts` |
| Startup gates | `backend/core/security_check.py` |
| CORS | `backend/core/simple_cors.py` |
| Rust court | `crates/tevarn-kernel/src/court.rs` |
| Session grants | `crates/tevarn-kernel/src/session_grants.rs`, `backend/agent/grant_store.py` |
| Host RPC auth | `crates/tevarn-kernel-host/src/main.rs` |
| Python court bridge | `backend/kernel/permission_court.py` |
| Tool gate | `backend/kernel/tool_gate.py` |
| Path / extra_roots | `backend/tools/permissions.py` |
| Tool executors / SSRF | `backend/services/tools/executors.py`, `backend/core/net_safety.py` |
| Job / bwrap isolation | `backend/computer/job_backend.py`, `backend/computer/bwrap_backend.py` |
| Permission hooks | `backend/agent/tool_hooks.py` |
| MCP | `backend/tools/builtins/manage_integration_tools.py`, `backend/mcp_hub/normalize.py` |
| Session delete / stop | `backend/api/routes/sessions.py`, `backend/api/websocket.py` |
| Redaction | `backend/services/secret_redact.py`, `backend/core/logging_config.py` |
| Electron preload / IPC | `electron/preload.ts`, `electron/main.ts` |
| Desktop HMAC | `backend/api/routes/desktop.py` |
| Frontend auth cookie | `frontend/stores/authStore.ts` |
| Mermaid sink | `frontend/components/chat/MarkdownContent.tsx` |

---

## 8. What this audit did not do

- No dynamic exploit development, no fuzzing, no live Electron run in this environment.
- Mobile Flutter / mesh path was only sampled (pairing codes).
- `tevarn-code` standalone CLI has its own permission stack (`tevarn-code/src/tevarn_code/agent/permissions.py`) and was not fully re-audited; it is a second surface when used without the host court.
- Supply-chain / package signing was not re-opened beyond noting prior P1-02 work (remote packages now have a hash path).
