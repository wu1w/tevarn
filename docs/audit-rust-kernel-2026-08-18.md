# Rust kernel/host audit — 2026-08-18

Read-only review of the Rust control plane (`crates/tevarn-kernel`, `crates/tevarn-kernel-host`, `crates/tevarn-runtime`) and the Python/Electron surfaces that recently collapsed court authority and chat stop/end UX into that host.

This PR does not change product or application code.

| Field | Value |
|---|---|
| Audited SHA | `cfccbbb9ed3a8a489097aa3a6e42ee0049a66e43` |
| Branch audited | `main` |
| Date | 2026-08-18 |
| Threat model | Local-first, single-user Agent OS (`docs/THREAT_MODEL.md`) |
| Method | Independent static review of latest main + kernel-ci logs. No assumed root cause. |
| Scope | Court/authority, process lifecycle, tool dispatch/cancel/timeout, token/context, concurrency, error/panic paths, kernel-ci |

Recent related commits (context only):

- `4133b5f` — Unify Rust court authority and chat stop/end UX (`extra_roots`, `mcp_*`, session grants; partial replies; detached backend reuse only when `jwt_fp` matches).
- `cfccbbb` — Follow-up chat runtime gaps (session delete, draft restore, tool stop).
- `758dc70` / `1787e488` / `d38686d` — Thinking strip, `file_read` window, glob brace expansion (Python executors, not kernel).

---

## Summary

The court unification is real and mostly in the right place: Python `permission_court` treats a Rust `decide_tool` result as authoritative, `extra_roots` / `allow_mcp_prefix` live on `CourtPolicy`, and `SessionGrantStore` can upgrade `ask` → `allow` without trusting `_session_grant` flags on the Python→RPC path (those flags are stripped by `sanitize_args_for_kernel`).

It is not finished. Several layers still short-circuit *before* the process capability / session-grant store, the host court policy is a single global last-writer-wins map, and a few confirmation-class tools are missing from the write/command lists. The host also has a structural availability risk: `parking_lot::RwLock` is held across WASM invoke and audit disk I/O, and the 30s `spawn_blocking` timeout does not cancel the worker.

`kernel-ci` on main is red for a **stale unit test**, not a host crash. `loop_guard::tests::orch_window_force` still expects a trip after ~3–4 orch-heavy rounds; production logic requires **6 of 8**. Cargo tests fail first, so the Python ABI suite in that workflow never runs. `backend/tests/kernel/test_court_authority.py` is not even listed in `.github/workflows/kernel-ci.yml`.

---

## Findings

Severity is product-risk under the local-single-user threat model: **critical** = host-wide freeze or a court hole that skips capability *and* confirmation for a high-impact class of tools; **high** = bypassable or half-migrated authority / hang that a prompt or concurrent session can hit; **medium** = residual dual-store, stale docs, or library-path panics; **low** = hygiene.

### Critical

#### C1. `wasm_invoke` holds the global kernel write lock for the whole guest run; dispatch timeout does not abort it

- **Where:** `crates/tevarn-kernel/src/kernel.rs` (`wasm_invoke`, ~4047–4057); `crates/tevarn-kernel-host/src/main.rs` (`DISPATCH_TIMEOUT` 30s, `handle_connection` ~2770–2816, `run_stdio` ~2845–2861).
- **What:** `AgentKernel` is one `parking_lot::RwLock<KernelInner>`. `wasm_invoke` takes `inner.write()` and then runs wasmtime. Host TCP dispatch wraps `spawn_blocking(dispatch)` in `tokio::time::timeout(30s)`. Timeout returns JSON-RPC `-32603` to the caller but **does not abort** the blocking task. That task keeps the write lock. `emit_locked` also appends the audit JSONL under the same write lock (`kernel.rs` ~279–300, `audit.rs` `append`).
- **Effect:** One long/hung WASM skill (or a stuck audit write) serializes *all* mediate / charge / decide_tool / ping-on-blocking-pool work. After 64 hung blocking tasks (`max_blocking_threads(64)`), new TCP accepts still succeed but dispatches starve — the comment in host already names this failure mode. Stdio mode has **no** timeout at all.
- **Suggested fix:** Drop the kernel lock before `wasm.invoke` (operate on a cloned module handle). Move `audit_store.append` off the write lock (channel + dedicated writer). On dispatch timeout, record a “orphan blocking task” metric and refuse new WASM invokes until the lock is free. Add a tokio timeout / wasmtime epoch interruption that actually traps the guest. Give stdio the same 30s bound if stdio is a supported production path.

