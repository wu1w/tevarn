//! Tevarn Kernel Host — line-delimited JSON-RPC 2.0 over TCP or stdio.
//!
//! Protocol (one JSON object per line):
//! ```json
//! {"jsonrpc":"2.0","id":1,"method":"create_process","params":{"identity":"main"}}
//! {"jsonrpc":"2.0","id":1,"result":{...}}
//! {"jsonrpc":"2.0","id":2,"error":{"code":-32000,"message":"...","data":{...}}}
//! ```

use std::collections::{BTreeMap, HashMap};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::Context;
use clap::Parser;
use serde_json::{json, Value};
use tevarn_kernel::{
    catalog_as_json, AgentKernel, CapabilityToken, IntentDeclaration, KernelConfig,
    LlmAdmissionConfig, LlmLeaseRequest, SoftRenewConfig, ABI_METHODS, ABI_VERSION,
    VERSION as KERNEL_VERSION,
};
use tevarn_runtime::{init_runtime, Runtime, RuntimeConfig, VERSION as RUNTIME_VERSION};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tracing::{info, warn};

#[derive(Parser, Debug)]
#[command(name = "tevarn-kernel-host", about = "Tevarn Rust Kernel Host")]
struct Args {
    /// Listen address (TCP). Empty + --stdio = stdio mode.
    #[arg(long, env = "TEVARN_KERNEL_HOST", default_value = "127.0.0.1:17890")]
    listen: String,

    /// Use stdin/stdout instead of TCP.
    #[arg(long)]
    stdio: bool,

    /// Disable audit JSONL persist.
    #[arg(long)]
    no_audit: bool,

    /// Audit JSONL path.
    #[arg(long, env = "TEVARN_KERNEL_AUDIT_PATH")]
    audit_path: Option<PathBuf>,

    /// Disable soft budget renew (default is already off — hard-budget first).
    #[arg(long)]
    no_soft_renew: bool,

    /// Enable soft budget renew (coding path usually leaves this off).
    #[arg(long, env = "TEVARN_KERNEL_SOFT_RENEW")]
    soft_renew: bool,

    /// Soft renew max times when enabled (product cap for day-use: 2).
    #[arg(long, env = "TEVARN_KERNEL_SOFT_RENEW_MAX", default_value = "2")]
    soft_renew_max: i32,

    /// P0-B: require intent/default readonly when caps omitted (`true`/`false`).
    #[arg(long, env = "TEVARN_KERNEL_REQUIRE_INTENT", default_value = "true")]
    require_intent: String,

    #[arg(long, env = "TEVARN_AIOS_PROFILE", default_value = "aios-dev")]
    profile: String,
}

fn build_runtime(args: &Args) -> Arc<Runtime> {
    // Hard-budget first: soft renew only if --soft-renew / env, and never if --no-soft-renew.
    let soft_enabled = args.soft_renew && !args.no_soft_renew;
    let soft = SoftRenewConfig {
        enabled: soft_enabled,
        max_renew: args.soft_renew_max.clamp(0, 12),
        ..Default::default()
    };
    // Default require_intent=true (P0-B).
    let ri = args.require_intent.trim().to_ascii_lowercase();
    let require_intent = !(ri == "0" || ri == "false" || ri == "no" || ri == "off");
    // HMAC：与 Python signing 同源（env TEVARN_TOKEN_HMAC_SECRET / TEVARN_JWT_SECRET）
    let hmac_key = std::env::var("TEVARN_TOKEN_HMAC_SECRET")
        .ok()
        .filter(|s| s.trim().len() >= 16)
        .or_else(|| {
            std::env::var("TEVARN_JWT_SECRET")
                .ok()
                .filter(|s| !s.trim().is_empty())
        })
        .map(|s| s.into_bytes());
    let kernel = KernelConfig {
        audit_path: args.audit_path.clone(),
        audit_persist: !args.no_audit,
        soft_renew: soft,
        hmac_key,
        require_intent,
    };
    init_runtime(RuntimeConfig {
        kernel,
        profile: args.profile.clone(),
        single_user: true,
    })
}

fn err_resp(id: Value, code: i64, message: impl Into<String>, data: Option<Value>) -> Value {
    let mut error = json!({"code": code, "message": message.into()});
    if let Some(d) = data {
        error["data"] = d;
    }
    json!({"jsonrpc": "2.0", "id": id, "error": error})
}