---

### High

#### H1. `mcp_*` is allowlisted before capability, skill contract, path, steward, and session grant

- **Where:** `crates/tevarn-kernel/src/court.rs` `decide_tool` ~369–381; Python fallback `backend/kernel/permission_court.py` ~441–449; catalog contrast `crates/tevarn-kernel/src/tool_catalog.rs` ~101–111.
- **What:** After secret floor and user deny only, `allow_mcp_prefix && name.starts_with("mcp_")` returns `allow` / `mcp:mounted_allow`. Default `CourtPolicy.allow_mcp_prefix` is `true`. Python always pushes `allow_mcp_prefix: True` when syncing policy. The court does **not** check that a server is mounted, nor `manage_mcp` / process caps. `capability_matches` *would* require `manage_mcp` / `mcp` / `integrations` / `mcp_call` — but court never reaches that layer.
- **Bypass:** A process created with `capabilities: ["file_read"]` can still court-allow `mcp_github_search` or a destructive mounted tool if the model emits the name (hallucination, skill inject, stale schema). `test_court_authority.test_mcp_prefix_allow_and_user_deny` encodes this as intended.
- **Half-migrated?** Catalog and mediate are stricter than court. Unification collapsed the *Python override*, not the *capability hole*.
- **Suggested fix:** Keep the early user-deny/secret checks, then require `capability_matches(name, proc.caps)` (or an explicit mounted-tool set the host refreshes). Treat unknown `mcp_*` as `ask` or `deny`. Do not return allow with `proc == None` unless a mount table says the tool exists.

#### H2. Host `CourtPolicy` is one global last-writer-wins object (extra_roots / workspace / profile race)

- **Where:** `crates/tevarn-kernel/src/kernel.rs` `court_policy` field + `set_court_policy` / `decide_tool` (~173, 1869–1911); `crates/tevarn-kernel-host/src/main.rs` `set_court_policy` (~985–1021); Python sync `backend/kernel/permission_court.py` ~254–328.
- **What:** Every `decide_tool` from Python first `set_court_policy` with *this run’s* `workspace_root` + `extra_roots` (ContextVar `get_run_extra_roots()` + `host_data_roots()`). Two sessions on two TCP connections interleave `set_court_policy` / `decide_tool` because each RPC is a separate `spawn_blocking`. Session A can be judged under Session B’s workspace and extra roots.
- **Effect:** False deny (A’s attachment root disappeared) or false allow (A reads B’s extra root). This is the extra_roots “collapse” working at the wrong granularity.
- **Suggested fix:** Pass `workspace_root` / `extra_roots` / `profile` as **per-call** `decide_tool` params (or a session-keyed policy map). Stop mutating a process-global policy on the hot path. Add a concurrent two-session test in `test_court_authority.py`.

#### H3. Steward layer trusts caller-supplied `_workforce` + `_identity_capabilities`

- **Where:** `crates/tevarn-kernel/src/court.rs` ~441–470, `identity_caps` ~655–666; `backend/kernel/permission_court.py` ~234–244; `backend/kernel/tool_gate.py` `_INTERNAL_ARG_DROP` (~52–67) — does **not** drop `_workforce` or `_identity_capabilities`.
- **What:** If `_workforce` is true (or `_agent_key` starts with `wf:`) and `_identity_capabilities` is a non-empty list, steward allow/deny happens *before* process capability and confirmation. Python re-copies those keys from the original args after sanitize.
- **Bypass:** A model-controlled tool JSON that includes `"_workforce": true` and `"_identity_capabilities": ["*"]` (or `command`) can allow write/command without `ask` and without matching the kernel process token. Secret floor and path still apply.
- **Suggested fix:** Drop `_workforce` / `_identity_capabilities` / `_agent_key` in `sanitize_args_for_kernel` unless the server overwrites them from Identity/DB after bind. Court should read caps from `AgentProcess` / identity cache only.

#### H4. Write/command confirmation lists are incomplete (`file_edit`, `remote_exec`, `shell_session`)