fn ok_resp(id: Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn map_err(e: tevarn_kernel::KernelError) -> (i64, String, Value) {
    let code = match e.kind() {
        "permission" => -32001,
        "budget_exceeded" => -32002,
        "capability_escalation" => -32003,
        "not_found" => -32004,
        "invalid" => -32005,
        _ => -32000,
    };
    (code, e.to_string(), e.to_json())
}

/// charge_tokens 幂等缓存：key → (deadline, remaining)
static CHARGE_IDEM: Mutex<Option<HashMap<String, (Instant, Option<i64>)>>> = Mutex::new(None);

fn charge_idem_get(key: &str) -> Option<Option<i64>> {
    let mut g = CHARGE_IDEM.lock().ok()?;
    let map = g.get_or_insert_with(HashMap::new);
    // 顺手清过期
    let now = Instant::now();
    map.retain(|_, (dl, _)| *dl > now);
    map.get(key).map(|(_, rem)| *rem)
}

fn charge_idem_put(key: &str, remaining: Option<i64>) {
    if let Ok(mut g) = CHARGE_IDEM.lock() {
        let map = g.get_or_insert_with(HashMap::new);
        map.insert(key.to_string(), (Instant::now() + Duration::from_secs(300), remaining));
        if map.len() > 4096 {
            let now = Instant::now();
            map.retain(|_, (dl, _)| *dl > now);
        }
    }
}

fn handle_method(kernel: &AgentKernel, runtime: &Runtime, method: &str, params: &Value) -> Result<Value, (i64, String, Value)> {
    match method {
        "abi_version" => Ok(json!({
            "abi": ABI_VERSION,
            "kernel": KERNEL_VERSION,
            "runtime": RUNTIME_VERSION,
        })),
        "list_methods" => Ok(json!({
            "methods": ABI_METHODS,
            "abi": ABI_VERSION,
        })),
        "ping" => Ok(json!({"pong": true, "runtime": runtime.health()})),
        "health" => Ok(runtime.health()),

        "create_process" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let session_id = params.get("session_id").and_then(|v| v.as_str());
            let parent_id = params.get("parent_id").and_then(|v| v.as_str());
            let capabilities = params.get("capabilities").and_then(|v| {
                if v.is_null() {
                    None
                } else {
                    v.as_array().map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_str().map(|s| s.to_string()))
                            .collect()
                    })
                }
            });
            let token_budget = params.get("token_budget").and_then(|v| v.as_i64());
            let meta = params.get("meta").and_then(|v| v.as_object()).map(|m| {
                m.iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect::<BTreeMap<_, _>>()
            });
            let intent = params
                .get("intent")
                .filter(|v| !v.is_null())
                .map(IntentDeclaration::from_dict)
                .transpose()
                .map_err(map_err)?;
            kernel
                .create_process_with_intent(
                    identity,
                    session_id,
                    parent_id,
                    capabilities,
                    token_budget,
                    meta,
                    intent,
                )
                .map(|p| p.to_dict())
                .map_err(map_err)
        }

        "end_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let state = params
                .get("state")
                .and_then(|v| v.as_str())
                .unwrap_or("completed");
            let reason = params.get("reason").and_then(|v| v.as_str());
            kernel
                .end_process(pid, state, reason)
                .map(|p| p.map(|x| x.to_dict()).unwrap_or(Value::Null))
                .map_err(map_err)
        }

        "mark_running" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel.mark_running(pid).map(|_| json!({"ok": true})).map_err(map_err)
        }

        "suspend_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let reason = params.get("reason").and_then(|v| v.as_str()).unwrap_or("");
            kernel
                .suspend_process(pid, reason)
                .map(|p| p.to_dict())
                .map_err(map_err)
        }

        "resume_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel
                .resume_process(pid)
                .map(|p| p.to_dict())
                .map_err(map_err)
        }

        "get_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(kernel
                .get_process(pid)
                .map(|p| p.to_dict())
                .unwrap_or(Value::Null))
        }

        "list_processes" => {
            let include_terminal = params
                .get("include_terminal")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let procs: Vec<_> = kernel
                .list_processes(include_terminal)
                .into_iter()
                .map(|p| p.to_dict())
                .collect();
            Ok(json!({"processes": procs, "total": procs.len()}))
        }

        "live_processes_for_identity" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let procs: Vec<_> = kernel
                .live_processes_for_identity(identity)
                .into_iter()
                .map(|p| p.to_dict())
                .collect();
            Ok(json!({"processes": procs, "total": procs.len()}))
        }

        "mediate" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let action = params
                .get("action")
                .and_then(|v| v.as_str())
                .unwrap_or("tool_call");
            let target = params
                .get("target")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args = params.get("args");
            kernel
                .mediate(pid, action, target, args)
                .map(|d| {
                    json!({
                        "allowed": d.allowed,
                        "reason": d.reason,
                        "capability_checked": d.capability_checked,
                    })
                })
                .map_err(map_err)
        }

        "charge_tokens" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let amount = params
                .get("amount")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            // 幂等：同 idempotency_key 5 分钟内只扣一次（防 RPC 超时双写）
            let idem = params
                .get("idempotency_key")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .filter(|s| !s.is_empty());
            if let Some(ref k) = idem {
                let cache_key = format!("{pid}:{k}");
                if let Some(rem) = charge_idem_get(&cache_key) {
                    return Ok(json!({"remaining": rem, "idempotent": true}));
                }
            }
            let out = kernel
                .charge_tokens(pid, amount)
                .map(|r| {
                    if let Some(ref k) = idem {
                        charge_idem_put(&format!("{pid}:{k}"), r);
                    }
                    json!({"remaining": r})
                })
                .map_err(map_err);
            out
        }

        "top_up_budget" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let amount = params
                .get("amount")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("ceo");
            let reason = params.get("reason").and_then(|v| v.as_str()).unwrap_or("");
            kernel
                .top_up_budget(pid, amount, by, reason)
                .map_err(map_err)
        }

        "try_soft_renew_budget" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let need = params.get("need").and_then(|v| v.as_i64()).unwrap_or(0);
            let reason = params
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("soft_renew");
            Ok(kernel
                .try_soft_renew_budget(pid, need, reason)
                .unwrap_or(Value::Null))
        }

        "issue_token" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let capabilities = params.get("capabilities").and_then(|v| {
                v.as_array().map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
            });
            let expires_at = params.get("expires_at").and_then(|v| v.as_f64());
            kernel
                .issue_token(pid, capabilities, expires_at)
                .map(|t| t.to_dict(None))
                .map_err(map_err)
        }

        "request_escalation" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let caps = params
                .get("capabilities")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let reason = params.get("reason").and_then(|v| v.as_str()).unwrap_or("");
            kernel
                .request_escalation(pid, caps, reason)
                .map(|r| r.to_dict())
                .map_err(map_err)
        }

        "approve_escalation" => {
            let id = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("user");
            kernel
                .approve_escalation(id, by)
                .map(|r| r.to_dict())
                .map_err(map_err)
        }

        "deny_escalation" => {
            let id = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("user");
            kernel
                .deny_escalation(id, by)
                .map(|r| r.to_dict())
                .map_err(map_err)
        }

        "list_escalations" => {
            let status = params.get("status").and_then(|v| v.as_str());
            let list: Vec<_> = kernel
                .list_escalations(status)
                .into_iter()
                .map(|r| r.to_dict())
                .collect();
            Ok(json!({"escalations": list}))
        }

        "get_escalation" => {
            let id = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            Ok(kernel
                .get_escalation(id)
                .map(|r| r.to_dict())
                .unwrap_or(Value::Null))
        }

        "events" => {
            let process_id = params.get("process_id").and_then(|v| v.as_str());
            let kind = params.get("kind").and_then(|v| v.as_str());
            let limit = params
                .get("limit")
                .and_then(|v| v.as_u64())
                .unwrap_or(200) as usize;
            let list: Vec<_> = kernel
                .events(process_id, kind, limit)
                .into_iter()
                .map(|e| e.to_dict())
                .collect();
            Ok(json!({"events": list}))
        }

        "verify_event_chain" => {
            let (ok, idx) = kernel.verify_event_chain();
            Ok(json!({"ok": ok, "break_index": idx}))
        }

        "gc_terminal" => {
            let older = params
                .get("older_than_seconds")
                .and_then(|v| v.as_f64())
                .unwrap_or(3600.0);
            Ok(json!({"removed": kernel.gc_terminal(older)}))
        }

        "retire_live_identity_processes" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let reason = params
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("superseded by new job");
            let except = params.get("except_process_id").and_then(|v| v.as_str());
            Ok(json!({
                "killed": kernel.retire_live_identity_processes(identity, reason, except)
            }))
        }

        "resource_charge" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let kind = params
                .get("kind")
                .and_then(|v| v.as_str())
                .unwrap_or("tool_calls");
            let amount = params
                .get("amount")
                .and_then(|v| v.as_i64())
                .unwrap_or(1);
            kernel
                .resource_charge(pid, kind, amount)
                .map(|r| json!({"remaining": r}))
                .map_err(map_err)
        }

        "resource_release" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let kind = params
                .get("kind")
                .and_then(|v| v.as_str())
                .unwrap_or("child_proc");
            let amount = params
                .get("amount")
                .and_then(|v| v.as_i64())
                .unwrap_or(1);
            kernel
                .resource_release(pid, kind, amount)
                .map_err(map_err)
        }

        "resource_usage" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(kernel.resource_usage(pid))
        }
        "resource_report_rss" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let rss = params
                .get("rss_bytes")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            kernel.resource_report_rss(pid, rss).map_err(map_err)
        }

        "scheduler_submit" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            let priority = params
                .get("priority")
                .and_then(|v| v.as_i64())
                .unwrap_or(10) as i32;
            Ok(kernel.scheduler_submit(pid, payload, priority))
        }

        "scheduler_next" => Ok(kernel.scheduler_next().unwrap_or(Value::Null)),
        "scheduler_stats" => Ok(kernel.scheduler_stats()),
        "scheduler_set_limits" => {
            let max_r = params
                .get("max_running")
                .and_then(|v| v.as_u64())
                .unwrap_or(16) as u32;
            let max_s = params
                .get("max_per_session")
                .and_then(|v| v.as_u64())
                .unwrap_or(2) as u32;
            Ok(kernel.scheduler_set_limits(max_r, max_s))
        }

        "scheduler_complete" => {
            let task_id = params
                .get("task_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "task_id required".into(), json!({})))?;
            let cancelled = params
                .get("cancelled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            kernel.scheduler_complete(task_id, cancelled);
            Ok(json!({"ok": true}))
        }

        "scheduler_cancel_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(json!({"cancelled": kernel.scheduler_cancel_process(pid)}))
        }

        "capability_narrow" => {
            let parent = params
                .get("token")
                .ok_or((-32005, "token required".into(), json!({})))?;
            let tok = CapabilityToken::from_dict(parent, None).map_err(map_err)?;
            let subset: Vec<String> = params
                .get("subset")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let process_id = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let expires_at = params.get("expires_at").and_then(|v| v.as_f64());
            tok.narrow(subset, process_id, expires_at)
                .map(|t| t.to_dict(None))
                .map_err(map_err)
        }

        "synthesize_intent" => {
            let intent = IntentDeclaration::from_dict(params).map_err(map_err)?;
            let (granted, dropped) = tevarn_kernel::synthesize_capabilities(&intent);
            Ok(json!({"granted": granted, "dropped": dropped, "intent": intent.to_dict()}))
        }

        "apply_intent" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let intent_val = params
                .get("intent")
                .ok_or((-32005, "intent required".into(), json!({})))?;
            let intent = IntentDeclaration::from_dict(intent_val).map_err(map_err)?;
            let parent_token = params
                .get("parent_token")
                .filter(|v| v.is_object())
                .and_then(|v| CapabilityToken::from_dict(v, None).ok());
            kernel
                .apply_intent(pid, intent, parent_token)
                .map(|(tok, dropped)| {
                    json!({
                        "token": tok.to_dict(None),
                        "granted": tok.capabilities.iter().cloned().collect::<Vec<_>>(),
                        "dropped": dropped,
                        "process": kernel.get_process(pid).map(|p| p.to_dict()),
                    })
                })
                .map_err(map_err)
        }

        "synthesize_and_issue" => {
            // create_process + intent in one shot, or apply to existing process_id
            if let Some(pid) = params.get("process_id").and_then(|v| v.as_str()) {
                let intent_val = params
                    .get("intent")
                    .ok_or((-32005, "intent required".into(), json!({})))?;
                let intent = IntentDeclaration::from_dict(intent_val).map_err(map_err)?;
                kernel
                    .apply_intent(pid, intent, None)
                    .map(|(tok, dropped)| {
                        json!({
                            "process_id": pid,
                            "token": tok.to_dict(None),
                            "granted": tok.capabilities.iter().cloned().collect::<Vec<_>>(),
                            "dropped": dropped,
                            "process": kernel.get_process(pid).map(|p| p.to_dict()),
                        })
                    })
                    .map_err(map_err)
            } else {
                let identity = params
                    .get("identity")
                    .and_then(|v| v.as_str())
                    .unwrap_or("main");
                let intent_val = params
                    .get("intent")
                    .ok_or((-32005, "intent required".into(), json!({})))?;
                let intent = IntentDeclaration::from_dict(intent_val).map_err(map_err)?;
                let session_id = params.get("session_id").and_then(|v| v.as_str());
                let parent_id = params.get("parent_id").and_then(|v| v.as_str());
                let token_budget = params
                    .get("token_budget")
                    .and_then(|v| v.as_i64())
                    .or_else(|| intent.token_budget_hint());
                let meta = params.get("meta").and_then(|v| v.as_object()).map(|m| {
                    m.iter()
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect::<BTreeMap<_, _>>()
                });
                kernel
                    .create_process_with_intent(
                        identity,
                        session_id,
                        parent_id,
                        None,
                        token_budget,
                        meta,
                        Some(intent),
                    )
                    .map(|p| {
                        json!({
                            "process_id": p.id,
                            "process": p.to_dict(),
                            "granted": p.capabilities,
                            "dropped": p.meta.get("intent_dropped"),
                            "token": p.token.as_ref().map(|t| t.to_dict(None)),
                        })
                    })
                    .map_err(map_err)
            }
        }

        "filter_tools" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let tools: Vec<String> = params
                .get("tools")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            kernel
                .filter_tools(pid, &tools)
                .map(|t| json!({"tools": t, "total": t.len()}))
                .map_err(map_err)
        }

        "tools_for_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel
                .tools_for_process(pid)
                .map(|t| json!({"tools": t, "unrestricted": t.is_none()}))
                .map_err(map_err)
        }

        "tool_catalog" => Ok(catalog_as_json()),

        "schedule_run" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            let class = params.get("priority_class").and_then(|v| v.as_str());
            let priority = params.get("priority").and_then(|v| v.as_i64()).map(|i| i as i32);
            Ok(kernel.schedule_run(pid, payload, class, priority))
        }

        "llm_try_acquire" => {
            let req = LlmLeaseRequest::from_dict(params);
            Ok(kernel.llm_try_acquire(req).to_dict())
        }

        "llm_poll" => {
            let rid = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            Ok(kernel.llm_poll(rid).to_dict())
        }

        "llm_release" => {
            let rid = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            Ok(json!({"ok": kernel.llm_release(rid)}))
        }

        "llm_cancel_wait" => {
            let rid = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            Ok(json!({"ok": kernel.llm_cancel_wait(rid)}))
        }

        "llm_release_by_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(json!({"released": kernel.llm_release_by_process(pid)}))
        }

        "llm_expire_stale" => {
            let max_hold = params
                .get("max_hold_secs")
                .and_then(|v| v.as_f64())
                .unwrap_or(600.0);
            Ok(json!({"expired": kernel.llm_expire_stale(max_hold)}))
        }

        "llm_reclaim" => {
            let null_max = params
                .get("null_pid_max_hold_secs")
                .and_then(|v| v.as_f64())
                .unwrap_or(120.0);
            let max_hold = params
                .get("max_hold_secs")
                .and_then(|v| v.as_f64())
                .unwrap_or(600.0);
            Ok(kernel.llm_reclaim(null_max, max_hold))
        }

        "llm_force_clear" => Ok(kernel.llm_force_clear()),

        "llm_charge_quota" => {
            let iid = params.get("identity_id").and_then(|v| v.as_str());
            let amount = params
                .get("amount")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            kernel.llm_charge_quota(iid, amount);
            Ok(json!({"ok": true}))
        }

        "llm_status" => Ok(kernel.llm_status()),

        "llm_set_config" => {
            let mut cfg = LlmAdmissionConfig::default();
            if let Some(v) = params.get("max_in_flight").and_then(|x| x.as_u64()) {
                cfg.max_in_flight = v as usize;
            }
            if let Some(v) = params.get("max_per_identity").and_then(|x| x.as_u64()) {
                cfg.max_per_identity = v as usize;
            }
            if let Some(v) = params.get("owner_reserve").and_then(|x| x.as_u64()) {
                cfg.owner_reserve = v as usize;
            }
            if let Some(v) = params.get("queue_max").and_then(|x| x.as_u64()) {
                cfg.queue_max = v as usize;
            }
            if let Some(v) = params.get("fairness_wait_weight").and_then(|x| x.as_f64()) {
                cfg.fairness_wait_weight = v;
            }
            if let Some(v) = params.get("daily_global").and_then(|x| x.as_i64()) {
                cfg.daily_global = v;
            }
            if let Some(v) = params.get("daily_identity").and_then(|x| x.as_i64()) {
                cfg.daily_identity = v;
            }
            kernel.llm_set_config(cfg);
            Ok(json!({"ok": true}))
        }

        "run_acquire" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel
                .run_acquire(pid)
                .map(|r| json!({"remaining": r}))
                .map_err(map_err)
        }

        "run_release" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel
                .run_release(pid)
                .map(|_| json!({"ok": true}))
                .map_err(map_err)
        }

        "run_gate_try" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let class = params.get("priority_class").and_then(|v| v.as_str());
            let priority = params
                .get("priority")
                .and_then(|v| v.as_i64())
                .map(|i| i as i32);
            Ok(kernel.run_gate_try(pid, class, priority).to_dict())
        }

        "run_gate_poll" => {
            let rid = params
                .get("request_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "request_id required".into(), json!({})))?;
            Ok(kernel.run_gate_poll(rid).to_dict())
        }

        "run_gate_release" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(json!({"ok": kernel.run_gate_release(pid)}))
        }

        "run_gate_status" => Ok(kernel.run_gate_status()),

        "run_gate_set_max" => {
            let n = params
                .get("max_concurrent")
                .or_else(|| params.get("max"))
                .and_then(|v| v.as_u64())
                .unwrap_or(4) as usize;
            kernel.run_gate_set_max(n);
            Ok(json!({"ok": true, "max_concurrent": n}))
        }

        "decide_tool" => {
            let name = params
                .get("name")
                .or_else(|| params.get("tool"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args = params.get("args").or_else(|| params.get("arguments"));
            let pid = params.get("process_id").and_then(|v| v.as_str());
            let skill_tools: Option<Vec<String>> = params
                .get("skill_tools")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                });
            let skill_deny: Option<Vec<String>> = params
                .get("skill_deny")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                });
            let emit = params
                .get("emit")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            let d = if emit {
                kernel.decide_tool_and_emit(
                    name,
                    args,
                    pid,
                    skill_tools.as_deref(),
                    skill_deny.as_deref(),
                )
            } else {
                kernel.decide_tool(
                    name,
                    args,
                    pid,
                    skill_tools.as_deref(),
                    skill_deny.as_deref(),
                )
            };
            Ok(d.to_audit())
        }

        "set_court_policy" => {
            let mut policy = kernel.court_policy();
            if let Some(v) = params.get("permission_enabled").and_then(|x| x.as_bool()) {
                policy.permission_enabled = v;
            }
            if let Some(v) = params.get("relax_secrets").and_then(|x| x.as_bool()) {
                policy.relax_secrets = v;
            }
            if let Some(v) = params.get("workspace_root").and_then(|x| x.as_str()) {
                policy.workspace_root = std::path::PathBuf::from(v);
            }
            if let Some(v) = params.get("profile").and_then(|x| x.as_str()) {
                policy.profile = v.to_string();
            }
            if let Some(arr) = params.get("user_deny").and_then(|x| x.as_array()) {
                policy.user_deny = arr
                    .iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect();
            }
            if let Some(arr) = params.get("user_allow").and_then(|x| x.as_array()) {
                policy.user_allow = arr
                    .iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect();
            }
            if let Some(arr) = params.get("extra_roots").and_then(|x| x.as_array()) {
                policy.extra_roots = arr
                    .iter()
                    .filter_map(|x| x.as_str().map(std::path::PathBuf::from))
                    .collect();
            }
            if let Some(v) = params.get("allow_mcp_prefix").and_then(|x| x.as_bool()) {
                policy.allow_mcp_prefix = v;
            }
            kernel.set_court_policy(policy);
            Ok(json!({"ok": true}))
        }

        "isolation_resolve" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let force = params.get("profile").and_then(|v| v.as_str());
            let is_wf = params
                .get("is_workforce")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            Ok(kernel.isolation_resolve(pid, force, is_wf))
        }

        "isolation_set_profile" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let profile = params
                .get("profile")
                .and_then(|v| v.as_str())
                .unwrap_or("interactive");
            kernel.isolation_set_profile(pid, profile);
            Ok(json!({"ok": true, "profile": profile}))
        }

        "isolation_spawn" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let command = params
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let backend = params
                .get("backend")
                .and_then(|v| v.as_str())
                .unwrap_or("local");
            kernel
                .isolation_spawn(pid, command, backend)
                .map_err(map_err)
        }
        "isolation_spawn_os" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let command = params
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let backend = params.get("backend").and_then(|v| v.as_str());
            // 强制过 court：任意 OS 命令必须留下裁决记录
            let args = json!({"command": command});
            let decision = kernel.decide_tool_and_emit("command", Some(&args), Some(pid), None, None);
            if decision.verdict != "allow" {
                return Err((
                    -32001,
                    format!(
                        "isolation_spawn_os denied by court: {} ({})",
                        decision.verdict, decision.reason
                    ),
                    json!({
                        "verdict": decision.verdict,
                        "layer": decision.layer,
                        "reason": decision.reason,
                    }),
                ));
            }
            kernel
                .isolation_spawn_os(pid, command, backend)
                .map_err(map_err)
        }
        "isolation_poll" => {
            let hid = params
                .get("handle_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "handle_id required".into(), json!({})))?;
            Ok(kernel.isolation_poll(hid))
        }
        "isolation_kill" => {
            let hid = params
                .get("handle_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "handle_id required".into(), json!({})))?;
            Ok(kernel.isolation_kill(hid).unwrap_or(Value::Null))
        }

        "isolation_complete" => {
            let hid = params
                .get("handle_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "handle_id required".into(), json!({})))?;
            let code = params
                .get("exit_code")
                .and_then(|v| v.as_i64())
                .unwrap_or(0) as i32;
            Ok(kernel
                .isolation_complete(hid, code)
                .unwrap_or(Value::Null))
        }
        "isolation_attach_pid" => {
            let hid = params
                .get("handle_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "handle_id required".into(), json!({})))?;
            let pid = params
                .get("os_pid")
                .or_else(|| params.get("pid"))
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32;
            Ok(kernel
                .isolation_attach_pid(hid, pid)
                .unwrap_or(Value::Null))
        }
        "isolation_reap" => {
            let max_age = params.get("max_age_secs").and_then(|v| v.as_f64());
            Ok(kernel.isolation_reap(max_age))
        }
        "isolation_status" => Ok(kernel.isolation_status()),

        "checkpoint_begin" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let path = params
                .get("path")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "path required".into(), json!({})))?;
            kernel.checkpoint_begin(pid, path).map_err(map_err)
        }

        "checkpoint_restore" => {
            let id = params
                .get("checkpoint_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "checkpoint_id required".into(), json!({})))?;
            kernel.checkpoint_restore(id).map_err(map_err)
        }

        "checkpoint_list" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(json!({"checkpoints": kernel.checkpoint_list(pid)}))
        }

        "export_decision_trail" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let limit = params
                .get("limit")
                .and_then(|v| v.as_u64())
                .unwrap_or(500) as usize;
            Ok(kernel.export_decision_trail(pid, limit))
        }

        // ── P0.5 ──────────────────────────────────────────
        "process_snapshot" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let meta = params.get("meta").cloned();
            kernel.process_snapshot(pid, meta).map_err(map_err)
        }
        "process_snapshot_latest" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.process_snapshot_latest(pid))
        }
        "process_snapshot_list" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(json!({"snapshots": kernel.process_snapshot_list(pid)}))
        }
        "process_recovery_plan" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.process_recovery_plan(pid))
        }
        "result_spill" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("system");
            let tool = params.get("tool").and_then(|v| v.as_str()).unwrap_or("tool");
            let content = params
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.result_spill(pid, tool, content))
        }
        "result_load" => {
            let id = params
                .get("handle_id")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "handle_id required".into(), json!({})))?;
            let pid = params
                .get("process_id")
                .or_else(|| params.get("caller_process_id"))
                .and_then(|v| v.as_str());
            kernel.result_load(id, pid).map_err(map_err)
        }
        "result_store_status" => Ok(kernel.result_store_status()),
        "iteration_set_budget" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let max = params
                .get("max_total")
                .or_else(|| params.get("max"))
                .and_then(|v| v.as_u64())
                .unwrap_or(25) as u32;
            kernel.iteration_set_budget(pid, max);
            Ok(json!({"ok": true, "max_total": max}))
        }
        "iteration_consume" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(kernel.iteration_consume(pid))
        }
        "iteration_refund" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            Ok(json!({"ok": kernel.iteration_refund(pid)}))
        }
        "iteration_status" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.iteration_status(pid))
        }
        "doom_record" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let tool = params.get("tool").or_else(|| params.get("name")).and_then(|v| v.as_str()).unwrap_or("");
            let args = params.get("args");
            Ok(kernel.doom_record(pid, tool, args))
        }
        "doom_reset" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel.doom_reset(pid);
            Ok(json!({"ok": true}))
        }
        "doom_status" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.doom_status(pid))
        }
        "loop_guard_configure" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let cfg = params
                .get("config")
                .cloned()
                .or_else(|| {
                    let mut m = params.clone();
                    if let Some(obj) = m.as_object_mut() {
                        obj.remove("process_id");
                    }
                    Some(m)
                })
                .unwrap_or_else(|| json!({}));
            Ok(kernel.loop_guard_configure(pid, &cfg))
        }
        "loop_guard_begin_round" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let names: Vec<String> = params
                .get("tool_names")
                .or_else(|| params.get("tools"))
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.loop_guard_begin_round(pid, &names))
        }
        "loop_guard_pre_tool" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let tool = params
                .get("tool")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args = params.get("args");
            Ok(kernel.loop_guard_pre_tool(pid, tool, args))
        }
        "loop_guard_post_tool" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let tool = params
                .get("tool")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args = params.get("args");
            let result_text = params.get("result").and_then(|v| v.as_str());
            let truncated = params.get("truncated").and_then(|v| v.as_bool());
            Ok(kernel.loop_guard_post_tool(pid, tool, args, result_text, truncated))
        }
        "loop_guard_budget_check" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let used = params.get("tokens_used").and_then(|v| v.as_i64());
            let budget = params.get("token_budget").and_then(|v| v.as_i64());
            Ok(kernel.loop_guard_budget_check(pid, used, budget))
        }
        "loop_guard_status" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.loop_guard_status(pid))
        }
        // Stateless progress helper — thrash arming for cargo failures
        "cargo_classify" => {
            let text = params
                .get("text")
                .or_else(|| params.get("result"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(tevarn_kernel::classify_cargo_error_json(text))
        }
        "policy_status" => Ok(kernel.policy_status()),
        "cache_record" => {
            let family = params
                .get("family")
                .or_else(|| params.get("provider"))
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            let hit = params.get("hit").and_then(|v| v.as_bool()).unwrap_or(false);
            let bytes = params
                .get("bytes_saved")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            let model = params.get("model").and_then(|v| v.as_str());
            Ok(kernel.cache_record(family, hit, bytes, model))
        }
        "cache_metrics" => Ok(kernel.cache_metrics()),
        "cost_charge" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("system");
            let family = params
                .get("family")
                .or_else(|| params.get("provider"))
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            let tokens = params
                .get("tokens")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            let billable = params
                .get("billable")
                .and_then(|v| v.as_u64())
                .unwrap_or(tokens);
            let model = params.get("model").and_then(|v| v.as_str());
            Ok(kernel.cost_charge(pid, family, tokens, billable, model))
        }
        "cost_panel" => Ok(kernel.cost_panel()),
        "cost_process" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.cost_process(pid))
        }
        "marathon_record" => {
            let kind = params
                .get("kind")
                .and_then(|v| v.as_str())
                .unwrap_or("attempt");
            let reason = params.get("reason").and_then(|v| v.as_str());
            Ok(kernel.marathon_record(kind, reason))
        }
        "marathon_metrics" => Ok(kernel.marathon_metrics()),
        "reclaim_process_tree" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let reason = params.get("reason").and_then(|v| v.as_str());
            Ok(kernel.reclaim_process_tree(pid, reason))
        }

        // ── P1-A IPC ──────────────────────────────────────
        "ipc_send" => {
            let from = params
                .get("from")
                .or_else(|| params.get("process_id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "from required".into(), json!({})))?;
            let to = params
                .get("to")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "to required".into(), json!({})))?;
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("message");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            kernel.ipc_send(from, to, kind, payload).map_err(map_err)
        }
        "ipc_recv" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let max = params.get("max").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
            kernel.ipc_recv(pid, max).map_err(map_err)
        }
        "ipc_status" => Ok(kernel.ipc_status()),
        "ipc_channel_subscribe" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let channel = params
                .get("channel")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "channel required".into(), json!({})))?;
            kernel
                .ipc_channel_subscribe(pid, channel)
                .map_err(map_err)
        }
        "ipc_channel_publish" => {
            let from = params
                .get("from")
                .or_else(|| params.get("process_id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "from required".into(), json!({})))?;
            let channel = params
                .get("channel")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "channel required".into(), json!({})))?;
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("message");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            kernel
                .ipc_channel_publish(from, channel, kind, payload)
                .map_err(map_err)
        }
        "ipc_broadcast" => {
            let from = params
                .get("from")
                .or_else(|| params.get("process_id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "from required".into(), json!({})))?;
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("message");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            kernel.ipc_broadcast(from, kind, payload).map_err(map_err)
        }
        "ipc_reply" => {
            let from = params
                .get("from")
                .or_else(|| params.get("process_id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "from required".into(), json!({})))?;
            let to = params
                .get("to")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "to required".into(), json!({})))?;
            let reply_to = params
                .get("reply_to")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "reply_to required".into(), json!({})))?;
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("reply");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            kernel
                .ipc_reply(from, to, reply_to, kind, payload)
                .map_err(map_err)
        }
        "multi_agent_demo" => kernel.multi_agent_demo().map_err(map_err),
        "eval_record" => {
            let suite = params
                .get("suite")
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            let overall = params
                .get("overall")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            let mut parts = std::collections::HashMap::new();
            if let Some(obj) = params.get("parts").and_then(|v| v.as_object()) {
                for (k, v) in obj {
                    if let Some(f) = v.as_f64() {
                        parts.insert(k.clone(), f);
                    }
                }
            }
            let meta = params.get("meta").cloned().unwrap_or(json!({}));
            Ok(kernel.eval_record(suite, overall, parts, meta))
        }
        "eval_trend" => {
            let suite = params
                .get("suite")
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            let last_n = params.get("last_n").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
            Ok(kernel.eval_trend(suite, last_n))
        }
        "eval_gate_check" => {
            let suite = params.get("suite").and_then(|v| v.as_str());
            Ok(kernel.eval_gate_check(suite))
        }
        "eval_status" => Ok(kernel.eval_status()),
        "agent_manifest_validate" => {
            if let Some(s) = params.get("json").and_then(|v| v.as_str()) {
                Ok(kernel.agent_manifest_validate_str(s))
            } else {
                let raw = params
                    .get("manifest")
                    .cloned()
                    .unwrap_or_else(|| params.clone());
                Ok(kernel.agent_manifest_validate(raw))
            }
        }
        "agent_sdk_checklist" => Ok(kernel.agent_sdk_checklist()),
        "skill_require_loadable" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            kernel.skill_require_loadable(name).map_err(map_err)
        }

        // ── P1-A services ─────────────────────────────────
        "service_register" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            let privilege = params
                .get("privilege")
                .and_then(|v| v.as_str())
                .unwrap_or("user");
            let meta = params.get("meta").cloned().unwrap_or(json!({}));
            Ok(kernel.service_register(name, privilege, meta))
        }
        "service_list" => Ok(kernel.service_list()),
        "service_health" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            let healthy = params
                .get("healthy")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            Ok(kernel.service_health(name, healthy))
        }
        "service_status" => Ok(kernel.service_status()),
        "sys_memory_put" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "identity required".into(), json!({})))?;
            let key = params
                .get("key")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "key required".into(), json!({})))?;
            let value = params.get("value").cloned().unwrap_or(Value::Null);
            Ok(kernel.sys_memory_put(identity, key, value))
        }
        "sys_memory_get" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let key = params.get("key").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.sys_memory_get(identity, key))
        }
        "sys_memory_list" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.sys_memory_list(identity))
        }
        "sys_notify_push" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("system");
            let level = params.get("level").and_then(|v| v.as_str()).unwrap_or("info");
            let title = params.get("title").and_then(|v| v.as_str()).unwrap_or("");
            let body = params.get("body").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.sys_notify_push(pid, level, title, body))
        }
        "sys_notify_list" => {
            let pid = params.get("process_id").and_then(|v| v.as_str());
            let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            Ok(kernel.sys_notify_list(pid, limit))
        }
        "sys_notify_ack" => {
            let id = params
                .get("id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "id required".into(), json!({})))?;
            Ok(kernel.sys_notify_ack(id))
        }

        // ── P1-A identity / inbox ─────────────────────────
        "identity_cache_put" => {
            let data = params
                .get("identity")
                .cloned()
                .or_else(|| Some(params.clone()))
                .unwrap_or(json!({}));
            kernel.identity_cache_put(data).map_err(map_err)
        }
        "identity_cache_get" => {
            let key = params
                .get("id")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.identity_cache_get(key))
        }
        "identity_cache_list" => Ok(kernel.identity_cache_list()),
        "identity_hire" => {
            let id = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let role = params.get("role").and_then(|v| v.as_str()).unwrap_or("");
            let caps: Option<Vec<String>> = params.get("capabilities").and_then(|v| {
                v.as_array().map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
            });
            let max_c = params
                .get("max_concurrent")
                .and_then(|v| v.as_u64())
                .map(|u| u as u32);
            kernel
                .identity_hire(id, name, role, caps, max_c)
                .map_err(map_err)
        }
        "identity_set_status" => {
            let id = params
                .get("id")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let status = params
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("active");
            kernel.identity_set_status(id, status).map_err(map_err)
        }
        "identity_set_capabilities" => {
            let id = params
                .get("id")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let caps: Vec<String> = params
                .get("capabilities")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            kernel
                .identity_set_capabilities(id, caps)
                .map_err(map_err)
        }
        "identity_admit" => {
            let id = params
                .get("id")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            kernel.identity_admit(id).map_err(map_err)
        }
        "identity_release" => {
            let id = params
                .get("id")
                .or_else(|| params.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.identity_release(id))
        }
        "identity_authority_status" => Ok(kernel.identity_authority_status()),
        "inbox_submit" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let instruction = params
                .get("instruction")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let priority = params
                .get("priority")
                .and_then(|v| v.as_i64())
                .unwrap_or(50) as i32;
            let meta = params.get("meta").cloned();
            Ok(kernel.inbox_submit(identity, instruction, priority, meta))
        }
        "inbox_claim" => {
            let worker = params
                .get("worker_id")
                .and_then(|v| v.as_str())
                .unwrap_or("worker");
            let identity = params.get("identity").and_then(|v| v.as_str());
            Ok(kernel.inbox_claim(worker, identity))
        }
        "inbox_reclaim" => Ok(kernel.inbox_reclaim()),
        "inbox_complete_by_db_id" => {
            let db_id = params
                .get("db_item_id")
                .or_else(|| params.get("item_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let result = params
                .get("result")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let pid = params.get("process_id").and_then(|v| v.as_str());
            Ok(kernel.inbox_complete_by_db_id(db_id, result, pid))
        }
        "inbox_fail_by_db_id" => {
            let db_id = params
                .get("db_item_id")
                .or_else(|| params.get("item_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let reason = params
                .get("reason")
                .or_else(|| params.get("error"))
                .and_then(|v| v.as_str())
                .unwrap_or("failed");
            Ok(kernel.inbox_fail_by_db_id(db_id, reason))
        }
        "inbox_touch_by_db_id" => {
            let db_id = params
                .get("db_item_id")
                .or_else(|| params.get("item_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.inbox_touch_by_db_id(db_id))
        }
        "inbox_set_claim_timeout" => {
            let secs = params
                .get("secs")
                .or_else(|| params.get("timeout_seconds"))
                .and_then(|v| v.as_f64())
                .unwrap_or(900.0);
            Ok(kernel.inbox_set_claim_timeout(secs))
        }
        "inbox_complete" => {
            let id = params
                .get("item_id")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "item_id required".into(), json!({})))?;
            let token = params
                .get("claim_token")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let result = params
                .get("result")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let pid = params.get("process_id").and_then(|v| v.as_str());
            kernel
                .inbox_complete(id, token, result, pid)
                .map_err(map_err)
        }
        "inbox_fail" => {
            let id = params
                .get("item_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "item_id required".into(), json!({})))?;
            let token = params
                .get("claim_token")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let reason = params
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("failed");
            kernel.inbox_fail(id, token, reason).map_err(map_err)
        }
        "inbox_release" => {
            let id = params
                .get("item_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "item_id required".into(), json!({})))?;
            let token = params
                .get("claim_token")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            kernel.inbox_release(id, token).map_err(map_err)
        }
        "inbox_list" => {
            let status = params.get("status").and_then(|v| v.as_str());
            let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            Ok(kernel.inbox_list(status, limit))
        }
        "inbox_status" => Ok(kernel.inbox_status()),

        // ── P1-B skill gate ───────────────────────────────
        "skill_register" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            let version = params
                .get("version")
                .and_then(|v| v.as_str())
                .unwrap_or("0.1.0");
            let content = params
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let permissions: Vec<String> = params
                .get("permissions")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let tests: Vec<String> = params
                .get("tests")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.skill_register(name, version, content, permissions, tests))
        }
        "skill_verify" => {
            let id = params
                .get("package_id")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "package_id required".into(), json!({})))?;
            kernel.skill_verify(id).map_err(map_err)
        }
        "skill_activate" => {
            let id = params
                .get("package_id")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str())
                .ok_or((-32005, "package_id required".into(), json!({})))?;
            kernel.skill_activate(id).map_err(map_err)
        }
        "skill_rollback" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            kernel.skill_rollback(name).map_err(map_err)
        }
        "skill_get_active" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.skill_get_active(name))
        }
        "skill_list" => Ok(kernel.skill_list()),
        "skill_is_loadable" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.skill_is_loadable(name))
        }
        "skill_gate_status" => Ok(kernel.skill_gate_status()),
        "evolution_policy" => Ok(kernel.evolution_policy()),
        "evolution_submit" => {
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("skill");
            let title = params.get("title").and_then(|v| v.as_str()).unwrap_or("");
            let body = params.get("body").and_then(|v| v.as_str()).unwrap_or("");
            let identity = params.get("identity").and_then(|v| v.as_str());
            let score = params.get("score").and_then(|v| v.as_f64()).unwrap_or(0.5);
            let meta = params.get("meta").cloned().unwrap_or(json!({}));
            Ok(kernel.evolution_submit(kind, title, body, identity, score, meta))
        }
        "evolution_list" => {
            let status = params.get("status").and_then(|v| v.as_str());
            let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            Ok(kernel.evolution_list(status, limit))
        }
        "evolution_approve" => {
            let id = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("user");
            kernel.evolution_approve(id, by).map_err(map_err)
        }
        "evolution_reject" => {
            let id = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("user");
            let reason = params.get("reason").and_then(|v| v.as_str()).unwrap_or("");
            kernel.evolution_reject(id, by, reason).map_err(map_err)
        }
        "evolution_apply" => {
            let id = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let by = params.get("by").and_then(|v| v.as_str()).unwrap_or("user");
            kernel.evolution_apply(id, by).map_err(map_err)
        }
        "evolution_status" => Ok(kernel.evolution_status()),
        "evolution_block_auto" => {
            let reason = params
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("auto_apply_forbidden");
            Ok(kernel.evolution_block_auto(reason))
        }
        "evolution_analyze" => {
            let snapshot = params
                .get("snapshot")
                .cloned()
                .or_else(|| {
                    // allow flat params as snapshot
                    if params.is_object() {
                        Some(params.clone())
                    } else {
                        None
                    }
                })
                .unwrap_or(json!({}));
            Ok(kernel.evolution_analyze(snapshot))
        }

        // ── P1 context / memory layers ────────────────────
        "context_set_quota" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let tokens = params
                .get("tokens")
                .or_else(|| params.get("quota"))
                .and_then(|v| v.as_u64())
                .unwrap_or(32000) as u32;
            Ok(kernel.context_set_quota(pid, tokens))
        }
        "context_put_page" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let label = params.get("label").and_then(|v| v.as_str()).unwrap_or("page");
            let content = params
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.context_put_page(pid, label, content))
        }
        "context_swap_in" => {
            let id = params
                .get("page_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "page_id required".into(), json!({})))?;
            let caller = params.get("process_id").and_then(|v| v.as_str());
            kernel.context_swap_in(id, caller).map_err(map_err)
        }
        "context_swap_out" => {
            let id = params
                .get("page_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "page_id required".into(), json!({})))?;
            let caller = params.get("process_id").and_then(|v| v.as_str());
            kernel.context_swap_out(id, caller).map_err(map_err)
        }
        "context_pin" => {
            let id = params
                .get("page_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "page_id required".into(), json!({})))?;
            let pinned = params
                .get("pinned")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            kernel.context_pin(id, pinned).map_err(map_err)
        }
        "context_set_isolation" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let mode = params
                .get("mode")
                .or_else(|| params.get("isolation"))
                .and_then(|v| v.as_str())
                .unwrap_or("process");
            Ok(kernel.context_set_isolation(pid, mode))
        }
        "context_schedule" => {
            let pid = params.get("process_id").and_then(|v| v.as_str());
            Ok(kernel.context_schedule(pid))
        }
        "context_list_pages" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.context_list_pages(pid))
        }
        "context_status" => {
            let pid = params.get("process_id").and_then(|v| v.as_str());
            Ok(kernel.context_status(pid))
        }
        "memory_layer_put" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let layer = params.get("layer").and_then(|v| v.as_str()).unwrap_or("working");
            let content = params
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let score = params
                .get("score")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.5);
            Ok(kernel.memory_layer_put(identity, layer, content, score))
        }
        "memory_layer_list" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let layer = params.get("layer").and_then(|v| v.as_str());
            Ok(kernel.memory_layer_list(identity, layer))
        }
        "memory_layer_consolidate" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.memory_layer_consolidate(identity))
        }
        "memory_layer_schedule" => {
            let identity = params.get("identity").and_then(|v| v.as_str());
            Ok(kernel.memory_layer_schedule(identity))
        }
        "memory_layer_status" => Ok(kernel.memory_layer_status()),
        "device_sync_status" => Ok(kernel.device_sync_status()),
        "device_sync_register" => {
            let id = params
                .get("device_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let label = params.get("label").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.device_sync_register(id, label))
        }
        "device_sync_set_local" => {
            let id = params
                .get("device_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let label = params.get("label").and_then(|v| v.as_str()).unwrap_or("local");
            Ok(kernel.device_sync_set_local(id, label))
        }
        "device_sync_list" => Ok(kernel.device_sync_list()),
        "device_sync_push" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let to = params.get("to_device").and_then(|v| v.as_str());
            Ok(kernel.device_sync_push(identity, to))
        }
        "device_sync_pull" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let since = params.get("since_revision").and_then(|v| v.as_u64());
            Ok(kernel.device_sync_pull(identity, since))
        }
        "device_sync_apply" => {
            let env = params
                .get("envelope")
                .cloned()
                .unwrap_or_else(|| params.clone());
            kernel.device_sync_apply(env).map_err(map_err)
        }
        "device_sync_outbox" => {
            let limit = params
                .get("limit")
                .and_then(|v| v.as_u64())
                .unwrap_or(16) as usize;
            Ok(kernel.device_sync_outbox(limit))
        }
        "audit_anchor_verify" => Ok(kernel.audit_anchor_verify()),
        "audit_anchor_status" => Ok(kernel.audit_anchor_status()),

        // ── P2 ────────────────────────────────────────────
        "coding_profile_list" => Ok(kernel.coding_profile_list()),
        "coding_profile_get" => {
            let id = params.get("id").or_else(|| params.get("profile")).and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.coding_profile_get(id))
        }
        "coding_profile_apply" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let profile = params.get("profile").or_else(|| params.get("id")).and_then(|v| v.as_str()).unwrap_or("engineering");
            kernel.coding_profile_apply(pid, profile).map_err(map_err)
        }
        "coding_profile_spawn" => {
            let identity = params
                .get("identity")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let profile = params
                .get("profile")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str())
                .unwrap_or("engineering");
            let session = params.get("session_id").and_then(|v| v.as_str());
            kernel
                .coding_profile_spawn(identity, profile, session)
                .map_err(map_err)
        }
        "collab_set_plan" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let steps: Vec<String> = params.get("steps").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            Ok(kernel.collab_set_plan(pid, steps))
        }
        "collab_revise_plan" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let steps: Vec<String> = params.get("steps").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            Ok(kernel.collab_revise_plan(pid, steps))
        }
        "collab_interrupt" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let reason = params.get("reason").and_then(|v| v.as_str()).unwrap_or("user interrupt");
            kernel.collab_interrupt(pid, reason).map_err(map_err)
        }
        "collab_resume" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            kernel.collab_resume(pid).map_err(map_err)
        }
        "collab_request_approval" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("other");
            let summary = params.get("summary").and_then(|v| v.as_str()).unwrap_or("");
            let detail = params.get("detail").cloned().unwrap_or(json!({}));
            Ok(kernel.collab_request_approval(pid, kind, summary, detail))
        }
        "collab_resolve_approval" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let aid = params.get("approval_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "approval_id required".into(), json!({})))?;
            let approve = params.get("approve").and_then(|v| v.as_bool()).unwrap_or(false);
            kernel.collab_resolve_approval(pid, aid, approve).map_err(map_err)
        }
        "collab_get" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.collab_get(pid))
        }
        "collab_status" => Ok(kernel.collab_status()),
        "edit_propose" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let path = params.get("path").and_then(|v| v.as_str()).ok_or((-32005, "path required".into(), json!({})))?;
            let after = params.get("after").or_else(|| params.get("content")).and_then(|v| v.as_str()).unwrap_or("");
            kernel.edit_propose(pid, path, after).map_err(map_err)
        }
        "edit_confirm" => {
            let id = params.get("session_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "session_id required".into(), json!({})))?;
            kernel.edit_confirm(id).map_err(map_err)
        }
        "edit_reject" => {
            let id = params.get("session_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "session_id required".into(), json!({})))?;
            kernel.edit_reject(id).map_err(map_err)
        }
        "edit_rollback" => {
            let id = params.get("session_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "session_id required".into(), json!({})))?;
            kernel.edit_rollback(id).map_err(map_err)
        }
        "edit_list" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.edit_list(pid))
        }
        "edit_get" => {
            let id = params.get("session_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.edit_get(id))
        }
        "repo_index_build" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).ok_or((-32005, "process_id required".into(), json!({})))?;
            let root = params.get("root").and_then(|v| v.as_str()).ok_or((-32005, "root required".into(), json!({})))?;
            let depth = params.get("max_depth").and_then(|v| v.as_u64()).unwrap_or(6) as usize;
            kernel.repo_index_build(pid, root, depth).map_err(map_err)
        }
        "repo_index_get" => {
            let id = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.repo_index_get(id))
        }
        "repo_index_list" => {
            let pid = params.get("process_id").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.repo_index_list(pid))
        }
        "hal_platform" => Ok(kernel.hal_platform()),
        "hal_resolve_path" => {
            let path = params.get("path").and_then(|v| v.as_str()).ok_or((-32005, "path required".into(), json!({})))?;
            let ws = params.get("workspace").and_then(|v| v.as_str());
            kernel.hal_resolve_path(ws, path).map_err(map_err)
        }
        "hal_resolve_command" => {
            let logical = params.get("logical").or_else(|| params.get("command")).and_then(|v| v.as_str()).unwrap_or("shell");
            let args: Vec<String> = params.get("args").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            Ok(kernel.hal_resolve_command(logical, args))
        }
        "hal_resolve_browser" => {
            let url = params.get("url").and_then(|v| v.as_str()).unwrap_or("about:blank");
            Ok(kernel.hal_resolve_browser(url))
        }
        "hal_status" => Ok(kernel.hal_status()),
        "hal_enforce_path" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let path = params
                .get("path")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "path required".into(), json!({})))?;
            let ws = params.get("workspace").and_then(|v| v.as_str());
            let cap = params.get("capability").and_then(|v| v.as_str());
            kernel
                .hal_enforce_path(pid, ws, path, cap)
                .map_err(map_err)
        }
        "hal_enforce_command" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let logical = params
                .get("logical")
                .or_else(|| params.get("command"))
                .and_then(|v| v.as_str())
                .unwrap_or("shell");
            let args: Vec<String> = params
                .get("args")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            kernel
                .hal_enforce_command(pid, logical, args)
                .map_err(map_err)
        }
        "hal_enforce_browser" => {
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "process_id required".into(), json!({})))?;
            let url = params
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "url required".into(), json!({})))?;
            kernel.hal_enforce_browser(pid, url).map_err(map_err)
        }
        "wasm_load" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("mod");
            let bytes = params.get("bytes").or_else(|| params.get("content")).and_then(|v| v.as_str()).unwrap_or("");
            let fuel = params.get("fuel_limit").and_then(|v| v.as_u64());
            let mem = params.get("memory_pages").and_then(|v| v.as_u64()).map(|u| u as u32);
            kernel.wasm_load(name, bytes, fuel, mem).map_err(map_err)
        }
        "wasm_activate" => {
            let id = params.get("module_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "module_id required".into(), json!({})))?;
            kernel.wasm_activate(id).map_err(map_err)
        }
        "wasm_invoke" => {
            let id = params.get("module_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "module_id required".into(), json!({})))?;
            let entry = params.get("entry").and_then(|v| v.as_str()).unwrap_or("main");
            let p = params.get("params").cloned().unwrap_or(json!({}));
            kernel.wasm_invoke(id, entry, p).map_err(map_err)
        }
        "wasm_unload" => {
            let id = params.get("module_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "module_id required".into(), json!({})))?;
            kernel.wasm_unload(id).map_err(map_err)
        }
        "wasm_kill" => {
            let id = params.get("module_id").or_else(|| params.get("id")).and_then(|v| v.as_str()).ok_or((-32005, "module_id required".into(), json!({})))?;
            kernel.wasm_kill(id).map_err(map_err)
        }
        "wasm_list" => Ok(kernel.wasm_list()),
        "wasm_status" => Ok(kernel.wasm_status()),
        "wasm_explain" => {
            let id = params
                .get("module_id")
                .or_else(|| params.get("id"))
                .and_then(|v| v.as_str());
            Ok(kernel.wasm_explain(id))
        }
        "pkg_install" => {
            let name = params.get("name").and_then(|v| v.as_str()).ok_or((-32005, "name required".into(), json!({})))?;
            let version = params.get("version").and_then(|v| v.as_str()).unwrap_or("0.1.0");
            let content = params.get("content").and_then(|v| v.as_str()).unwrap_or("");
            let deps: Vec<String> = params.get("dependencies").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            let perms: Vec<String> = params.get("permissions").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
            let sig = params.get("signature").and_then(|v| v.as_str());
            kernel.pkg_install(name, version, content, deps, perms, sig).map_err(map_err)
        }
        "pkg_activate" => {
            let name = params.get("name").and_then(|v| v.as_str()).ok_or((-32005, "name required".into(), json!({})))?;
            kernel.pkg_activate(name).map_err(map_err)
        }
        "pkg_uninstall" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.pkg_uninstall(name))
        }
        "pkg_list" => Ok(kernel.pkg_list()),
        "pkg_get" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.pkg_get(name))
        }
        "pkg_sign" => {
            let content = params.get("content").and_then(|v| v.as_str()).unwrap_or("");
            Ok(kernel.pkg_sign(content))
        }
        "pkg_set_signing_key" => {
            let key = params
                .get("key")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.pkg_set_signing_key(key))
        }
        "pkg_set_require_secure" => {
            let require = params
                .get("require")
                .or_else(|| params.get("require_secure"))
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            Ok(kernel.pkg_set_require_secure(require))
        }
        "pkg_scan" => {
            let content = params.get("content").and_then(|v| v.as_str()).unwrap_or("");
            let perms: Vec<String> = params
                .get("permissions")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.pkg_scan(content, perms))
        }
        "pkg_promote" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            let force = params
                .get("force")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            kernel.pkg_promote(name, force).map_err(map_err)
        }
        "pkg_catalog" => Ok(kernel.pkg_catalog()),
        "pkg_status" => Ok(kernel.pkg_status()),
        "instance_export" => {
            let identity = params.get("identity").and_then(|v| v.as_str()).unwrap_or("main");
            let pid = params.get("process_id").and_then(|v| v.as_str());
            Ok(kernel.instance_export(identity, pid))
        }
        "instance_import" => {
            let bundle = params.get("bundle").cloned().unwrap_or_else(|| params.clone());
            kernel.instance_import(bundle).map_err(map_err)
        }
        "instance_list" => Ok(kernel.instance_list()),
        "instance_status" => Ok(kernel.instance_status()),
        "abi_compat" => Ok(kernel.abi_compat()),
        "abi_negotiate" => {
            let client = params
                .get("client_abi")
                .or_else(|| params.get("abi"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.abi_negotiate(client))
        }
        "abi_record_break" => {
            let from = params
                .get("from_abi")
                .or_else(|| params.get("from"))
                .and_then(|v| v.as_str())
                .unwrap_or(env!("CARGO_PKG_VERSION"));
            let to = params
                .get("to_abi")
                .or_else(|| params.get("to"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let reason = params
                .get("reason")
                .and_then(|v| v.as_str())
                .unwrap_or("unspecified");
            let removed: Vec<String> = params
                .get("methods_removed")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.abi_record_break(from, to, reason, removed))
        }

        // ── R3/R4 domain + approval ───────────────────────
        "domain_publish" => {
            let topic = params.get("topic").and_then(|v| v.as_str()).unwrap_or("custom");
            let payload = params.get("payload").cloned().unwrap_or(json!({}));
            Ok(kernel.domain_publish(topic, payload))
        }
        "domain_recent" => {
            let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            let prefix = params.get("prefix").and_then(|v| v.as_str());
            let since_ts = params.get("since_ts").and_then(|v| v.as_f64());
            let after_seq = params.get("after_seq").and_then(|v| v.as_u64());
            Ok(kernel.domain_recent(limit, prefix, since_ts, after_seq))
        }
        "domain_seq" => Ok(kernel.domain_seq()),
        "domain_status" => Ok(kernel.domain_status()),
        "approval_set_rules" => {
            let rules = params.get("rules").cloned().unwrap_or_else(|| params.clone());
            Ok(kernel.approval_set_rules(rules))
        }
        "approval_get_rules" => Ok(kernel.approval_get_rules()),
        "approval_classify" => {
            let caps: Vec<String> = params
                .get("capabilities")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.approval_classify(caps))
        }
        "approval_should_auto" => {
            let caps: Vec<String> = params
                .get("capabilities")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            Ok(kernel.approval_should_auto(caps))
        }
        "approval_cap_eligible" => {
            let cap = params
                .get("cap")
                .or_else(|| params.get("capability"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let high = params
                .get("high_risk_auto")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            Ok(kernel.approval_cap_eligible(cap, high))
        }
        "session_grant_add" => {
            let sid = params
                .get("session_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let mut sigs: Vec<String> = params
                .get("sigs")
                .or_else(|| params.get("signatures"))
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            if let Some(s) = params.get("sig").and_then(|v| v.as_str()) {
                sigs.push(s.to_string());
            }
            Ok(kernel.session_grant_add(sid, sigs))
        }
        "session_grant_has" => {
            let sid = params
                .get("session_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let sig = params
                .get("sig")
                .or_else(|| params.get("signature"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.session_grant_has(sid, sig))
        }
        "session_grant_clear" => {
            let sid = params
                .get("session_id")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            Ok(kernel.session_grant_clear(sid))
        }

        "emit" => {
            let kind = params.get("kind").and_then(|v| v.as_str()).unwrap_or("custom");
            let pid = params
                .get("process_id")
                .and_then(|v| v.as_str())
                .unwrap_or("system");
            let detail = params.get("detail").cloned().unwrap_or(json!({}));
            Ok(kernel.emit(kind, pid, detail).to_dict())
        }

        "register_service" => {
            let name = params
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or((-32005, "name required".into(), json!({})))?;
            let meta = params.get("meta").cloned().unwrap_or(json!({}));
            runtime.register_service(name, meta);
            Ok(json!({"ok": true}))
        }

        other => Err((
            -32601,
            format!("Method not found: {other}"),
            json!({}),
        )),
    }
}

/// 探活方法：无需 RPC secret（其余方法一律需认证）。
const PUBLIC_METHODS: &[&str] = &["ping", "health", "list_methods", "abi_version"];

/// 确保存在 RPC secret：env 优先，否则读写 `~/.tevarn/rpc.secret`（仅当前用户可读）。
/// Soft-migrate pre-rebrand leftovers: `TAKTON_KERNEL_RPC_SECRET` / `~/.takton/rpc.secret`.
fn ensure_rpc_secret() -> String {
    for key in ["TEVARN_KERNEL_RPC_SECRET", "TAKTON_KERNEL_RPC_SECRET"] {
        if let Ok(s) = std::env::var(key) {
            let t = s.trim().to_string();
            if !t.is_empty() {
                std::env::set_var("TEVARN_KERNEL_RPC_SECRET", &t);
                return t;
            }
        }
    }
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let path = home.join(".tevarn").join("rpc.secret");
    // Prefer Tevarn path; fall back to legacy ~/.takton secret so upgrades keep working.
    for candidate in [&path, &home.join(".takton").join("rpc.secret")] {
        if let Ok(existing) = std::fs::read_to_string(candidate) {
            let t = existing.trim().to_string();
            if !t.is_empty() {
                // 同步到 env，便于同机 Python 客户端读取
                std::env::set_var("TEVARN_KERNEL_RPC_SECRET", &t);
                // Mirror into ~/.tevarn if we only found the legacy file.
                if candidate != &path {
                    if let Some(parent) = path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    if std::fs::write(&path, t.as_bytes()).is_ok() {
                        #[cfg(windows)]
                        tighten_windows_secret_acl(&path);
                    }
                }
                return t;
            }
        }
    }
    // CSPRNG：uuid v4 底层走 OS 随机源（审计 P3-5）
    let secret = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)
        {
            use std::io::Write;
            let _ = f.write_all(secret.as_bytes());
        }
    }
    #[cfg(windows)]
    {
        // 先写文件，再用 icacls 收紧为当前用户只读（审计 P2-4）
        if let Err(e) = std::fs::write(&path, secret.as_bytes()) {
            tracing::warn!("failed to write rpc.secret: {e}");
        } else {
            tighten_windows_secret_acl(&path);
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = std::fs::write(&path, secret.as_bytes());
    }
    std::env::set_var("TEVARN_KERNEL_RPC_SECRET", &secret);
    info!("kernel RPC secret persisted to {}", path.display());
    secret
}

/// Windows：去掉继承 ACL，仅授予当前用户读权限。
#[cfg(windows)]
fn tighten_windows_secret_acl(path: &std::path::Path) {
    let Some(path_s) = path.to_str() else {
        return;
    };
    // 优先用 USERNAME；失败则跳过（不阻断启动）
    let user = std::env::var("USERNAME")
        .or_else(|_| std::env::var("USER"))
        .unwrap_or_default();
    if user.is_empty() {
        tracing::warn!("rpc.secret: USERNAME empty, skip ACL tighten");
        return;
    }
    // /inheritance:r 去掉继承；/grant:r 仅当前用户读
    let grant = format!("{user}:(R)");
    match std::process::Command::new("icacls")
        .args([path_s, "/inheritance:r", "/grant:r", &grant])
        .output()
    {
        Ok(out) if out.status.success() => {
            info!("rpc.secret ACL tightened for user {user}");
        }
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            tracing::warn!(
                "icacls failed for rpc.secret (status={}): {}",
                out.status,
                stderr.trim()
            );
        }
        Err(e) => {
            tracing::warn!("icacls not available for rpc.secret ACL: {e}");
        }
    }
}

fn check_rpc_auth(method: &str, params: &Value) -> Result<(), (i64, String, Value)> {
    // 探活始终放行
    if PUBLIC_METHODS.contains(&method) {
        return Ok(());
    }
    // 默认全部需认证（消除「漏加白名单」旁路）
    let secret = ensure_rpc_secret();
    let got = params
        .get("_rpc_auth")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    // 常量时间比较（长度不等时仍做一轮比较）
    let a = secret.as_bytes();
    let b = got.as_bytes();
    let mut diff = (a.len() ^ b.len()) as u8;
    let n = a.len().min(b.len());
    for i in 0..n {
        diff |= a[i] ^ b[i];
    }
    if diff == 0 && a.len() == b.len() && !a.is_empty() {
        return Ok(());
    }
    Err((
        -32013,
        "RPC auth required: set params._rpc_auth = TEVARN_KERNEL_RPC_SECRET (or ~/.tevarn/rpc.secret)".into(),
        json!({"method": method}),
    ))
}

fn dispatch(runtime: &Arc<Runtime>, line: &str) -> Value {
    let req: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            return err_resp(Value::Null, -32700, format!("Parse error: {e}"), None);
        }
    };
    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let method = match req.get("method").and_then(|v| v.as_str()) {
        Some(m) => m,
        None => return err_resp(id, -32600, "Invalid Request", None),
    };
    let mut params = req.get("params").cloned().unwrap_or(json!({}));
    if let Err((code, msg, data)) = check_rpc_auth(method, &params) {
        return err_resp(id, code, msg, Some(data));
    }
    // 不把密钥写进审计：剥掉 _rpc_auth
    if let Some(obj) = params.as_object_mut() {
        obj.remove("_rpc_auth");
    }
    let kernel = runtime.kernel();
    match handle_method(kernel.as_ref(), runtime.as_ref(), method, &params) {
        Ok(result) => ok_resp(id, result),
        Err((code, msg, data)) => err_resp(id, code, msg, Some(data)),
    }
}

/// Max wall time for a single JSON-RPC dispatch on the blocking pool.
/// Prevents one hung mediate/lock from filling the pool and starving `accept`
/// (symptom: LISTEN open but new TCP connects SYN_SENT / timed out).
const DISPATCH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

async fn handle_connection(runtime: Arc<Runtime>, stream: TcpStream) {
    let (reader, mut writer) = stream.into_split();
    let mut lines = BufReader::new(reader).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        // spawn_blocking: sync dispatch must not occupy a Tokio worker.
        // A stuck/long mediate on one connection previously starved all other
        // clients (UI panel 500s / ping timeout) when workers blocked on locks.
        // Also: if the blocking pool saturates while every worker awaits
        // spawn_blocking, accept() never runs → host looks "up" but dead.
        let resp = dispatch_with_timeout(runtime.clone(), line).await;
        let mut out = serde_json::to_string(&resp).unwrap_or_else(|_| {
            r#"{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"serialize"}}"#
                .into()
        });
        out.push('\n');
        if let Err(e) = writer.write_all(out.as_bytes()).await {
            warn!("write failed: {e}");
            break;
        }
    }
}

async fn run_tcp(runtime: Arc<Runtime>, addr: SocketAddr) -> anyhow::Result<()> {
    let listener = TcpListener::bind(addr)
        .await
        .with_context(|| format!("bind {addr}"))?;
    info!("tevarn-kernel-host listening on {addr}");
    loop {
        let (stream, peer) = listener.accept().await?;
        info!("client connected {peer}");
        let rt = runtime.clone();
        tokio::spawn(async move {
            handle_connection(rt, stream).await;
        });
    }
}