- **Where:** `crates/tevarn-kernel/src/court.rs` `WRITE_TOOLS` / `COMMAND_TOOLS` (~146–161); catalog `tool_catalog.rs` maps `file_edit` → `file_rw`, `remote_exec` → `command`; Python `tool_gate.py` `CHILD_PROC_TOOLS` includes `remote_exec` and `shell_session`.
- **What:** With a process that has `file_rw`, `file_edit` passes capability and **does not** enter the ask/session-grant branch. `remote_exec` / `shell_session` are not command-class in Rust court. Session grants therefore cannot be the authority for those names.
- **Suggested fix:** Align the two static lists with `TOOL_TO_CREW_CAP` + `CHILD_PROC_TOOLS`. Add unit tests: `file_edit` → `ask` unless grant store has `file_edit`; `remote_exec` → `ask`.

#### H5. Isolation OS children have no host-side watchdog; reap is dispatcher-best-effort (600s default)

- **Where:** `crates/tevarn-kernel/src/isolation.rs` `spawn_os` (~276–304), `reap_tick` (~516–578); `kernel.rs` `isolation_reap` default `max_age_secs=600` (~2048–2050); `backend/kernel/dispatcher.py` `_rust_tick_hooks` (~314–330).
- **What:** Real children are only created by `isolation_spawn_os`. Host has no background reap loop. Python dispatcher calls `isolation_reap` on tick with `max(inbox_timeout, 60)`. If the dispatcher is idle, wedged, or the backend is a reused detached process without ticks, a hung `sh -c` child (stdout/stderr already nulled) can run until 600s or forever.
- **Chat stop:** Stop/end UX lives in Python/WebSocket (`backend/api/websocket.py` partial snapshots). Kernel `end_process` *does* `isolation.drop_process` + LLM lease release + child process reclaim — but only if Python actually `end_process`es the kernel pid. A “stop generating” that only cancels the asyncio task does not, by itself, SIGKILL an `isolation_spawn_os` child.
- **Suggested fix:** Host-side interval reap (e.g. 5s) independent of Python. Per-handle timeout at spawn. On `end_process` / scheduler_cancel, document and test that OS children die. Wire chat-stop to `isolation_kill` / `reclaim_process_tree` for the run’s pid.

#### H6. Relative paths skip the Rust workspace check; court and Python FS gate can disagree

- **Where:** `crates/tevarn-kernel/src/court.rs` `is_outside_workspace` (~775–790): non-absolute paths return `false` (treated as inside). `extract_path` (~590–636) `file://` rewriting is easy to get wrong on Unix vs Windows. Actual IO is `backend/services/tools/executors.py` `_resolve_workspace_path` / `_is_within` (symlink-aware).
- **What:** Unification moved extra_roots into Rust so `path:workspace` is not a false deny. Rust still does not join relative paths to `workspace_root` before the check. Python `normalize_tool_path_args` tries to make in-workspace absolutes relative so Rust will not deny them. Enforcement of “cannot read `/etc`” for relative `../../etc/passwd` is `is_path_escape` (ParentDir components) plus Python. A path key the court does not extract (`cwd` is extracted; some tools use other keys) never hits the path layer.
- **Suggested fix:** Canonicalize relative paths against `workspace_root` in Rust (same as Python `_is_within`). Add tests for `../`, symlink escape, and `file:///etc/passwd`. Keep extra_roots as additional *canonical* prefixes, not a reason to skip capability.

#### H7. kernel-ci on main is red: stale `orch_window_force` (Python ABI never reached)

- **Where:** `crates/tevarn-kernel/src/loop_guard.rs` window logic (~402–424, threshold **≥6 orch-heavy of last 8**); test `orch_window_force` (~816–843) still comments “3 orch rounds should trip”; CI run `32037918738` (2026-08-17, SHA `698ebe5`, same test present on `cfccbbb`). Workflow `.github/workflows/kernel-ci.yml`.
- **What:** `cargo test -p tevarn-kernel --all-targets` fails: `got status=allow`. Job exits 101 before host build + pytest. Failures on main go back through at least `4133b5f` and `0679671`; last green `kernel-ci` on main in the fetched window was `31388387697` (v0.4.1, 2026-08-10). `test_court_authority.py` is not in the pytest list.
- **This is not a loop_guard production bug.** Defaults were relaxed on purpose (`max_crew_total` 24, window 6/8). The test was not updated.
- **Suggested fix (separate PR):** Update the test to drive 6 orch-heavy rounds (or inject a tiny window). Add `test_court_authority.py` to kernel-ci. Do not “fix CI” in this report PR.

---

### Medium

#### M1. Session grants are dual-store; host has no TTL; `_session_grant` still exists in Rust court

- **Where:** `crates/tevarn-kernel/src/session_grants.rs` (comment: host map is authority, `decide_tool` should not depend on flags); `court.rs` still honors `_confirm_ok` / `_session_grant` (~515–564); `kernel.rs` `decide_tool` upgrades `ask` via store + `_session_id` (~1895–1909); Python `grant_store.py` persist + 7d TTL + `rehydrate_session_grants_to_kernel`; `tool_hooks.py` still sets `_session_grant` for the Python tail.
- **What:** Production RPC path strips the flags (`tool_gate.py`), so the leftover court flags are not a default bypass. Host store is in-memory: restart loses grants until Python rehydrates. Python TTL prune does not automatically `session_grant_clear` on the host. `has_session_grant` still talks to both. Command signatures are `{tool}:{argv0}` — granting `command:rm` is not `command:npm`, which is good; granting whole-tool `command` is still a session-wide shell allow.
- **Suggested fix:** Delete `_session_grant` / `_confirm_ok` branches from `court.rs`. TTL-prune the host store (or make host the only store and persist there). On Python prune/delete session, always RPC `session_grant_clear`.

#### M2. `path_matches` final `p.contains(&g)` is a substring deny/allow

- **Where:** `crates/tevarn-kernel/src/court.rs` ~700–749.
- **What:** User deny pattern `tmp` or `id` matches any path containing that token. Secret globs like `**/*secret*` also collapse to `contains("secret")`. Can over-deny (`aside.md`) or surprise-allow via `user_allow` wildcards (`file_*` is prefix, which is OK; `*` allowlists every tool).
- **Suggested fix:** Use component-aware glob (or the same matcher as Python). Add tests for short tokens.

#### M3. Token/context accounting is split; Context VM is unused by the chat loop

- **Where:** Process budget `process.rs` `charge_tokens`; kernel `charge_tokens_locked` (~791–897) + host idempotency cache (`CHARGE_IDEM`, 300s, 4096 keys); `loop_guard` `budget_check` (force final at 85%); `context_vm.rs` default quota 32k tokens, isolation `process|identity|shared`.
- **What:** Soft renew is off by default (good). `charge_tokens_locked` uses `unwrap()` on process after a clone check (~821, 848) — safe today because the write lock is held, brittle if charge is ever split. Context VM `swap_in` also `unwrap()`s after a get (~257, 262). Chat token_used / file_read windows live in Python (`FILE_READ_DEFAULT_LIMIT = 2000`, `FILE_READ_MAX_CHARS = 96_000` in `backend/services/tools/executors.py`). Rust loop_guard `max_file_reads` (default 80, role-scaled) is a *count* cap, not a window.
- **Suggested fix:** Keep charging under one lock or use entry API without unwrap. Treat Context VM as non-authoritative until the loop actually `context_swap_*`s, or document it as unused.

#### M4. Glob / cwd are Python-only; court sees `cwd` as a path key

- **Where:** Brace expansion `backend/services/tools/executors.py` `_expand_glob_brace_sets`; command default cwd = workspace (`core_tools.py` CommandTool). Court `extract_path` includes `cwd` / `working_directory`.
- **What:** 1787e488 / d38686d look intact (default 2000-line file_read, brace glob, tests in `backend/tests/test_glob_brace_and_search_root.py`). Rust does not re-implement glob. If command args include an absolute `cwd` outside workspace and extra_roots, court should `path:workspace` deny — good. Relative `cwd` skips Rust (H6).
- **Suggested fix:** None in Rust beyond H6 canonicalize. Keep glob tests in backend-ci (out of scope here).

#### M5. Host bind + RPC secret are local-trust; listen address is configurable

- **Where:** `tevarn-kernel-host` `--listen` / `TEVARN_KERNEL_HOST` default `127.0.0.1:17890`; `check_rpc_auth` (~2714–2740) default-deny except `ping/health/list_methods/abi_version`; secret from env or `~/.tevarn/rpc.secret` with Takton soft-migrate.
- **What:** Constant-time compare is present. Binding `0.0.0.0` plus a readable home secret is a LAN RPC surface. Public methods need no auth (info leak only). Aligns with threat model if bind stays loopback.
- **Suggested fix:** Refuse non-loopback binds unless an explicit flag is set. Do not treat this as a remote-unauth hole on default desktop.