async fn isolation_reap_loop(runtime: Arc<Runtime>) {
    // Poll exited children even when the Python dispatcher is idle.
    // max_age stays 600s so a long-running intended child is not killed early.
    let mut interval = tokio::time::interval(Duration::from_secs(5));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        interval.tick().await;
        let rt = runtime.clone();
        let _ = tokio::task::spawn_blocking(move || {
            rt.kernel().isolation_reap(Some(600.0));
        })
        .await;
    }
}

async fn dispatch_with_timeout(runtime: Arc<Runtime>, line: String) -> Value {
    let rt = runtime.clone();
    match tokio::time::timeout(
        DISPATCH_TIMEOUT,
        tokio::task::spawn_blocking(move || dispatch(&rt, &line)),
    )
    .await
    {
        Ok(Ok(v)) => v,
        Ok(Err(e)) => {
            warn!("dispatch join failed: {e}");
            err_resp(
                serde_json::Value::Null,
                -32603,
                format!("dispatch join: {e}"),
                None,
            )
        }
        Err(_) => {
            warn!("dispatch timed out after {DISPATCH_TIMEOUT:?}");
            runtime.kernel().wasm_interrupt();
            err_resp(
                serde_json::Value::Null,
                -32603,
                format!(
                    "dispatch timeout after {}s (kernel busy / lock)",
                    DISPATCH_TIMEOUT.as_secs()
                ),
                None,
            )
        }
    }
}

async fn run_stdio(runtime: Arc<Runtime>) -> anyhow::Result<()> {
    use tokio::io::stdin;
    info!("tevarn-kernel-host stdio mode");
    let mut lines = BufReader::new(stdin()).lines();
    let mut stdout = tokio::io::stdout();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        let resp = dispatch_with_timeout(runtime.clone(), line).await;
        let mut out = serde_json::to_string(&resp).unwrap_or_default();
        out.push('\n');
        stdout.write_all(out.as_bytes()).await?;
        stdout.flush().await?;
    }
    Ok(())
}

fn main() -> anyhow::Result<()> {
    // Larger blocking pool so concurrent agent RPCs + UI side-channel do not
    // exhaust workers and deadlock accept (default is ~512 but under load with
    // held kernel locks we still want headroom for short pings).
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .thread_name("tevarn-host")
        .worker_threads(4)
        .max_blocking_threads(64)
        .build()
        .context("tokio runtime")?;
    rt.block_on(async_main())
}

async fn async_main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .with_writer(std::io::stderr)
        .init();

    let args = Args::parse();
    let runtime = build_runtime(&args);
    info!(
        "runtime up profile={} health={} blocking_pool=64 dispatch_timeout={}s",
        runtime.profile(),
        runtime.health(),
        DISPATCH_TIMEOUT.as_secs()
    );

    let reap_rt = runtime.clone();
    tokio::spawn(async move {
        isolation_reap_loop(reap_rt).await;
    });

    if args.stdio {
        run_stdio(runtime).await
    } else {
        let addr: SocketAddr = args
            .listen
            .parse()
            .with_context(|| format!("bad listen addr {}", args.listen))?;
        run_tcp(runtime, addr).await
    }
}