#### M6. Library-path `unwrap` / `expect` (non-test)

| Location | Risk |
|---|---|
| `kernel.rs` `charge_tokens_locked` process `unwrap` after get | Panic if pid vanishes under the same write lock (should not; still a landmine). |
| `loop_guard.rs` / `policy.rs` / `collab.rs` `ensure()` → `get_mut().unwrap()` | Same insert-then-get pattern; panic only on logic bug. |
| `skill_gate.rs` activate/rollback `get_mut().unwrap()` | After key proven present; OK. |
| `identity_cache.rs` `get_mut().unwrap()` after id lookup | OK. |
| `context_vm.rs` `swap_in` unwraps | Logic bug → panic on RPC. |
| `package_mgr.rs` `sign_hmac` `expect("hmac key")` | `HmacSha256::new_from_slice` fails on empty key → panic on `pkg_sign`. |
| `inbox.rs` `list` `partial_cmp(...).unwrap()` | NaN `created_at` panics. Use `unwrap_or(Equal)` like `kernel.rs`. |
| `audit.rs` `lock.lock().unwrap_or_else(into_inner)` | Poison-tolerant; good. |
| Host `serde_json::to_string` unwrap_or fallback | Good. |

- **Suggested fix:** Replace remaining library-path unwraps with `ok_or` / `expect` that names the invariant, or `unwrap_or` for sorts. Never `expect` on HMAC key — return `KernelError::Invalid`.

#### M7. Docs and ABI list are half-migrated

- **Where:** `docs/KERNEL_RUST.md` still says “permission_court 完整 path/steward 层 ⏳ 仍 Python tool_hooks” and “LLM admission ⏳ Python”. Host already serves `decide_tool`, `llm_*`, session grants. `lib.rs` `ABI_METHODS` lists `"evolution_policy"` twice (~282, ~300).
- **Suggested fix:** Update KERNEL_RUST.md to match H2 table vs court unification. Deduplicate ABI method list.

#### M8. Loop-guard dead heuristic and stale test (see H7)

- **Where:** `loop_guard.rs` `post_tool` computes `looks_trunc` then `_ = looks_trunc` (~603–620). Truncation tracking is explicit-flag only (`truncated` from Python).
- **What:** Idle-loop / file-read slice-thrash is guarded by `truncated_reread_blocked` + `max_file_reads` + orch window. The unused length-band heuristic is not a leak; it is unfinished. Thinking leaks are **not** in Rust — `backend/agent/user_channel.py` + `thinking_format.strip_thinking` (commit `758dc70`). No kernel thinking channel found.
- **Suggested fix:** Either use `looks_trunc` or delete it. Keep thinking strip in Python.

---

### Low

#### L1. `jwt_fp` detached-backend reuse is Electron/Python, not kernel — and looks correct

- **Where:** `electron/main.ts` `isReusableTevarnBackend` / `jwtFingerprint` (~376–414); `backend/api/runtime_identity.py` `jwt_fp` / `can_reuse_detached_backend`.
- **What:** Reuse requires `ok`, `product == tevarn-aios`, matching 16-hex SHA-256 of JWT secret, and role `fastapi_backend` or `control_plane`. Lying `kernel_host` role is rejected. Out of Rust scope; recorded because it shipped in the same unification commit.

#### L2. Chat stop / partial replies are WebSocket snapshots, not kernel

- **Where:** `backend/api/websocket.py` `partial_content` cap + flush; `backend/core/config.py` run-snapshot persist.
- **What:** `4133b5f` / `cfccbbb` intended “keep partial on stop.” Kernel `end_process` is a clean reclaim (caps cleared, children killed in-table, LLM leases released). No Rust bug found. Residual risk is H5 (OS child not tied to stop).

#### L3. `capability:compat` when `capabilities` is `None`

- **Where:** `court.rs` `decide_capability` ~279–293; `create_process` `require_intent` default true (`host` `--require-intent`).
- **What:** Compat allow is gated by intent synthesis in production. DEV_UNSAFE / explicit None caps still full-allow at capability layer. Threat model already lists this.

#### L4. Isolation `spawn()` is ledger-only (good); `spawn_os` still `sh -c` / `cmd /C`

- **Where:** `isolation.rs` comments + `build_command`.
- **What:** Avoids double-exec. Free-form shell is the product. Env scrub drops `*_SECRET` keys. Not a defect for this OS.

---

## Court / authority consistency (direct answers)

| Mechanism | Collapsed into host court? | Consistent? | Bypassable? | Half-migrated? |
|---|---|---|---|---|
| `extra_roots` | Yes, `CourtPolicy.extra_roots` | Path-only (not a full allow) — good | Global policy race (H2); relative paths skip Rust (H6) | Python still owns real FS `_is_within` |
| `mcp_*` | Yes, `allow_mcp_prefix` | Court **looser** than catalog | Yes — prefix allow before caps (H1) | Catalog still requires `manage_mcp` |
| Session grants | Yes, `SessionGrantStore` + RPC | Ask→allow on host is correct | Flags remain in `court.rs` but stripped on RPC (M1); missing tools skip ask (H4) | Python disk TTL + host RAM |
| Steward / identity caps | Args + process caps | Process caps OK | Forged `_workforce` + `_identity_capabilities` (H3) | Python re-injects those keys |
| User deny/allow / secret floor | Yes | First layers; no `name.contains` for tools | Substring path globs (M2) | Python fallback locked in production |
| Python court tail | Fail-closed when host up | Matches docs | `TEVARN_DEV_UNSAFE` / `agent_court_rust_required=false` | Intentional |

**Verdict:** Unification removed the old Python *overrides* (`path:extra_roots`, `mcp:override_rust_deny`) and is not a no-op. It is **half-migrated**: one global policy, MCP prefix as a cap bypass, incomplete risk lists, and a second grant store.

---

## Process lifecycle (start / stop / end)

| Step | Rust | Notes |
|---|---|---|
| Start / `create_process` | Token + intent + resource accounts | `require_intent` default readonly if caps omitted |
| Run gate / LLM admission | Queues, 300s wait timeout, `cancel_wait` releases `in_flight` | Cancel-deadlock fix in `llm_admission.rs` looks solid |
| Chat stop | Not a kernel method | Partial text is Python WS; tool stop is Python (`cfccbbb`) |
| `end_process` | Terminal state, clear caps, reclaim children, drop isolation, release LLM, cancel scheduler, run_gate release | Good control-plane hygiene |
| Detached backend reuse | N/A | Electron `jwt_fp` match (L1) |
| Hung model | No kernel watchdog | Admission timeout is *queue wait*, not generation wall time |
| Hung tool | Isolation reap 600s if dispatcher ticks | H5 |

---

## Tool dispatch, cancellation, timeout, hang

Rust does **not** execute ordinary tools. It mediates, charges `tool_calls`, and optionally owns OS children via `isolation_spawn_os`.

| Concern | Behavior on `cfccbbb` |
|---|---|
| Dispatch | JSON-RPC line protocol; one `handle_method` per line; TCP uses 30s timeout |
| Cancel tool | `isolation_kill` / `scheduler_cancel_process` / `llm_cancel_wait` / `wasm_kill` (status flag, not a running-engine abort unless invoke returns) |
| Timeout | RPC 30s (TCP only); isolation reap default 600s; run/LLM grant wait 300s; command timeout is Python (default 120, max 600) |
| Model hang | Python stream cancel; kernel lease can remain until `end_process` / `llm_release` / expire_stale(600s) |
| Tool hang | Python executor timeout; OS child may outlive chat stop (H5) |
| WASM hang | Fuel meter helps CPU loops; lock held (C1); no epoch interrupt observed as used |

---

## Token / context / file_read / glob / idle-loop

| Topic | Rust | Python (for completeness) | Leak still present? |
|---|---|---|---|
| Token budget | Hard cap; soft renew off by default; idempotent charge | Copies usage onto `agent_runs` | No kernel leak found |
| Context VM | Quota + swap + isolation | Chat loop does not appear to drive it | Unused, not leaky |
| file_read window | Count cap + truncated re-read block | 2000 lines / 96k chars | Slice-thrash look **fixed** in executors |
| glob / cwd | `cwd` is a court path key | Brace expand + workspace walk | Brace bug **fixed** in Python |
| Idle-loop / orch thrash | 6/8 window, crew caps, role bans | Consults loop_guard RPCs | Production relaxed; **test stale** (H7) |
| Thinking | None | `strip_thinking` / user channel | Not a kernel issue |

---

## Concurrency

- **Lock:** Single `RwLock<KernelInner>` (parking_lot). Sync API. Host correctly uses `spawn_blocking` so Tokio workers are not blocked by mediate — but **all** kernel mutations share one write lock.
- **Lock across await:** No `.await` while holding the lock (Rust kernel is sync). The equivalent bug is **lock across blocking I/O and WASM** (C1).
- **Channels:** IPC bus, domain events, JSON-RPC lines. Inbox/scheduler are in-memory maps under the same lock.
- **Shutdown races:** `end_process` cascades children then drops isolation/LLM. TCP connection drop does not end processes. Host process kill loses in-memory grants, inbox, isolation `Child` handles (OS children become orphans until OS reaps). Python `_resync_pending_to_rust` exists for inbox after `host_epoch` change.
- **Charge idempotency:** Separate `Mutex` map; `lock().ok()` swallows poison (skip idempotency rather than panic) — acceptable.

---

## kernel-ci (record only)

Latest fetched main run: [32037918738](https://github.com/wu1w/tevarn/actions/runs/32037918738) — **failure**, 34s.

```
---- loop_guard::tests::orch_window_force stdout ----
thread 'loop_guard::tests::orch_window_force' panicked at crates/tevarn-kernel/src/loop_guard.rs:833:9:
got Object {"process_id": String("p1"), "status": String("allow")}
test result: FAILED. 110 passed; 1 failed
```

Same failure on `4133b5f` (`32004377089`). Workflow never reaches `cargo build -p tevarn-kernel-host --release` or the Python ABI tests on current main. **Do not fix in this PR.**

---

## What looks solid

- **Ask → session grant on the host** (`kernel.rs` `decide_tool` + `session_grant_*` RPC + unit test `session_grant_upgrades_ask`). Extra roots do **not** become a write allow (`test_court_authority` / court unit `extra_roots_not_path_workspace_deny`).
- **Production court fail-closed:** host up + empty/error `decide_tool` → deny; Python tail locked without `DEV_UNSAFE` (`permission_court.py`, `docs/KERNEL_RUST.md` H-04 table).
- **Flag stripping:** `_confirm_ok` / `_session_grant` / `_tool_gate_passed` cannot ride HTTP/model JSON into Rust RPC (`tool_gate.py`).
- **RPC default-deny + constant-time secret** (`check_rpc_auth`); `_rpc_auth` stripped before handle.
- **`end_process` reclaim:** children, isolation, run_gate, scheduler, LLM `release_by_process` (commented max_in_flight leak is addressed).
- **`llm_cancel_wait`** clears `in_flight` when a grant was pending — documented fix for per-identity deadlock.
- **`isolation.spawn` ledger-only** vs `spawn_os` — avoids double-exec (H-01 closeout).
- **WASM fuel** on real Cranelift; tests assert fuel is consumed; fake modules fall back to a metered hostcall ledger.
- **Capability tokens:** monotonic `narrow`, optional HMAC decoupled via `TEVARN_TOKEN_HMAC_SECRET`.
- **Audit hash chain** + rotation + optional WORM; poison-tolerant file lock.
- **Loop-guard truncated re-read** and worker orch ban have working tests.
- **Electron `jwt_fp` reuse** is strict (product + role + fingerprint).
- **file_read / glob Python fixes** from mid-August still present (2000-line window, brace expansion, symlink-safe `_is_within`).
- **Thinking** stripped on the user channel (not a kernel leak).
- Host comments show the team already hit “LISTEN up, accept starved” and added a blocking pool + timeout — the remaining gap is cancellation (C1), not unawareness.

---

## Suggested fix order (follow-up PRs, not this one)

1. **C1** — unlock before WASM / audit I/O; make dispatch timeout meaningful.
2. **H1 + H3 + H4** — court must not allow `mcp_*` or steward-forged caps without process authority; complete write/command lists.
3. **H2** — per-call or per-session court policy (this is the extra_roots race).
4. **H5** — host reap loop + stop → kill OS children.
5. **H7** — retarget `orch_window_force`; add `test_court_authority.py` to kernel-ci.
6. **H6 + M1 + M2 + M6** — canonicalize paths; delete flag branches; tighten globs; remove library unwraps.

---

## Out of scope / not claimed

- Did not run `cargo test` or pytest in this environment (CI logs used for kernel-ci).
- Did not treat Electron/Python chat UX bugs as kernel defects except where they fail to call kernel reclaim.
- Did not audit `mobile/crates/**` (separate engine).
- Did not change CI or product code.
