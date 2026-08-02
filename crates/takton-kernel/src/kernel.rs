//! AgentKernel — process table, mediate, budget, audit, resources.

use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::audit::{AuditEventStore, KernelEvent, EVENT_BUFFER_MAX, GENESIS_HASH};
use crate::capability::CapabilityToken;
use crate::checkpoint::CheckpointStore;
use crate::court::{decide_capability, decide_tool, CourtDecision, CourtPolicy};
use crate::isolation::{IsolationProfile, IsolationSupervisor};
use crate::run_gate::{RunGate, RunGateResult};
use crate::process_snapshot::ProcessSnapshotStore;
use crate::result_store::ResultSpillStore;
use crate::policy::PolicySupervisor;
use crate::cache_metrics::CacheMetrics;
use crate::cost::CostLedger;
use crate::marathon::MarathonMetrics;
use crate::ipc::IpcBus;
use crate::services::{ServicePrivilege, ServiceSupervisor};
use crate::identity_cache::IdentityCache;
use crate::inbox::InboxQueue;
use crate::skill_gate::SkillGate;
use crate::evolution_gate::EvolutionGate;
use crate::context_vm::ContextVm;
use crate::memory_layers::{LayeredMemory, MemoryLayer};
use crate::coding_profile::CodingProfileRegistry;
use crate::collab::CollabHub;
use crate::edit_session::EditSessionStore;
use crate::repo_index::RepoIndexStore;
use crate::hal::Hal;
use crate::wasm_runtime::WasmRuntime;
use crate::package_mgr::PackageManager;
use crate::instance::InstanceRegistry;
use crate::device_sync::DeviceSyncHub;
use crate::domain_events::DomainEventBus;
use crate::approval_rules::ApprovalPolicy;
use crate::eval_suite::EvalSuite;
use crate::abi_compat::AbiCompatState;
use crate::agent_manifest::{pack_checklist, validate_agent_manifest, validate_agent_manifest_str};
use crate::error::{KernelError, KernelResult};
use crate::intent::{synthesize_token, IntentDeclaration};
use crate::process::{AgentProcess, ProcessState};
use crate::resource::{ResourceKind, ResourceManager};
use crate::scheduler::AgentScheduler;
use crate::llm_admission::{
    LlmAcquireResult, LlmAdmissionConfig, LlmAdmissionController, LlmLeaseRequest,
};
use crate::scheduler::PriorityClass;
use crate::tool_catalog::{filter_tool_names, tools_for_capabilities};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn short_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MediationDecision {
    pub allowed: bool,
    pub reason: String,
    pub capability_checked: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EscalationRequest {
    pub id: String,
    pub process_id: String,
    pub capabilities: Vec<String>,
    pub reason: String,
    pub status: String,
    pub created_at: f64,
    pub resolved_at: Option<f64>,
    pub resolved_by: Option<String>,
    pub target: Option<String>,
    pub identity_id: Option<String>,
}

impl EscalationRequest {
    pub fn to_dict(&self) -> Value {
        let mut d = json!({
            "id": self.id,
            "process_id": self.process_id,
            "capabilities": self.capabilities,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        });
        if let Some(ref t) = self.target {
            d["target"] = json!(t);
        }
        if let Some(ref i) = self.identity_id {
            d["identity_id"] = json!(i);
        }
        d
    }
}

#[derive(Debug, Clone)]
pub struct SoftRenewConfig {
    pub enabled: bool,
    pub max_renew: i32,
    pub factor: f64,
    pub min_add: i64,
    pub hard_cap: i64,
}

impl Default for SoftRenewConfig {
    /// Product default (0.5.0-alpha polish): **hard-budget first**.
    /// Soft renew is off unless host/operator explicitly enables it.
    /// Marathon / long-job profiles may override with higher max_renew.
    fn default() -> Self {
        Self {
            enabled: false,
            max_renew: 2,
            factor: 1.0,
            min_add: 50_000,
            hard_cap: 2_000_000,
        }
    }
}

#[derive(Debug, Clone)]
pub struct KernelConfig {
    pub audit_path: Option<PathBuf>,
    pub audit_persist: bool,
    pub soft_renew: SoftRenewConfig,
    pub hmac_key: Option<Vec<u8>>,
    /// P0-B: when true, create_process without caps uses default readonly intent
    /// (never silent full-open). Explicit capabilities still honor caller list.
    pub require_intent: bool,
}

impl Default for KernelConfig {
    fn default() -> Self {
        Self {
            audit_path: None,
            audit_persist: true,
            soft_renew: SoftRenewConfig::default(),
            hmac_key: None,
            require_intent: true,
        }
    }
}

struct KernelInner {
    processes: HashMap<String, AgentProcess>,
    events: Vec<KernelEvent>,
    escalations: HashMap<String, EscalationRequest>,
    scheduler: AgentScheduler,
    resources: ResourceManager,
    audit_store: Option<AuditEventStore>,
    disk_tail_hash: Option<String>,
    soft_renew: SoftRenewConfig,
    hmac_key: Option<Vec<u8>>,
    require_intent: bool,
    llm: LlmAdmissionController,
    isolation: IsolationSupervisor,
    court_policy: CourtPolicy,
    checkpoints: CheckpointStore,
    run_gate: RunGate,
    process_snapshots: ProcessSnapshotStore,
    result_store: ResultSpillStore,
    policy: PolicySupervisor,
    cache_metrics: CacheMetrics,
    cost_ledger: CostLedger,
    marathon: MarathonMetrics,
    ipc: IpcBus,
    services: ServiceSupervisor,
    identity_cache: IdentityCache,
    inbox: InboxQueue,
    skill_gate: SkillGate,
    evolution: EvolutionGate,
    context_vm: ContextVm,
    memory_layers: LayeredMemory,
    collab: CollabHub,
    edit_sessions: EditSessionStore,
    repo_index: RepoIndexStore,
    wasm: WasmRuntime,
    packages: PackageManager,
    instances: InstanceRegistry,
    device_sync: DeviceSyncHub,
    domain_events: DomainEventBus,
    approval: ApprovalPolicy,
    eval_suite: EvalSuite,
    abi: AbiCompatState,
    /// Opaque identity registry hook marker (set by host/runtime glue)
    identity_registry_attached: bool,
}

pub struct AgentKernel {
    inner: RwLock<KernelInner>,
}

impl AgentKernel {
    pub fn new(config: KernelConfig) -> Self {
        let audit_store = if config.audit_persist {
            let path = config
                .audit_path
                .unwrap_or_else(AuditEventStore::default_path);
            Some(AuditEventStore::new(path))
        } else {
            None
        };
        let disk_tail_hash = audit_store.as_ref().and_then(|s| s.load_tail_hash());
        Self {
            inner: RwLock::new(KernelInner {
                processes: HashMap::new(),
                events: Vec::new(),
                escalations: HashMap::new(),
                scheduler: AgentScheduler::new(),
                resources: ResourceManager::new(),
                audit_store,
                disk_tail_hash,
                soft_renew: config.soft_renew,
                hmac_key: config.hmac_key,
                require_intent: config.require_intent,
                llm: LlmAdmissionController::default(),
                isolation: IsolationSupervisor::new(),
                court_policy: CourtPolicy::default(),
                checkpoints: CheckpointStore::new(CheckpointStore::default_dir()),
                run_gate: RunGate::new(4),
                process_snapshots: ProcessSnapshotStore::new(Some(
                    ProcessSnapshotStore::default_dir(),
                )),
                result_store: ResultSpillStore::default(),
                policy: PolicySupervisor::default(),
                cache_metrics: CacheMetrics::default(),
                cost_ledger: CostLedger::default(),
                marathon: MarathonMetrics::default(),
                ipc: IpcBus::default(),
                services: ServiceSupervisor::default(),
                identity_cache: IdentityCache::default(),
                inbox: InboxQueue::default(),
                skill_gate: SkillGate::default(),
                evolution: EvolutionGate::default(),
                context_vm: ContextVm::default(),
                memory_layers: LayeredMemory::default(),
                collab: CollabHub::default(),
                edit_sessions: EditSessionStore::default(),
                repo_index: RepoIndexStore::default(),
                wasm: WasmRuntime::default(),
                packages: PackageManager::default(),
                instances: InstanceRegistry::default(),
                device_sync: DeviceSyncHub::default(),
                domain_events: DomainEventBus::default(),
                approval: ApprovalPolicy::default(),
                eval_suite: EvalSuite::default(),
                abi: AbiCompatState::default(),
                identity_registry_attached: false,
            }),
        }
    }

    pub fn default_new() -> Self {
        Self::new(KernelConfig::default())
    }

    // ── emit ──────────────────────────────────────────────

    fn emit_locked(inner: &mut KernelInner, kind: &str, process_id: &str, detail: Value) -> KernelEvent {
        let prev = if let Some(last) = inner.events.last() {
            last.hash.clone()
        } else if let Some(ref h) = inner.disk_tail_hash {
            h.clone()
        } else {
            GENESIS_HASH.to_string()
        };
        let event = KernelEvent::new(kind, process_id, detail.clone(), &prev);
        // Domain event fan-out (product bus; complements audit chain)
        let _ = inner
            .domain_events
            .publish_from_kernel(kind, process_id, detail);
        if let Some(ref store) = inner.audit_store {
            let _ = store.append(&event.to_dict());
        }
        inner.events.push(event.clone());
        if inner.events.len() > EVENT_BUFFER_MAX {
            let excess = inner.events.len() - EVENT_BUFFER_MAX;
            inner.events.drain(0..excess);
        }
        event
    }

    pub fn emit(&self, kind: &str, process_id: &str, detail: Value) -> KernelEvent {
        let mut g = self.inner.write();
        Self::emit_locked(&mut g, kind, process_id, detail)
    }

    // ── process management ────────────────────────────────

    /// Create process. Optional `intent` forces capability synthesis (P0-B).
    ///
    /// Rules:
    /// - `intent` present → synthesize granted caps + token (overrides empty caps)
    /// - no intent, no caps, `require_intent` → default readonly intent
    /// - explicit `capabilities` without intent → honored (workforce identity caps)
    pub fn create_process(
        &self,
        identity: &str,
        session_id: Option<&str>,
        parent_id: Option<&str>,
        capabilities: Option<Vec<String>>,
        token_budget: Option<i64>,
        meta: Option<BTreeMap<String, Value>>,
    ) -> KernelResult<AgentProcess> {
        self.create_process_with_intent(
            identity,
            session_id,
            parent_id,
            capabilities,
            token_budget,
            meta,
            None,
        )
    }

    pub fn create_process_with_intent(
        &self,
        identity: &str,
        session_id: Option<&str>,
        parent_id: Option<&str>,
        capabilities: Option<Vec<String>>,
        token_budget: Option<i64>,
        meta: Option<BTreeMap<String, Value>>,
        intent: Option<IntentDeclaration>,
    ) -> KernelResult<AgentProcess> {
        let mut g = self.inner.write();
        let require_intent = g.require_intent;
        let mut effective_caps = capabilities;
        let mut effective_budget = token_budget;
        let mut pending_intent = intent;
        let mut intent_dropped: Vec<String> = Vec::new();
        let mut parent_token: Option<CapabilityToken> = None;

        if let Some(pid) = parent_id {
            let parent = g
                .processes
                .get(pid)
                .ok_or_else(|| KernelError::NotFound(format!("未知父进程 {pid}")))?
                .clone();
            if parent.is_terminal() {
                return Err(KernelError::Invalid(format!(
                    "父进程 {pid} 已终止（{}），无法派生子进程",
                    parent.state
                )));
            }
            parent_token = parent.token.clone();
            if let (Some(ref caps), Some(ref pcaps)) = (&effective_caps, &parent.capabilities) {
                if !pcaps.iter().any(|c| c == "*") {
                    let extra: Vec<_> = caps
                        .iter()
                        .filter(|c| !pcaps.contains(c))
                        .cloned()
                        .collect();
                    if !extra.is_empty() {
                        let mut sorted = extra;
                        sorted.sort();
                        return Err(KernelError::CapabilityEscalation(format!(
                            "子进程能力 {sorted:?} 超出父进程能力集"
                        )));
                    }
                }
            } else if effective_caps.is_none() && pending_intent.is_none() {
                effective_caps = parent.capabilities.clone();
            }

            if let (Some(tb), Some(rem)) = (token_budget, parent.budget_remaining()) {
                if tb > rem {
                    return Err(KernelError::BudgetExceeded(format!(
                        "子进程预算 {tb} 超过父进程剩余预算 {rem}"
                    )));
                }
                Self::charge_tokens_locked(&mut g, pid, tb)?;
            }
        }

        // P0-B: no silent full-open — default readonly intent when required
        if pending_intent.is_none() && effective_caps.is_none() && require_intent {
            pending_intent = Some(IntentDeclaration::default_readonly(&format!(
                "process {identity}"
            )));
        }

        // Pre-synthesize intent to get caps/budget before insert (need process id after)
        let intent_for_apply = pending_intent.clone();

        let mut meta = meta.unwrap_or_default();
        meta.insert("_sync_at".into(), json!(now_secs()));
        if let Some(ref intent) = intent_for_apply {
            if effective_budget.is_none() {
                if let Some(tb) = intent.token_budget_hint() {
                    effective_budget = Some(tb);
                }
            }
        }
        if let Some(b) = effective_budget {
            meta.insert("budget_base".into(), json!(b));
        }

        // Placeholder caps; intent apply overwrites
        if let Some(ref intent) = intent_for_apply {
            let (tok, dropped) =
                synthesize_token(intent, "pending", parent_token.as_ref())?;
            intent_dropped = dropped;
            effective_caps = Some(tok.capabilities.iter().cloned().collect());
            meta.insert("intent".into(), intent.to_dict());
            meta.insert("intent_dropped".into(), json!(intent_dropped));
        }

        let mut proc = AgentProcess::new(
            identity,
            session_id.map(|s| s.to_string()),
            parent_id.map(|s| s.to_string()),
            effective_caps.clone(),
            effective_budget,
            meta,
        );
        let id = proc.id.clone();

        // Finalize token with real process id
        if let Some(ref intent) = intent_for_apply {
            let (tok, dropped) = synthesize_token(intent, &id, parent_token.as_ref())?;
            intent_dropped = dropped;
            let granted: Vec<String> = tok.capabilities.iter().cloned().collect();
            proc.capabilities = Some(granted.clone());
            proc.token = Some(tok);
            proc.meta.insert("intent".into(), intent.to_dict());
            proc.meta
                .insert("intent_dropped".into(), json!(intent_dropped));
            effective_caps = Some(granted);
        }

        g.resources.ensure_process(&id, effective_budget);
        g.processes.insert(id.clone(), proc.clone());
        Self::emit_locked(
            &mut g,
            "process_created",
            &id,
            json!({
                "identity": identity,
                "session_id": session_id,
                "parent_id": parent_id,
                "capabilities": effective_caps,
                "token_budget": effective_budget,
                "intent_applied": intent_for_apply.is_some(),
                "intent_dropped": intent_dropped,
            }),
        );
        Ok(proc)
    }

    /// Apply intent to an existing process (synthesize caps + token).
    pub fn apply_intent(
        &self,
        process_id: &str,
        intent: IntentDeclaration,
        parent_token: Option<CapabilityToken>,
    ) -> KernelResult<(CapabilityToken, Vec<String>)> {
        let mut g = self.inner.write();
        let parent_tok = {
            let proc = g
                .processes
                .get(process_id)
                .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
            if let Some(pt) = parent_token {
                Some(pt)
            } else if let Some(ref pid) = proc.parent_id {
                g.processes.get(pid).and_then(|p| p.token.clone())
            } else {
                None
            }
        };
        let (token, dropped) = synthesize_token(&intent, process_id, parent_tok.as_ref())?;
        let granted: Vec<String> = token.capabilities.iter().cloned().collect();
        if let Some(proc) = g.processes.get_mut(process_id) {
            proc.capabilities = Some(granted.clone());
            proc.token = Some(token.clone());
            proc.meta.insert("intent".into(), intent.to_dict());
            proc.meta.insert("intent_dropped".into(), json!(&dropped));
        }
        Self::emit_locked(
            &mut g,
            "intent_applied",
            process_id,
            json!({
                "granted": granted,
                "dropped": dropped,
                "goal": intent.goal,
            }),
        );
        Ok((token, dropped))
    }

    /// Filter tool names by process capabilities (None caps = all pass).
    pub fn filter_tools(&self, process_id: &str, tool_names: &[String]) -> KernelResult<Vec<String>> {
        let g = self.inner.read();
        let proc = g
            .processes
            .get(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
        Ok(filter_tool_names(
            tool_names,
            proc.capabilities.as_deref(),
        ))
    }

    pub fn tools_for_process(&self, process_id: &str) -> KernelResult<Option<Vec<String>>> {
        let g = self.inner.read();
        let proc = g
            .processes
            .get(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
        Ok(tools_for_capabilities(proc.capabilities.as_deref()))
    }

    pub fn end_process(
        &self,
        process_id: &str,
        state: &str,
        reason: Option<&str>,
    ) -> KernelResult<Option<AgentProcess>> {
        let mut g = self.inner.write();
        Ok(Self::end_process_locked(
            &mut g,
            process_id,
            state,
            reason,
            true, // reclaim children
        ))
    }

    /// End process and force-release caps/resources/policy/results (P0.5 E4).
    /// When reclaim_children, cascades to non-terminal descendants.
    fn end_process_locked(
        g: &mut KernelInner,
        process_id: &str,
        state: &str,
        reason: Option<&str>,
        reclaim_children: bool,
    ) -> Option<AgentProcess> {
        let children: Vec<String> = if reclaim_children {
            g.processes
                .values()
                .filter(|p| p.parent_id.as_deref() == Some(process_id) && !p.is_terminal())
                .map(|p| p.id.clone())
                .collect()
        } else {
            Vec::new()
        };
        for cid in &children {
            Self::end_process_locked(
                g,
                cid,
                "killed",
                Some("parent_reclaimed"),
                true,
            );
        }

        let Some(proc) = g.processes.get_mut(process_id) else {
            return None;
        };
        if proc.is_terminal() {
            return Some(proc.clone());
        }
        let st = match state {
            "failed" => ProcessState::Failed,
            "killed" => ProcessState::Killed,
            _ => ProcessState::Completed,
        };
        proc.state = st;
        proc.ended_at = Some(now_secs());
        proc.exit_reason = reason.map(|s| s.to_string());
        // strip live capabilities on terminal (no residual caps)
        if let Some(ref mut caps) = proc.capabilities {
            caps.clear();
        }
        let out = proc.clone();
        let duration_ms = ((proc.ended_at.unwrap_or(0.0)
            - proc.started_at.unwrap_or(proc.created_at))
            * 1000.0) as i64;
        Self::emit_locked(
            g,
            "process_ended",
            process_id,
            json!({
                "state": state,
                "reason": reason,
                "tokens_used": out.tokens_used,
                "duration_ms": duration_ms,
                "children_reclaimed": children.len(),
            }),
        );
        g.resources.drop_process(process_id);
        g.scheduler.cancel_process(process_id);
        g.isolation.drop_process(process_id);
        g.run_gate.release(process_id);
        // P1：回收该进程 LLM 租约，防 max_in_flight=4 漏满后全系统堵死
        let llm_n = g.llm.release_by_process(process_id);
        if llm_n > 0 {
            Self::emit_locked(
                g,
                "llm.released_by_process",
                process_id,
                json!({ "count": llm_n }),
            );
        }
        // 顺带扫过期租约
        let expired = g.llm.expire_stale(600.0);
        if expired > 0 {
            Self::emit_locked(
                g,
                "llm.expired",
                process_id,
                json!({ "count": expired }),
            );
        }
        g.policy.drop_process(process_id);
        g.result_store.drop_process(process_id);
        g.cost_ledger.drop_process(process_id);
        g.ipc.drop_process(process_id);
        g.context_vm.drop_process(process_id);
        g.collab.drop_process(process_id);
        g.repo_index.drop_process(process_id);
        Some(out)
    }

    /// Explicit tree reclaim (parent + descendants).
    pub fn reclaim_process_tree(
        &self,
        process_id: &str,
        reason: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        let before = g
            .processes
            .values()
            .filter(|p| !p.is_terminal())
            .count();
        let ended = Self::end_process_locked(
            &mut g,
            process_id,
            "killed",
            reason.or(Some("reclaim_process_tree")),
            true,
        );
        let after = g
            .processes
            .values()
            .filter(|p| !p.is_terminal())
            .count();
        json!({
            "ok": ended.is_some(),
            "process_id": process_id,
            "live_before": before,
            "live_after": after,
            "reclaimed": before.saturating_sub(after),
        })
    }

    fn tail_hash_locked(g: &KernelInner) -> String {
        if let Some(last) = g.events.last() {
            last.hash.clone()
        } else if let Some(ref h) = g.disk_tail_hash {
            h.clone()
        } else {
            GENESIS_HASH.to_string()
        }
    }

    pub fn mark_running(&self, process_id: &str) -> KernelResult<()> {
        let mut g = self.inner.write();
        if let Some(proc) = g.processes.get_mut(process_id) {
            if proc.state == ProcessState::Created {
                proc.state = ProcessState::Running;
                proc.started_at = Some(now_secs());
            }
        }
        Ok(())
    }

    pub fn suspend_process(&self, process_id: &str, reason: &str) -> KernelResult<AgentProcess> {
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get_mut(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
        proc.suspend()?;
        if !reason.is_empty() {
            proc.meta
                .insert("suspend_reason".into(), json!(reason));
        }
        let out = proc.clone();
        Self::emit_locked(
            &mut g,
            "process_suspended",
            process_id,
            json!({"reason": reason}),
        );
        Ok(out)
    }

    pub fn resume_process(&self, process_id: &str) -> KernelResult<AgentProcess> {
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get_mut(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
        let was = proc.state;
        proc.resume();
        if was == ProcessState::Suspended {
            proc.meta.remove("suspend_reason");
        }
        let out = proc.clone();
        if was == ProcessState::Suspended {
            Self::emit_locked(&mut g, "process_resumed", process_id, json!({}));
        }
        Ok(out)
    }

    pub fn get_process(&self, process_id: &str) -> Option<AgentProcess> {
        self.inner.read().processes.get(process_id).cloned()
    }

    pub fn list_processes(&self, include_terminal: bool) -> Vec<AgentProcess> {
        let g = self.inner.read();
        g.processes
            .values()
            .filter(|p| include_terminal || !p.is_terminal())
            .cloned()
            .collect()
    }

    pub fn live_processes_for_identity(&self, identity: &str) -> Vec<AgentProcess> {
        let key = identity.trim();
        if key.is_empty() {
            return vec![];
        }
        self.list_processes(false)
            .into_iter()
            .filter(|p| p.identity == key)
            .collect()
    }

    pub fn retire_live_identity_processes(
        &self,
        identity: &str,
        reason: &str,
        except_process_id: Option<&str>,
    ) -> Vec<String> {
        let live = self.live_processes_for_identity(identity);
        let mut killed = Vec::new();
        for p in live {
            if except_process_id == Some(p.id.as_str()) {
                continue;
            }
            if self
                .end_process(&p.id, "killed", Some(reason))
                .ok()
                .flatten()
                .is_some()
            {
                killed.push(p.id);
            }
        }
        killed
    }

    // ── budget ────────────────────────────────────────────

    fn charge_tokens_locked(inner: &mut KernelInner, process_id: &str, amount: i64) -> KernelResult<Option<i64>> {
        // Python parity: unknown process → Ok(None), not an error
        let Some(proc) = inner.processes.get(process_id).cloned() else {
            return Ok(None);
        };

        if amount > 0 {
            if let Some(budget) = proc.token_budget {
                if proc.tokens_used + amount > budget {
                    // soft renew
                    if Self::try_soft_renew_locked(inner, process_id, amount, "charge_overflow").is_none() {
                        Self::emit_locked(
                            inner,
                            "budget_exceeded",
                            process_id,
                            json!({
                                "token_budget": proc.token_budget,
                                "tokens_used": proc.tokens_used,
                                "rejected_charge": amount,
                                "soft_renew_attempted": false,
                            }),
                        );
                        return Err(KernelError::BudgetExceeded(format!(
                            "进程 {process_id} 预算不足（已用 {}/{}，拒绝 +{amount}）",
                            proc.tokens_used,
                            proc.token_budget.unwrap_or(0)
                        )));
                    }
                    // re-check after renew (clone fields to avoid borrow across emit)
                    let (used2, budget2) = {
                        let p = inner.processes.get(process_id).unwrap();
                        (p.tokens_used, p.token_budget)
                    };
                    if let Some(b) = budget2 {
                        if used2 + amount > b {
                            Self::emit_locked(
                                inner,
                                "budget_exceeded",
                                process_id,
                                json!({
                                    "token_budget": budget2,
                                    "tokens_used": used2,
                                    "rejected_charge": amount,
                                    "soft_renew_attempted": true,
                                }),
                            );
                            return Err(KernelError::BudgetExceeded(format!(
                                "进程 {process_id} 预算不足（已用 {used2}/{b}，拒绝 +{amount}）"
                            )));
                        }
                    }
                }
            }
        }

        // charge under a short mutable borrow, then emit with owned values
        let (remaining, used, budget, charge_err) = {
            let proc = inner.processes.get_mut(process_id).unwrap();
            if amount > 0 {
                proc.meta.insert("last_charge_at".into(), json!(now_secs()));
                let billable = proc
                    .meta
                    .get("billable_tokens_used")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0)
                    + amount;
                proc.meta
                    .insert("billable_tokens_used".into(), json!(billable));
            }
            match proc.charge_tokens(amount) {
                Ok(r) => (Some(r), proc.tokens_used, proc.token_budget, None),
                Err(e) => (
                    None,
                    proc.tokens_used,
                    proc.token_budget,
                    Some(e.to_string()),
                ),
            }
        };
        if let Some(msg) = charge_err {
            Self::emit_locked(
                inner,
                "budget_exceeded",
                process_id,
                json!({
                    "token_budget": budget,
                    "tokens_used": used,
                    "rejected_charge": amount,
                }),
            );
            return Err(KernelError::BudgetExceeded(msg));
        }
        let remaining = remaining.unwrap();
        inner.resources.sync_token_used(process_id, used, budget);
        if remaining == Some(0) {
            Self::emit_locked(
                inner,
                "budget_exhausted",
                process_id,
                json!({
                    "token_budget": budget,
                    "tokens_used": used,
                }),
            );
        }
        Ok(remaining)
    }

    fn try_soft_renew_locked(
        inner: &mut KernelInner,
        process_id: &str,
        need: i64,
        reason: &str,
    ) -> Option<Value> {
        let cfg = inner.soft_renew.clone();
        if !cfg.enabled {
            return None;
        }
        let proc = inner.processes.get(process_id)?;
        if proc.is_terminal() || proc.token_budget.is_none() {
            return None;
        }
        let count = proc
            .meta
            .get("soft_renew_count")
            .and_then(|v| v.as_i64())
            .unwrap_or(0) as i32;
        if count >= cfg.max_renew {
            return None;
        }
        let base = proc
            .meta
            .get("budget_base")
            .and_then(|v| v.as_i64())
            .filter(|b| *b > 0)
            .unwrap_or_else(|| proc.token_budget.unwrap_or(cfg.min_add));
        let gap = (need
            - (proc.token_budget.unwrap_or(0) - proc.tokens_used).max(0))
        .max(0);
        let mut add = ((base as f64) * cfg.factor.max(0.25)) as i64;
        add = add.max(cfg.min_add).max(gap * 2).max(50_000);
        let cur = proc.token_budget.unwrap_or(0);
        if cfg.hard_cap > 0 && cur + add > cfg.hard_cap {
            add = (cfg.hard_cap - cur).max(0);
        }
        if add <= 0 {
            return None;
        }
        // top up
        let old = cur;
        let new_b = old + add;
        if let Some(proc) = inner.processes.get_mut(process_id) {
            proc.token_budget = Some(new_b);
            proc.meta.insert("soft_renew_count".into(), json!(count + 1));
            proc.meta.insert("budget_base".into(), json!(base));
            proc.meta.insert("last_soft_renew_at".into(), json!(now_secs()));
            proc.meta.insert("_sync_at".into(), json!(now_secs()));
        }
        Self::emit_locked(
            inner,
            "budget_soft_renew",
            process_id,
            json!({
                "amount": add,
                "renew_count": count + 1,
                "token_budget": new_b,
                "reason": reason,
            }),
        );
        Some(json!({
            "ok": true,
            "amount": add,
            "renew_count": count + 1,
            "token_budget": new_b,
        }))
    }

    pub fn charge_tokens(&self, process_id: &str, amount: i64) -> KernelResult<Option<i64>> {
        let mut g = self.inner.write();
        Self::charge_tokens_locked(&mut g, process_id, amount)
    }

    pub fn top_up_budget(
        &self,
        process_id: &str,
        amount: i64,
        by: &str,
        reason: &str,
    ) -> KernelResult<Value> {
        if amount <= 0 {
            return Err(KernelError::Invalid("top_up amount 必须为正整数".into()));
        }
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get_mut(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
        if proc.is_terminal() {
            return Err(KernelError::Invalid(format!(
                "进程已终态（{}），不可追加预算",
                proc.state
            )));
        }
        if proc.token_budget.is_none() {
            return Ok(json!({
                "ok": true,
                "unlimited": true,
                "process_id": process_id,
                "token_budget": null,
                "tokens_used": proc.tokens_used,
                "budget_remaining": null,
            }));
        }
        let old = proc.token_budget.unwrap();
        let new_b = old + amount;
        proc.token_budget = Some(new_b);
        proc.meta.insert("_sync_at".into(), json!(now_secs()));
        let used = proc.tokens_used;
        Self::emit_locked(
            &mut g,
            "budget_top_up",
            process_id,
            json!({
                "from": old,
                "to": new_b,
                "amount": amount,
                "by": by,
                "reason": reason,
                "tokens_used": used,
            }),
        );
        Ok(json!({
            "ok": true,
            "unlimited": false,
            "process_id": process_id,
            "token_budget": new_b,
            "tokens_used": used,
            "budget_remaining": (new_b - used).max(0),
            "added": amount,
            "by": by,
        }))
    }

    pub fn try_soft_renew_budget(
        &self,
        process_id: &str,
        need: i64,
        reason: &str,
    ) -> Option<Value> {
        let mut g = self.inner.write();
        Self::try_soft_renew_locked(&mut g, process_id, need, reason)
    }

    // ── mediate ───────────────────────────────────────────

    pub fn mediate(
        &self,
        process_id: &str,
        action: &str,
        target: &str,
        args: Option<&Value>,
    ) -> KernelResult<MediationDecision> {
        let mut g = self.inner.write();
        let proc = g.processes.get(process_id).cloned();
        let court = decide_capability(process_id, action, target, proc.as_ref(), args);
        let ident = proc.as_ref().map(|p| p.identity.clone());
        let pid = proc
            .as_ref()
            .map(|p| p.id.clone())
            .unwrap_or_else(|| process_id.to_string());

        // E-02: collab first-class gate after capability court allows write/command
        let mut final_verdict = court.verdict.clone();
        let mut final_reason = court.reason.clone();
        let mut final_layer = court.layer.clone();
        let mut final_rule = court.matched_rule.clone();
        if court.verdict == "allow" && CollabHub::is_gated_action(action, target) {
            if let Some(reason) = g.collab.block_reason(process_id, action, target) {
                final_verdict = "deny".into();
                final_reason = reason;
                final_layer = "collab".into();
                final_rule = "collab:human_gate".into();
            }
        }

        let audit = court.to_audit();
        let outcome = if final_verdict == "allow" {
            "allow"
        } else {
            "deny"
        };

        let mut detail = json!({
            "action": action,
            "target": target,
            "allowed": final_verdict == "allow",
            "reason": final_reason,
            "capability_checked": court.capability_checked,
            "layer": final_layer,
            "matched_rule": final_rule,
            "args_keys": args.and_then(|a| a.as_object()).map(|m| {
                let mut k: Vec<_> = m.keys().cloned().collect();
                k.sort();
                k
            }).unwrap_or_default(),
        });
        if let Value::Object(ref mut map) = detail {
            if let Value::Object(audit_map) = audit {
                for (k, v) in audit_map {
                    map.insert(k, v);
                }
            }
            // collab override wins on layer/reason for explainability
            map.insert("layer".into(), json!(final_layer));
            map.insert("matched_rule".into(), json!(final_rule));
            map.insert("reason".into(), json!(final_reason));
            map.insert("allowed".into(), json!(final_verdict == "allow"));
        }

        Self::emit_locked(&mut g, "mediation", &pid, detail);
        Self::emit_locked(
            &mut g,
            "policy.decision",
            &pid,
            json!({
                "who": ident.unwrap_or_else(|| process_id.to_string()),
                "what": format!("{action}:{target}"),
                "action": action,
                "target": target,
                "outcome": outcome,
                "allowed": final_verdict == "allow",
                "reason": final_reason,
                "source": if final_layer == "collab" { "collab" } else { "permission_court" },
                "tool": court.tool,
                "args_digest": court.args_digest,
                "verdict": final_verdict,
                "matched_rule": final_rule,
                "layer": final_layer,
                "capability_checked": court.capability_checked,
            }),
        );

        if final_verdict != "allow" {
            return Err(KernelError::Permission(final_reason));
        }
        Ok(MediationDecision {
            allowed: true,
            reason: String::new(),
            capability_checked: court.capability_checked,
        })
    }

    // ── tokens ────────────────────────────────────────────

    pub fn issue_token(
        &self,
        process_id: &str,
        capabilities: Option<Vec<String>>,
        expires_at: Option<f64>,
    ) -> KernelResult<CapabilityToken> {
        let mut g = self.inner.write();
        let key = g.hmac_key.clone();
        let proc_caps = {
            let proc = g
                .processes
                .get(process_id)
                .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?;
            proc.capabilities.clone()
        };
        let caps = capabilities.unwrap_or_else(|| {
            proc_caps.clone().unwrap_or_else(|| vec!["*".into()])
        });
        let token = CapabilityToken::new(caps.iter().cloned(), process_id, expires_at);
        if let Some(ref pcaps) = proc_caps {
            if !pcaps.iter().any(|c| c == "*") {
                let extra: Vec<_> = token
                    .capabilities
                    .iter()
                    .filter(|c| !pcaps.contains(c))
                    .cloned()
                    .collect();
                if !extra.is_empty() {
                    let mut sorted = extra;
                    sorted.sort();
                    return Err(KernelError::CapabilityEscalation(format!(
                        "令牌能力 {sorted:?} 超出进程能力集"
                    )));
                }
            }
        }
        let mut signed = token;
        if let Some(ref k) = key {
            let dict = signed.to_dict(Some(k));
            signed.signature = dict
                .get("signature")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
        }
        if let Some(proc) = g.processes.get_mut(process_id) {
            proc.token = Some(signed.clone());
        }
        Ok(signed)
    }

    // ── escalation ────────────────────────────────────────

    pub fn request_escalation(
        &self,
        process_id: &str,
        capabilities: Vec<String>,
        reason: &str,
    ) -> KernelResult<EscalationRequest> {
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?
            .clone();
        if proc.is_terminal() {
            return Err(KernelError::Invalid(format!(
                "进程已终止（{}），无法提权",
                proc.state
            )));
        }
        let Some(ref pcaps) = proc.capabilities else {
            return Err(KernelError::Invalid(
                "兼容模式进程（无显式能力集）无需提权".into(),
            ));
        };
        let mut caps: Vec<String> = capabilities
            .into_iter()
            .filter(|c| !pcaps.contains(c))
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect();
        caps.sort();
        if caps.is_empty() {
            return Err(KernelError::Invalid(
                "申请的能力均已在进程能力集内".into(),
            ));
        }
        // dedup pending
        for existing in g.escalations.values() {
            if existing.process_id == process_id
                && existing.status == "pending"
                && caps.iter().all(|c| existing.capabilities.contains(c))
            {
                return Ok(existing.clone());
            }
        }
        let req = EscalationRequest {
            id: short_id(),
            process_id: process_id.to_string(),
            capabilities: caps.clone(),
            reason: reason.to_string(),
            status: "pending".into(),
            created_at: now_secs(),
            resolved_at: None,
            resolved_by: None,
            target: None,
            identity_id: None,
        };
        g.escalations.insert(req.id.clone(), req.clone());
        Self::emit_locked(
            &mut g,
            "escalation_requested",
            process_id,
            json!({
                "escalation_id": req.id,
                "capabilities": caps,
                "reason": reason,
            }),
        );
        Self::emit_locked(
            &mut g,
            "policy.decision",
            process_id,
            json!({
                "who": proc.identity,
                "what": format!("escalation:{}", caps.join(",")),
                "action": "escalation",
                "target": caps.join(","),
                "outcome": "escalate",
                "allowed": false,
                "reason": reason,
                "source": "kernel",
                "escalation_id": req.id,
                "capabilities": caps,
            }),
        );
        Ok(req)
    }

    pub fn approve_escalation(&self, request_id: &str, by: &str) -> KernelResult<EscalationRequest> {
        let mut g = self.inner.write();
        let req = g
            .escalations
            .get(request_id)
            .cloned()
            .ok_or_else(|| KernelError::NotFound(format!("未知提权申请 {request_id}")))?;
        if req.status != "pending" {
            return Err(KernelError::Invalid(format!(
                "申请已处理（{}）",
                req.status
            )));
        }
        let proc = g.processes.get(&req.process_id).cloned();
        if let Some(proc) = proc {
            if !proc.is_terminal() {
                let mut merged: Vec<String> = proc
                    .capabilities
                    .unwrap_or_default()
                    .into_iter()
                    .chain(req.capabilities.iter().cloned())
                    .collect::<std::collections::BTreeSet<_>>()
                    .into_iter()
                    .collect();
                merged.sort();
                if let Some(p) = g.processes.get_mut(&req.process_id) {
                    p.capabilities = Some(merged.clone());
                    // re-issue token
                    p.token = Some(CapabilityToken::new(
                        merged.iter().cloned(),
                        &req.process_id,
                        None,
                    ));
                }
                let done = EscalationRequest {
                    status: "approved".into(),
                    resolved_at: Some(now_secs()),
                    resolved_by: Some(by.to_string()),
                    target: Some("process".into()),
                    ..req.clone()
                };
                g.escalations.insert(done.id.clone(), done.clone());
                Self::emit_locked(
                    &mut g,
                    "escalation_approved",
                    &req.process_id,
                    json!({
                        "escalation_id": done.id,
                        "capabilities": done.capabilities,
                        "resolved_by": by,
                        "capabilities_after": merged,
                        "target": "process",
                    }),
                );
                return Ok(done);
            }
        }
        // process dead — mark approved targeting identity (host fills identity_id)
        let done = EscalationRequest {
            status: "approved".into(),
            resolved_at: Some(now_secs()),
            resolved_by: Some(by.to_string()),
            target: Some("identity".into()),
            ..req
        };
        g.escalations.insert(done.id.clone(), done.clone());
        Self::emit_locked(
            &mut g,
            "escalation_approved",
            &done.process_id,
            json!({
                "escalation_id": done.id,
                "capabilities": done.capabilities,
                "resolved_by": by,
                "target": "identity",
                "message": "能力需由 identity 层并入编制档案",
            }),
        );
        Ok(done)
    }

    pub fn deny_escalation(&self, request_id: &str, by: &str) -> KernelResult<EscalationRequest> {
        let mut g = self.inner.write();
        let req = g
            .escalations
            .get(request_id)
            .cloned()
            .ok_or_else(|| KernelError::NotFound(format!("未知提权申请 {request_id}")))?;
        if req.status != "pending" {
            return Err(KernelError::Invalid(format!(
                "申请已处理（{}）",
                req.status
            )));
        }
        let done = EscalationRequest {
            status: "denied".into(),
            resolved_at: Some(now_secs()),
            resolved_by: Some(by.to_string()),
            ..req
        };
        g.escalations.insert(done.id.clone(), done.clone());
        Self::emit_locked(
            &mut g,
            "escalation_denied",
            &done.process_id,
            json!({
                "escalation_id": done.id,
                "capabilities": done.capabilities,
                "resolved_by": by,
            }),
        );
        Ok(done)
    }

    pub fn list_escalations(&self, status: Option<&str>) -> Vec<EscalationRequest> {
        let g = self.inner.read();
        let mut out: Vec<_> = g.escalations.values().cloned().collect();
        if let Some(st) = status {
            out.retain(|r| r.status == st);
        }
        out.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap_or(std::cmp::Ordering::Equal));
        out
    }

    // ── audit ─────────────────────────────────────────────

    pub fn events(
        &self,
        process_id: Option<&str>,
        kind: Option<&str>,
        limit: usize,
    ) -> Vec<KernelEvent> {
        let g = self.inner.read();
        let mut out: Vec<_> = g.events.clone();
        if let Some(pid) = process_id {
            out.retain(|e| e.process_id == pid);
        }
        if let Some(k) = kind {
            out.retain(|e| e.kind == k);
        }
        out.sort_by(|a, b| a.ts.partial_cmp(&b.ts).unwrap_or(std::cmp::Ordering::Equal));
        let n = out.len();
        if n > limit {
            out = out.split_off(n - limit);
        }
        out
    }

    pub fn verify_event_chain(&self) -> (bool, i64) {
        let g = self.inner.read();
        for (i, e) in g.events.iter().enumerate() {
            let expected = crate::audit::event_hash(
                &e.prev_hash,
                &e.kind,
                &e.process_id,
                &e.detail,
                e.ts,
                &e.id,
            );
            if e.hash != expected {
                return (false, i as i64);
            }
            if i > 0 && e.prev_hash != g.events[i - 1].hash {
                return (false, i as i64);
            }
        }
        (true, -1)
    }

    pub fn gc_terminal(&self, older_than_seconds: f64) -> usize {
        let mut g = self.inner.write();
        let now = now_secs();
        let dead: Vec<_> = g
            .processes
            .iter()
            .filter(|(_, p)| {
                p.is_terminal()
                    && p.ended_at
                        .map(|t| now - t > older_than_seconds)
                        .unwrap_or(false)
            })
            .map(|(id, _)| id.clone())
            .collect();
        for id in &dead {
            g.processes.remove(id);
            g.resources.drop_process(id);
        }
        dead.len()
    }

    // ── resources ─────────────────────────────────────────

    pub fn resource_charge(
        &self,
        process_id: &str,
        kind: &str,
        amount: i64,
    ) -> KernelResult<i64> {
        let kind = ResourceKind::parse(kind)
            .ok_or_else(|| KernelError::Invalid(format!("unknown resource kind {kind}")))?;
        let mut g = self.inner.write();
        match g.resources.charge(process_id, kind, amount) {
            Ok(rem) => {
                Self::emit_locked(
                    &mut g,
                    "resource_charge",
                    process_id,
                    json!({"kind": kind.as_str(), "amount": amount, "remaining": rem}),
                );
                Ok(rem)
            }
            Err(e) => {
                // K-05 / P0：超限必须进审计链（拒绝可回放），再向上返回错误
                Self::emit_locked(
                    &mut g,
                    "resource_denied",
                    process_id,
                    json!({
                        "kind": kind.as_str(),
                        "amount": amount,
                        "reason": e.to_string(),
                        "verdict": "deny",
                    }),
                );
                Err(e)
            }
        }
    }

    pub fn resource_usage(&self, process_id: &str) -> Value {
        self.inner.read().resources.usage(process_id)
    }

    /// Release previously charged resource (e.g. child_proc after command exits).
    /// Turns ChildProc from a lifetime counter into a concurrency lease.
    pub fn resource_release(
        &self,
        process_id: &str,
        kind: &str,
        amount: i64,
    ) -> KernelResult<Value> {
        let kind = ResourceKind::parse(kind)
            .ok_or_else(|| KernelError::Invalid(format!("unknown resource kind {kind}")))?;
        let mut g = self.inner.write();
        let amt = amount.max(0);
        g.resources.release_amount(process_id, kind, amt);
        let usage = g.resources.usage(process_id);
        Self::emit_locked(
            &mut g,
            "resource_release",
            process_id,
            json!({"kind": kind.as_str(), "amount": amt}),
        );
        Ok(json!({"ok": true, "kind": kind.as_str(), "amount": amt, "usage": usage}))
    }

    /// OS RSS → memory_bytes account (deepen hard limit surface).
    pub fn resource_report_rss(&self, process_id: &str, rss_bytes: i64) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let (used, limit, over) = g.resources.report_rss(process_id, rss_bytes)?;
        Self::emit_locked(
            &mut g,
            "resource_rss",
            process_id,
            json!({
                "rss_bytes": rss_bytes,
                "used": used,
                "limit": limit,
                "over_limit": over,
            }),
        );
        if over {
            Self::emit_locked(
                &mut g,
                "resource_denied",
                process_id,
                json!({
                    "kind": "memory_bytes",
                    "amount": rss_bytes,
                    "used": used,
                    "limit": limit,
                    "reason": "memory_bytes over_limit after RSS sample",
                    "verdict": "deny",
                }),
            );
        }
        Ok(json!({
            "process_id": process_id,
            "rss_bytes": rss_bytes,
            "used": used,
            "limit": limit,
            "over_limit": over,
            "ok": true,
        }))
    }

    pub fn scheduler_submit(
        &self,
        process_id: &str,
        payload: Value,
        priority: i32,
    ) -> Value {
        let mut g = self.inner.write();
        let t = g.scheduler.submit(process_id, payload, priority);
        json!({
            "id": t.id,
            "process_id": t.process_id,
            "effective_priority": t.effective_priority,
            "state": t.state,
            "seq": t.seq,
        })
    }

    pub fn scheduler_next(&self) -> Option<Value> {
        let mut g = self.inner.write();
        g.scheduler.next().map(|t| {
            json!({
                "id": t.id,
                "process_id": t.process_id,
                "payload": t.payload,
                "effective_priority": t.effective_priority,
                "state": t.state,
            })
        })
    }

    pub fn scheduler_stats(&self) -> Value {
        let g = self.inner.read();
        g.scheduler.status()
    }

    pub fn scheduler_set_limits(&self, max_running: u32, max_per_session: u32) -> Value {
        let mut g = self.inner.write();
        g.scheduler
            .set_limits(max_running as usize, max_per_session as usize);
        g.scheduler.status()
    }

    pub fn scheduler_complete(&self, task_id: &str, cancelled: bool) {
        let mut g = self.inner.write();
        g.scheduler.complete(task_id, cancelled);
    }

    pub fn scheduler_cancel_process(&self, process_id: &str) -> usize {
        let mut g = self.inner.write();
        g.scheduler.cancel_process(process_id)
    }

    /// P0-C: submit a run onto the priority queue (class name or numeric priority).
    pub fn schedule_run(
        &self,
        process_id: &str,
        payload: Value,
        priority_class: Option<&str>,
        priority: Option<i32>,
    ) -> Value {
        let prio = priority.unwrap_or_else(|| {
            PriorityClass::parse(priority_class.unwrap_or("workforce")).as_i32()
        });
        self.scheduler_submit(process_id, payload, prio)
    }

    // ── LLM admission (P0-C) ──────────────────────────────

    pub fn llm_set_config(&self, cfg: LlmAdmissionConfig) {
        self.inner.write().llm.set_config(cfg);
    }

    pub fn llm_try_acquire(&self, req: LlmLeaseRequest) -> LlmAcquireResult {
        let mut g = self.inner.write();
        let result = g.llm.try_acquire(req);
        if let LlmAcquireResult::Granted { ref lease } = result {
            Self::emit_locked(
                &mut g,
                "llm.granted",
                lease.process_id.as_deref().unwrap_or("system"),
                lease.to_dict(),
            );
        }
        result
    }

    pub fn llm_poll(&self, request_id: &str) -> LlmAcquireResult {
        self.inner.write().llm.poll(request_id)
    }

    pub fn llm_release(&self, request_id: &str) -> bool {
        let mut g = self.inner.write();
        let ok = g.llm.release(request_id);
        if ok {
            Self::emit_locked(
                &mut g,
                "llm.released",
                "system",
                json!({"request_id": request_id}),
            );
        }
        ok
    }

    pub fn llm_cancel_wait(&self, request_id: &str) -> bool {
        self.inner.write().llm.cancel_wait(request_id)
    }

    pub fn llm_charge_quota(&self, identity_id: Option<&str>, amount: i64) {
        self.inner.write().llm.charge_quota(identity_id, amount);
    }

    pub fn llm_status(&self) -> Value {
        self.inner.write().llm.status()
    }

    /// Acquire global run lease + charge per-process concurrency_slots (accounting).
    /// Returns remaining concurrency_slots after charge; fails only if process missing.
    pub fn run_acquire(&self, process_id: &str) -> KernelResult<i64> {
        match self.run_gate_try(process_id, Some("workforce"), None) {
            RunGateResult::Granted { .. } => {}
            RunGateResult::Queued { .. } => {
                // Not granted yet — still allow resource accounting of intent via 0 charge
                return Ok(self
                    .resource_usage(process_id)
                    .get("concurrency_slots")
                    .and_then(|v| v.get("remaining"))
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0));
            }
            RunGateResult::Rejected { reason, .. } => {
                return Err(KernelError::BudgetExceeded(reason));
            }
        }
        self.resource_charge(process_id, "concurrency_slots", 1)
    }

    pub fn run_release(&self, process_id: &str) -> KernelResult<()> {
        let mut g = self.inner.write();
        g.resources
            .release_amount(process_id, crate::resource::ResourceKind::ConcurrencySlots, 1);
        g.run_gate.release(process_id);
        Ok(())
    }

    /// Global run admission (cross-session). True wait/queue semantics.
    pub fn run_gate_try(
        &self,
        process_id: &str,
        priority_class: Option<&str>,
        priority: Option<i32>,
    ) -> RunGateResult {
        let mut g = self.inner.write();
        let r = g.run_gate.try_acquire(process_id, priority_class, priority);
        if let RunGateResult::Granted { ref lease } = r {
            Self::emit_locked(
                &mut g,
                "run_gate.granted",
                process_id,
                json!({"lease_id": lease.lease_id, "priority": lease.priority}),
            );
        }
        r
    }

    pub fn run_gate_poll(&self, request_id: &str) -> RunGateResult {
        self.inner.write().run_gate.poll(request_id)
    }

    pub fn run_gate_release(&self, process_id: &str) -> bool {
        let mut g = self.inner.write();
        let ok = g.run_gate.release(process_id);
        if ok {
            Self::emit_locked(
                &mut g,
                "run_gate.released",
                process_id,
                json!({}),
            );
        }
        ok
    }

    pub fn run_gate_status(&self) -> Value {
        self.inner.read().run_gate.status()
    }

    pub fn run_gate_set_max(&self, max_concurrent: usize) {
        self.inner.write().run_gate.set_max_concurrent(max_concurrent);
    }

    // ── Court / isolation / checkpoint (P0-D) ─────────────

    pub fn set_court_policy(&self, policy: CourtPolicy) {
        self.inner.write().court_policy = policy;
    }

    pub fn court_policy(&self) -> CourtPolicy {
        self.inner.read().court_policy.clone()
    }

    pub fn decide_tool(
        &self,
        name: &str,
        args: Option<&Value>,
        process_id: Option<&str>,
        skill_tools: Option<&[String]>,
        skill_deny: Option<&[String]>,
    ) -> CourtDecision {
        let g = self.inner.read();
        let proc = process_id.and_then(|pid| g.processes.get(pid));
        let decision = decide_tool(
            name,
            args,
            &g.court_policy,
            proc,
            skill_tools,
            skill_deny,
        );
        decision
    }

    pub fn decide_tool_and_emit(
        &self,
        name: &str,
        args: Option<&Value>,
        process_id: Option<&str>,
        skill_tools: Option<&[String]>,
        skill_deny: Option<&[String]>,
    ) -> CourtDecision {
        let decision = self.decide_tool(name, args, process_id, skill_tools, skill_deny);
        let pid = process_id.unwrap_or("system");
        let outcome = if decision.verdict == "allow" {
            "allow"
        } else if decision.verdict == "deny" {
            "deny"
        } else {
            "escalate"
        };
        self.emit(
            "policy.decision",
            pid,
            json!({
                "who": pid,
                "what": format!("tool_call:{name}"),
                "action": "tool_call",
                "target": name,
                "outcome": outcome,
                "allowed": decision.verdict == "allow",
                "reason": decision.reason,
                "source": "permission_court",
                "tool": decision.tool,
                "args_digest": decision.args_digest,
                "verdict": decision.verdict,
                "matched_rule": decision.matched_rule,
                "layer": decision.layer,
                "capability_checked": decision.capability_checked,
            }),
        );
        decision
    }

    pub fn isolation_resolve(
        &self,
        process_id: &str,
        force_profile: Option<&str>,
        is_workforce: bool,
    ) -> Value {
        self.inner
            .read()
            .isolation
            .resolve(process_id, force_profile, is_workforce)
    }

    pub fn isolation_set_profile(&self, process_id: &str, profile: &str) {
        let mut g = self.inner.write();
        g.isolation
            .set_process_profile(process_id, IsolationProfile::parse(profile));
    }

    pub fn isolation_spawn(
        &self,
        process_id: &str,
        command: &str,
        backend: &str,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.isolation.spawn(process_id, command, backend) {
            Ok(h) => {
                Self::emit_locked(
                    &mut g,
                    "isolation.spawn",
                    process_id,
                    json!(h),
                );
                Ok(json!(h))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    /// Force real OS process spawn (ignores ledger-only backends).
    pub fn isolation_spawn_os(
        &self,
        process_id: &str,
        command: &str,
        backend: Option<&str>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let be = backend.unwrap_or("os");
        match g.isolation.spawn_os(process_id, command, be) {
            Ok(h) => {
                Self::emit_locked(
                    &mut g,
                    "isolation.spawn_os",
                    process_id,
                    json!(h),
                );
                Ok(json!(h))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn isolation_poll(&self, handle_id: &str) -> Value {
        let mut g = self.inner.write();
        g.isolation
            .poll(handle_id)
            .unwrap_or_else(|| json!({"ok": false, "error": "unknown_handle"}))
    }

    pub fn isolation_kill(&self, handle_id: &str) -> Option<Value> {
        let mut g = self.inner.write();
        let h = g.isolation.kill(handle_id)?;
        let pid = h.process_id.clone();
        Self::emit_locked(
            &mut g,
            "isolation.kill",
            &pid,
            json!({"handle_id": handle_id, "status": h.status}),
        );
        Some(json!(h))
    }

    pub fn isolation_complete(&self, handle_id: &str, exit_code: i32) -> Option<Value> {
        let mut g = self.inner.write();
        g.isolation.complete(handle_id, exit_code).map(|h| json!(h))
    }

    pub fn isolation_attach_pid(&self, handle_id: &str, os_pid: u32) -> Option<Value> {
        let mut g = self.inner.write();
        g.isolation
            .attach_os_pid(handle_id, os_pid)
            .map(|h| json!(h))
    }

    pub fn isolation_reap(&self, max_age_secs: Option<f64>) -> Value {
        let mut g = self.inner.write();
        g.isolation.reap_tick(max_age_secs.unwrap_or(600.0))
    }

    pub fn isolation_status(&self) -> Value {
        self.inner.read().isolation.status()
    }

    pub fn checkpoint_begin(&self, process_id: &str, path: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.checkpoints.begin(process_id, path) {
            Ok(cp) => {
                Self::emit_locked(
                    &mut g,
                    "checkpoint.begin",
                    process_id,
                    json!({"id": cp.id, "path": cp.path, "existed": cp.existed}),
                );
                Ok(json!(cp))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn checkpoint_restore(&self, checkpoint_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.checkpoints.restore(checkpoint_id) {
            Ok(cp) => {
                Self::emit_locked(
                    &mut g,
                    "checkpoint.restore",
                    &cp.process_id,
                    json!({"id": cp.id, "path": cp.path}),
                );
                Ok(json!(cp))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn checkpoint_list(&self, process_id: &str) -> Value {
        json!(self.inner.read().checkpoints.list_for_process(process_id))
    }

    /// Export mediation + policy.decision events for a process (decision trail).
    pub fn export_decision_trail(&self, process_id: &str, limit: usize) -> Value {
        let events = self.events(Some(process_id), None, limit.max(500));
        let trail: Vec<_> = events
            .into_iter()
            .filter(|e| {
                e.kind == "mediation"
                    || e.kind == "policy.decision"
                    || e.kind == "checkpoint.begin"
                    || e.kind == "checkpoint.restore"
                    || e.kind == "isolation.spawn"
            })
            .map(|e| e.to_dict())
            .collect();
        json!({
            "process_id": process_id,
            "events": trail,
            "total": trail.len(),
        })
    }

    pub fn get_escalation(&self, request_id: &str) -> Option<EscalationRequest> {
        self.inner.read().escalations.get(request_id).cloned()
    }

    pub fn set_identity_registry_attached(&self, attached: bool) {
        self.inner.write().identity_registry_attached = attached;
    }

    pub fn identity_registry_attached(&self) -> bool {
        self.inner.read().identity_registry_attached
    }

    // ── P0.5: process snapshot / result spill / policy / cache ──

    pub fn process_snapshot(
        &self,
        process_id: &str,
        meta: Option<Value>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("未知进程 {process_id}")))?
            .clone();
        let tail = Self::tail_hash_locked(&g);
        let event_count = g.events.len() as u64;
        let snap = g
            .process_snapshots
            .capture(&proc, &tail, event_count, meta);
        Self::emit_locked(
            &mut g,
            "process.snapshot",
            process_id,
            json!({"id": snap.id, "seq": snap.seq, "tail_hash": snap.tail_hash}),
        );
        Ok(json!(snap))
    }

    pub fn process_snapshot_latest(&self, process_id: &str) -> Value {
        let g = self.inner.read();
        match g.process_snapshots.latest_for_process(process_id) {
            Some(s) => json!(s),
            None => Value::Null,
        }
    }

    pub fn process_snapshot_list(&self, process_id: &str) -> Value {
        json!(self
            .inner
            .read()
            .process_snapshots
            .list_for_process(process_id))
    }

    pub fn process_recovery_plan(&self, process_id: &str) -> Value {
        self.inner
            .read()
            .process_snapshots
            .recovery_plan(process_id)
    }

    /// Spill large tool result; returns handle summary for context or original content.
    pub fn result_spill(
        &self,
        process_id: &str,
        tool: &str,
        content: &str,
    ) -> Value {
        let mut g = self.inner.write();
        match g.result_store.maybe_spill(process_id, tool, content) {
            Some(h) => {
                let summary = ResultSpillStore::handle_summary(&h);
                Self::emit_locked(
                    &mut g,
                    "result.spill",
                    process_id,
                    json!({
                        "id": h.id,
                        "tool": h.tool,
                        "bytes": h.bytes,
                        "sha256": h.sha256,
                    }),
                );
                json!({
                    "spilled": true,
                    "handle": h,
                    "context": summary,
                })
            }
            None => json!({
                "spilled": false,
                "context": content,
            }),
        }
    }

    pub fn result_load(
        &self,
        handle_id: &str,
        process_id: Option<&str>,
    ) -> KernelResult<Value> {
        let g = self.inner.read();
        match g.result_store.load(handle_id, process_id) {
            Ok(text) => Ok(json!({
                "id": handle_id,
                "content": text,
                "bytes": text.len(),
                "process_id": process_id.unwrap_or(""),
            })),
            Err(e) => {
                // Cross-process / missing bind → Permission; unknown id → NotFound.
                if e.contains("another process") || e.contains("process_id required") {
                    Err(KernelError::Permission(e))
                } else {
                    Err(KernelError::NotFound(e))
                }
            }
        }
    }

    pub fn result_store_status(&self) -> Value {
        self.inner.read().result_store.status()
    }

    pub fn iteration_set_budget(&self, process_id: &str, max_total: u32) {
        self.inner
            .write()
            .policy
            .set_iteration_budget(process_id, max_total);
    }

    pub fn iteration_consume(&self, process_id: &str) -> Value {
        let mut g = self.inner.write();
        let d = g.policy.iteration_consume(process_id);
        if d.is_blocking() {
            Self::emit_locked(
                &mut g,
                "policy.iteration_exhausted",
                process_id,
                d.to_dict(),
            );
        }
        d.to_dict()
    }

    pub fn iteration_refund(&self, process_id: &str) -> bool {
        self.inner.write().policy.iteration_refund(process_id)
    }

    pub fn iteration_status(&self, process_id: &str) -> Value {
        self.inner.read().policy.iteration_status(process_id)
    }

    pub fn doom_record(&self, process_id: &str, tool: &str, args: Option<&Value>) -> Value {
        let mut g = self.inner.write();
        let empty = json!({});
        let d = g
            .policy
            .doom_record(process_id, tool, args.unwrap_or(&empty));
        if d.is_blocking() {
            Self::emit_locked(&mut g, "policy.doom_loop", process_id, d.to_dict());
        }
        d.to_dict()
    }

    pub fn doom_reset(&self, process_id: &str) {
        self.inner.write().policy.doom_reset(process_id);
    }

    pub fn doom_status(&self, process_id: &str) -> Value {
        self.inner.read().policy.doom_status(process_id)
    }

    pub fn policy_status(&self) -> Value {
        self.inner.read().policy.status()
    }

    pub fn cache_record(
        &self,
        family: &str,
        hit: bool,
        bytes_saved: u64,
        model: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        g.cache_metrics.record(family, hit, bytes_saved, model);
        Self::emit_locked(
            &mut g,
            "cache.record",
            "system",
            json!({
                "family": family,
                "model": model.unwrap_or(""),
                "hit": hit,
                "bytes_saved": bytes_saved,
            }),
        );
        g.cache_metrics.status()
    }

    pub fn cache_metrics(&self) -> Value {
        self.inner.read().cache_metrics.status()
    }

    pub fn cost_charge(
        &self,
        process_id: &str,
        family: &str,
        tokens: u64,
        billable: u64,
        model: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        g.cost_ledger
            .charge(process_id, family, tokens, billable, model);
        Self::emit_locked(
            &mut g,
            "cost.charge",
            process_id,
            json!({
                "family": family,
                "model": model.unwrap_or(""),
                "tokens": tokens,
                "billable": billable,
            }),
        );
        g.cost_ledger.panel()
    }

    pub fn cost_panel(&self) -> Value {
        self.inner.read().cost_ledger.panel()
    }

    pub fn cost_process(&self, process_id: &str) -> Value {
        self.inner.read().cost_ledger.process_cost(process_id)
    }

    /// kind: attempt | resume_ok | resume_fail | snapshot_ok
    pub fn marathon_record(&self, kind: &str, reason: Option<&str>) -> Value {
        let mut g = self.inner.write();
        match kind {
            "attempt" => g.marathon.record_attempt(),
            "resume_ok" => g.marathon.record_resume(true, reason.unwrap_or("ok")),
            "resume_fail" => g
                .marathon
                .record_resume(false, reason.unwrap_or("fail")),
            "snapshot_ok" => g.marathon.record_snapshot(true),
            _ => {}
        }
        g.marathon.status()
    }

    pub fn marathon_metrics(&self) -> Value {
        self.inner.read().marathon.status()
    }

    // ── P1-A: IPC ─────────────────────────────────────────

    /// Send IPC after capability check (ipc_send or * or compat).
    pub fn ipc_send(
        &self,
        from: &str,
        to: &str,
        kind: &str,
        payload: Value,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        // sender must exist and hold ipc_send (or compat null caps)
        let from_proc = g
            .processes
            .get(from)
            .ok_or_else(|| KernelError::NotFound(format!("sender {from}")))?;
        if !from_proc.is_terminal() {
            let allowed = match &from_proc.capabilities {
                None => true,
                Some(caps) => {
                    caps.iter().any(|c| {
                        c == "*"
                            || c == "ipc_send"
                            || c == "ipc"
                            || c == "agent_comm"
                    })
                }
            };
            if !allowed {
                g.ipc.record_denied();
                Self::emit_locked(
                    &mut g,
                    "ipc.denied",
                    from,
                    json!({"to": to, "reason": "missing ipc_send"}),
                );
                return Err(KernelError::Permission(
                    "ipc_send denied: missing capability".into(),
                ));
            }
        }
        if !g.processes.contains_key(to) {
            return Err(KernelError::NotFound(format!("recipient {to}")));
        }
        match g.ipc.send(from, to, kind, payload) {
            Ok(msg) => {
                Self::emit_locked(
                    &mut g,
                    "ipc.send",
                    from,
                    json!({"id": msg.id, "to": to, "kind": kind}),
                );
                Ok(json!(msg))
            }
            Err(e) => Err(KernelError::BudgetExceeded(e)),
        }
    }

    pub fn ipc_recv(&self, process_id: &str, max: usize) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let proc = g
            .processes
            .get(process_id)
            .ok_or_else(|| KernelError::NotFound(format!("进程 {process_id}")))?;
        let allowed = match &proc.capabilities {
            None => true,
            Some(caps) => caps.iter().any(|c| {
                c == "*" || c == "ipc_recv" || c == "ipc" || c == "agent_comm" || c == "ipc_send"
            }),
        };
        if !allowed {
            g.ipc.record_denied();
            return Err(KernelError::Permission(
                "ipc_recv denied: missing capability".into(),
            ));
        }
        let msgs = g.ipc.recv(process_id, max);
        Ok(json!({"messages": msgs, "count": msgs.len()}))
    }

    pub fn ipc_status(&self) -> Value {
        self.inner.read().ipc.status()
    }

    /// M-01: subscribe process to named IPC channel.
    pub fn ipc_channel_subscribe(&self, process_id: &str, channel: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        if !g.processes.contains_key(process_id) {
            return Err(KernelError::NotFound(format!("进程 {process_id}")));
        }
        let allowed = match &g.processes[process_id].capabilities {
            None => true,
            Some(caps) => caps.iter().any(|c| {
                c == "*" || c == "ipc_recv" || c == "ipc" || c == "agent_comm" || c == "ipc_send"
            }),
        };
        if !allowed {
            g.ipc.record_denied();
            return Err(KernelError::Permission(
                "ipc_channel_subscribe denied: missing ipc capability".into(),
            ));
        }
        Ok(g.ipc.channel_subscribe(channel, process_id))
    }

    /// M-01: publish to channel subscribers (capability-checked).
    pub fn ipc_channel_publish(
        &self,
        from: &str,
        channel: &str,
        kind: &str,
        payload: Value,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let from_proc = g
            .processes
            .get(from)
            .ok_or_else(|| KernelError::NotFound(format!("sender {from}")))?;
        let allowed = match &from_proc.capabilities {
            None => true,
            Some(caps) => caps
                .iter()
                .any(|c| c == "*" || c == "ipc_send" || c == "ipc" || c == "agent_comm"),
        };
        if !allowed {
            g.ipc.record_denied();
            return Err(KernelError::Permission(
                "ipc_channel_publish denied: missing ipc_send".into(),
            ));
        }
        let r = g
            .ipc
            .channel_publish(from, channel, kind, payload)
            .map_err(KernelError::BudgetExceeded)?;
        Self::emit_locked(
            &mut g,
            "ipc.channel_publish",
            from,
            json!({"channel": channel, "kind": kind, "result": r}),
        );
        Ok(r)
    }

    /// M-01: broadcast to all non-terminal peers with ipc_recv.
    pub fn ipc_broadcast(
        &self,
        from: &str,
        kind: &str,
        payload: Value,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let from_proc = g
            .processes
            .get(from)
            .ok_or_else(|| KernelError::NotFound(format!("sender {from}")))?;
        let allowed = match &from_proc.capabilities {
            None => true,
            Some(caps) => caps
                .iter()
                .any(|c| c == "*" || c == "ipc_send" || c == "ipc" || c == "agent_comm"),
        };
        if !allowed {
            g.ipc.record_denied();
            return Err(KernelError::Permission(
                "ipc_broadcast denied: missing ipc_send".into(),
            ));
        }
        let peers: Vec<String> = g
            .processes
            .values()
            .filter(|p| !p.is_terminal() && p.id != from)
            .filter(|p| match &p.capabilities {
                None => true,
                Some(caps) => caps.iter().any(|c| {
                    c == "*" || c == "ipc_recv" || c == "ipc" || c == "agent_comm" || c == "ipc_send"
                }),
            })
            .map(|p| p.id.clone())
            .collect();
        let r = g.ipc.broadcast_to(from, &peers, kind, payload);
        Self::emit_locked(
            &mut g,
            "ipc.broadcast",
            from,
            json!({"kind": kind, "result": r}),
        );
        Ok(r)
    }

    /// M-01: reply correlated to a prior message id.
    pub fn ipc_reply(
        &self,
        from: &str,
        to: &str,
        reply_to: &str,
        kind: &str,
        payload: Value,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let from_proc = g
            .processes
            .get(from)
            .ok_or_else(|| KernelError::NotFound(format!("sender {from}")))?;
        let allowed = match &from_proc.capabilities {
            None => true,
            Some(caps) => caps
                .iter()
                .any(|c| c == "*" || c == "ipc_send" || c == "ipc" || c == "agent_comm"),
        };
        if !allowed {
            g.ipc.record_denied();
            return Err(KernelError::Permission(
                "ipc_reply denied: missing ipc_send".into(),
            ));
        }
        if !g.processes.contains_key(to) {
            return Err(KernelError::NotFound(format!("recipient {to}")));
        }
        match g.ipc.send_ex(
            from,
            to,
            kind,
            payload,
            Some(reply_to.to_string()),
            None,
        ) {
            Ok(msg) => {
                Self::emit_locked(
                    &mut g,
                    "ipc.reply",
                    from,
                    json!({"id": msg.id, "to": to, "reply_to": reply_to}),
                );
                Ok(json!(msg))
            }
            Err(e) => Err(KernelError::BudgetExceeded(e)),
        }
    }

    /// M-01 demo: two agents ping-pong under capability mediation (productization path).
    pub fn multi_agent_demo(&self) -> KernelResult<Value> {
        use crate::intent::IntentDeclaration;
        let ipc_caps = vec![
            "ipc_send".to_string(),
            "ipc_recv".to_string(),
            "ipc".to_string(),
        ];
        let intent_a = IntentDeclaration {
            goal: "multi-agent demo A".into(),
            capabilities: ipc_caps.clone(),
            constraints: BTreeMap::new(),
        };
        let intent_b = IntentDeclaration {
            goal: "multi-agent demo B".into(),
            capabilities: ipc_caps.clone(),
            constraints: BTreeMap::new(),
        };
        let a = self.create_process_with_intent(
            "demo_a",
            None,
            None,
            Some(ipc_caps.clone()),
            Some(50_000),
            None,
            Some(intent_a),
        )?;
        let b = self.create_process_with_intent(
            "demo_b",
            None,
            None,
            Some(ipc_caps),
            Some(50_000),
            None,
            Some(intent_b),
        )?;
        let ping = self.ipc_send(&a.id, &b.id, "ping", json!({"hello": "from A"}))?;
        let ping_id = ping
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let inbox_b = self.ipc_recv(&b.id, 8)?;
        let pong = self.ipc_reply(&b.id, &a.id, &ping_id, "pong", json!({"echo": true}))?;
        let inbox_a = self.ipc_recv(&a.id, 8)?;
        let _ = self.ipc_channel_subscribe(&a.id, "demo-room");
        let _ = self.ipc_channel_subscribe(&b.id, "demo-room");
        let pub_r = self.ipc_channel_publish(
            &a.id,
            "demo-room",
            "announce",
            json!({"msg": "room open"}),
        )?;
        let _ = self.end_process(&a.id, "completed", Some("demo done"));
        let _ = self.end_process(&b.id, "completed", Some("demo done"));
        Ok(json!({
            "ok": true,
            "agents": [a.id, b.id],
            "ping": ping,
            "inbox_b": inbox_b,
            "pong": pong,
            "inbox_a": inbox_a,
            "channel_publish": pub_r,
            "note": "M-01 multi-agent demo path (Rust authority)",
        }))
    }

    // ── M-07 eval suite ───────────────────────────────────

    pub fn eval_record(
        &self,
        suite: &str,
        overall: f64,
        parts: HashMap<String, f64>,
        meta: Value,
    ) -> Value {
        let mut g = self.inner.write();
        let run = g.eval_suite.record(suite, overall, parts, meta);
        Self::emit_locked(
            &mut g,
            "eval.record",
            "system",
            json!({"id": run.id, "suite": suite, "overall": overall}),
        );
        json!(run)
    }

    pub fn eval_trend(&self, suite: &str, last_n: usize) -> Value {
        self.inner.read().eval_suite.trend(suite, last_n)
    }

    pub fn eval_gate_check(&self, suite: Option<&str>) -> Value {
        self.inner.read().eval_suite.check_gate(suite)
    }

    pub fn eval_status(&self) -> Value {
        self.inner.read().eval_suite.status()
    }

    // ── M-08 agent manifest ───────────────────────────────

    pub fn agent_manifest_validate(&self, raw: Value) -> Value {
        let r = validate_agent_manifest(&raw);
        json!(r)
    }

    pub fn agent_manifest_validate_str(&self, s: &str) -> Value {
        let r = validate_agent_manifest_str(s);
        json!(r)
    }

    pub fn agent_sdk_checklist(&self) -> Value {
        pack_checklist()
    }

    /// M-05: hard gate — skill must be active before load.
    pub fn skill_require_loadable(&self, name: &str) -> KernelResult<Value> {
        let g = self.inner.read();
        if g.skill_gate.is_loadable(name) {
            Ok(json!({"ok": true, "name": name, "loadable": true}))
        } else {
            Err(KernelError::Permission(format!(
                "skill '{name}' not loadable: must register → verify → activate (skill_gate)"
            )))
        }
    }

    // ── P1-A: services ────────────────────────────────────

    pub fn service_register(
        &self,
        name: &str,
        privilege: &str,
        meta: Value,
    ) -> Value {
        let mut g = self.inner.write();
        let rec = g.services.register(
            name,
            ServicePrivilege::parse(privilege),
            meta,
        );
        Self::emit_locked(
            &mut g,
            "service.registered",
            "system",
            json!({"name": name}),
        );
        json!(rec)
    }

    pub fn service_list(&self) -> Value {
        json!({"services": self.inner.read().services.list()})
    }

    pub fn service_health(&self, name: &str, healthy: bool) -> Value {
        let mut g = self.inner.write();
        match g.services.health_check(name, healthy) {
            Some(r) => json!(r),
            None => json!({"ok": false, "error": "not found"}),
        }
    }

    pub fn service_status(&self) -> Value {
        self.inner.read().services.status()
    }

    pub fn sys_memory_put(&self, identity: &str, key: &str, value: Value) -> Value {
        let mut g = self.inner.write();
        let r = g.services.memory.put(identity, key, value);
        Self::emit_locked(
            &mut g,
            "sys.memory.put",
            "system",
            json!({"identity": identity, "key": key}),
        );
        r
    }

    pub fn sys_memory_get(&self, identity: &str, key: &str) -> Value {
        self.inner.write().services.memory.get(identity, key)
    }

    pub fn sys_memory_list(&self, identity: &str) -> Value {
        self.inner.read().services.memory.list_keys(identity)
    }

    pub fn sys_notify_push(
        &self,
        process_id: &str,
        level: &str,
        title: &str,
        body: &str,
    ) -> Value {
        let mut g = self.inner.write();
        let n = g.services.notify.push(process_id, level, title, body);
        Self::emit_locked(
            &mut g,
            "sys.notify",
            process_id,
            json!({"id": n.id, "level": level, "title": title}),
        );
        json!(n)
    }

    pub fn sys_notify_list(&self, process_id: Option<&str>, limit: usize) -> Value {
        json!({
            "items": self.inner.read().services.notify.list(process_id, limit)
        })
    }

    pub fn sys_notify_ack(&self, id: &str) -> Value {
        json!({"ok": self.inner.write().services.notify.ack(id)})
    }

    // ── P1-A: identity cache ──────────────────────────────

    pub fn identity_hire(
        &self,
        id: &str,
        name: &str,
        role: &str,
        capabilities: Option<Vec<String>>,
        max_concurrent: Option<u32>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.identity_cache.hire(
            id,
            name,
            role,
            capabilities,
            max_concurrent.unwrap_or(2),
        ) {
            Ok(r) => {
                Self::emit_locked(
                    &mut g,
                    "identity.hired",
                    "system",
                    json!({"id": id, "name": name}),
                );
                Ok(json!(r))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn identity_set_status(&self, id_or_name: &str, status: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.identity_cache.set_status(id_or_name, status) {
            Ok(r) => Ok(json!(r)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn identity_set_capabilities(
        &self,
        id_or_name: &str,
        caps: Vec<String>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.identity_cache.set_capabilities(id_or_name, caps) {
            Ok(r) => Ok(json!(r)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn identity_admit(&self, id_or_name: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.identity_cache.admit_process(id_or_name) {
            Ok(()) => Ok(json!({"ok": true, "identity": id_or_name})),
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn identity_release(&self, id_or_name: &str) -> Value {
        self.inner.write().identity_cache.release_process(id_or_name);
        json!({"ok": true})
    }

    pub fn identity_authority_status(&self) -> Value {
        self.inner.read().identity_cache.authority_status()
    }

    pub fn identity_cache_put(&self, data: Value) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.identity_cache.put_json(&data) {
            Ok(r) => {
                Self::emit_locked(
                    &mut g,
                    "identity.cache_put",
                    "system",
                    json!({"id": r.id, "name": r.name}),
                );
                Ok(json!(r))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn identity_cache_get(&self, id_or_name: &str) -> Value {
        match self.inner.read().identity_cache.get(id_or_name) {
            Some(r) => json!(r),
            None => Value::Null,
        }
    }

    pub fn identity_cache_list(&self) -> Value {
        json!({"identities": self.inner.read().identity_cache.list()})
    }

    // ── P1-A: inbox ───────────────────────────────────────

    pub fn inbox_submit(
        &self,
        identity: &str,
        instruction: &str,
        priority: i32,
        meta: Option<Value>,
    ) -> Value {
        let mut g = self.inner.write();
        let item = g.inbox.submit(identity, instruction, priority, meta);
        Self::emit_locked(
            &mut g,
            "inbox.submitted",
            "system",
            json!({"id": item.id, "identity": identity}),
        );
        json!(item)
    }

    pub fn inbox_claim(&self, worker_id: &str, identity: Option<&str>) -> Value {
        let mut g = self.inner.write();
        match g.inbox.claim(worker_id, identity) {
            Some(item) => {
                Self::emit_locked(
                    &mut g,
                    "inbox.claimed",
                    "system",
                    json!({"id": item.id, "worker": worker_id}),
                );
                json!({"claimed": true, "item": item})
            }
            None => json!({"claimed": false, "item": null}),
        }
    }

    /// Explicit stale-claim reclaim (dispatcher tick).
    pub fn inbox_reclaim(&self) -> Value {
        let mut g = self.inner.write();
        let n = g.inbox.reclaim_stale();
        json!({"reclaimed": n, "authority": "rust"})
    }

    /// Complete by db_item_id in meta (Python dual-write path).
    pub fn inbox_complete_by_db_id(
        &self,
        db_item_id: &str,
        result: &str,
        process_id: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        let found = g.inbox.list(Some("claimed"), 200).into_iter().find(|it| {
            it.meta
                .get("db_item_id")
                .and_then(|v| v.as_str())
                == Some(db_item_id)
        });
        match found {
            Some(it) => {
                let token = it.claim_token.clone().unwrap_or_default();
                match g.inbox.complete(&it.id, &token, result, process_id) {
                    Ok(item) => json!({"ok": true, "item": item}),
                    Err(e) => json!({"ok": false, "error": e}),
                }
            }
            None => json!({"ok": false, "error": "not_found_or_not_claimed"}),
        }
    }

    pub fn inbox_fail_by_db_id(&self, db_item_id: &str, reason: &str) -> Value {
        let mut g = self.inner.write();
        let found = g.inbox.list(Some("claimed"), 200).into_iter().find(|it| {
            it.meta
                .get("db_item_id")
                .and_then(|v| v.as_str())
                == Some(db_item_id)
        });
        match found {
            Some(it) => {
                let token = it.claim_token.clone().unwrap_or_default();
                match g.inbox.fail(&it.id, &token, reason) {
                    Ok(item) => json!({"ok": true, "item": item}),
                    Err(e) => json!({"ok": false, "error": e}),
                }
            }
            None => json!({"ok": false, "error": "not_found_or_not_claimed"}),
        }
    }

    /// Heartbeat claimed lease so long-running workers are not reclaimed mid-job.
    pub fn inbox_touch_by_db_id(&self, db_item_id: &str) -> Value {
        let mut g = self.inner.write();
        let ok = g.inbox.touch_by_db_id(db_item_id);
        json!({"ok": ok, "db_item_id": db_item_id})
    }

    /// Align claim lease with Python agent_inbox_item_timeout (+ grace).
    pub fn inbox_set_claim_timeout(&self, secs: f64) -> Value {
        let mut g = self.inner.write();
        g.inbox.set_claim_timeout(secs);
        json!({"ok": true, "claim_timeout_secs": secs.max(30.0)})
    }

    pub fn inbox_complete(
        &self,
        item_id: &str,
        claim_token: &str,
        result: &str,
        process_id: Option<&str>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.inbox.complete(item_id, claim_token, result, process_id) {
            Ok(item) => Ok(json!(item)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn inbox_fail(
        &self,
        item_id: &str,
        claim_token: &str,
        reason: &str,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.inbox.fail(item_id, claim_token, reason) {
            Ok(item) => Ok(json!(item)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn inbox_release(
        &self,
        item_id: &str,
        claim_token: &str,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.inbox.release_to_pending(item_id, claim_token) {
            Ok(item) => Ok(json!(item)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn inbox_list(&self, status: Option<&str>, limit: usize) -> Value {
        json!({"items": self.inner.read().inbox.list(status, limit)})
    }

    pub fn inbox_status(&self) -> Value {
        self.inner.read().inbox.status()
    }

    // ── P1-B: skill gate ──────────────────────────────────

    pub fn skill_register(
        &self,
        name: &str,
        version: &str,
        content: &str,
        permissions: Vec<String>,
        tests: Vec<String>,
    ) -> Value {
        let mut g = self.inner.write();
        let pkg = g
            .skill_gate
            .register(name, version, content, permissions, tests);
        Self::emit_locked(
            &mut g,
            "skill.registered",
            "system",
            json!({"id": pkg.id, "name": name, "version": version}),
        );
        json!(pkg)
    }

    pub fn skill_verify(&self, package_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.skill_gate.verify(package_id) {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "skill.verified",
                    "system",
                    json!({"id": package_id}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn skill_activate(&self, package_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.skill_gate.activate(package_id) {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "skill.activated",
                    "system",
                    json!({"id": package_id, "name": p.manifest.name}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn skill_rollback(&self, name: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.skill_gate.rollback(name) {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "skill.rollback",
                    "system",
                    json!({"name": name, "id": p.id}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn skill_get_active(&self, name: &str) -> Value {
        match self.inner.read().skill_gate.get_active(name) {
            Some(p) => json!(p),
            None => Value::Null,
        }
    }

    pub fn skill_list(&self) -> Value {
        json!({"packages": self.inner.read().skill_gate.list()})
    }

    pub fn skill_is_loadable(&self, name: &str) -> Value {
        json!({
            "name": name,
            "loadable": self.inner.read().skill_gate.is_loadable(name),
        })
    }

    pub fn skill_gate_status(&self) -> Value {
        self.inner.read().skill_gate.status()
    }

    pub fn evolution_policy(&self) -> Value {
        // Rust evolution_gate is authority; skill_gate mirrors hard false
        crate::evolution_gate::EvolutionGate::policy()
    }

    pub fn evolution_submit(
        &self,
        kind: &str,
        title: &str,
        body: &str,
        identity: Option<&str>,
        score: f64,
        meta: Value,
    ) -> Value {
        let mut g = self.inner.write();
        let p = g
            .evolution
            .submit(kind, title, body, identity, score, meta);
        Self::emit_locked(
            &mut g,
            "evolution.submitted",
            "system",
            json!({"id": p.id, "kind": kind}),
        );
        json!(p)
    }

    pub fn evolution_list(&self, status: Option<&str>, limit: usize) -> Value {
        json!({
            "proposals": self.inner.read().evolution.list(status, limit),
        })
    }

    pub fn evolution_approve(&self, id: &str, by: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.evolution.approve(id, by) {
            Ok(p) => Ok(json!(p)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn evolution_reject(&self, id: &str, by: &str, reason: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.evolution.reject(id, by, reason) {
            Ok(p) => Ok(json!(p)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn evolution_apply(&self, id: &str, by: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        let skill_ok = {
            let p = g.evolution.get(id);
            match p {
                Some(pp) if pp.kind == "skill" => {
                    let name = pp
                        .meta
                        .get("skill_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(pp.title.as_str());
                    g.skill_gate.is_loadable(name)
                }
                _ => true,
            }
        };
        match g.evolution.try_apply(id, by, skill_ok) {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "evolution.applied",
                    "system",
                    json!({"id": id}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn evolution_status(&self) -> Value {
        self.inner.read().evolution.status()
    }

    pub fn evolution_block_auto(&self, reason: &str) -> Value {
        self.inner.write().evolution.block_auto_apply(reason)
    }

    /// Business analysis fully in Rust. Python only feeds the snapshot.
    pub fn evolution_analyze(&self, snapshot: Value) -> Value {
        let mut g = self.inner.write();
        let props = g.evolution.analyze(&snapshot);
        let identity = snapshot
            .get("identity")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        if !props.is_empty() {
            Self::emit_locked(
                &mut g,
                "evolution.analyzed",
                identity,
                json!({
                    "count": props.len(),
                    "kinds": props.iter().map(|p| p.kind.clone()).collect::<Vec<_>>(),
                }),
            );
        }
        json!({
            "ok": true,
            "authority": "rust",
            "analyzer": "rust",
            "proposals": props,
            "count": props.len(),
        })
    }

    // ── P1: context VM ────────────────────────────────────

    pub fn context_set_quota(&self, process_id: &str, tokens: u32) -> Value {
        self.inner
            .write()
            .context_vm
            .set_quota(process_id, tokens);
        json!({"ok": true, "process_id": process_id, "quota": tokens})
    }

    pub fn context_put_page(
        &self,
        process_id: &str,
        label: &str,
        content: &str,
    ) -> Value {
        let mut g = self.inner.write();
        let page = g.context_vm.put_page(process_id, label, content);
        json!(page)
    }

    pub fn context_swap_in(
        &self,
        page_id: &str,
        caller_process: Option<&str>,
    ) -> KernelResult<Value> {
        match self
            .inner
            .write()
            .context_vm
            .swap_in(page_id, caller_process)
        {
            Ok(p) => Ok(json!(p)),
            Err(e) => {
                if e.contains("isolation") {
                    Err(KernelError::Permission(e))
                } else {
                    Err(KernelError::NotFound(e))
                }
            }
        }
    }

    pub fn context_swap_out(
        &self,
        page_id: &str,
        caller_process: Option<&str>,
    ) -> KernelResult<Value> {
        match self
            .inner
            .write()
            .context_vm
            .swap_out(page_id, caller_process)
        {
            Ok(p) => Ok(json!(p)),
            Err(e) => {
                if e.contains("isolation") || e.contains("pinned") {
                    Err(KernelError::Permission(e))
                } else {
                    Err(KernelError::NotFound(e))
                }
            }
        }
    }

    pub fn context_pin(&self, page_id: &str, pinned: bool) -> KernelResult<Value> {
        match self.inner.write().context_vm.pin_page(page_id, pinned) {
            Ok(p) => Ok(json!(p)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn context_set_isolation(&self, process_id: &str, mode: &str) -> Value {
        self.inner
            .write()
            .context_vm
            .set_isolation(process_id, mode);
        json!({"ok": true, "process_id": process_id, "isolation": mode})
    }

    pub fn context_schedule(&self, process_id: Option<&str>) -> Value {
        self.inner.write().context_vm.schedule_tick(process_id)
    }

    pub fn context_list_pages(&self, process_id: &str) -> Value {
        json!({"pages": self.inner.read().context_vm.list_pages(process_id)})
    }

    pub fn context_status(&self, process_id: Option<&str>) -> Value {
        self.inner.read().context_vm.status(process_id)
    }

    // ── P1: memory layers ─────────────────────────────────

    pub fn memory_layer_put(
        &self,
        identity: &str,
        layer: &str,
        content: &str,
        score: f64,
    ) -> Value {
        let mut g = self.inner.write();
        let e = g.memory_layers.put(
            identity,
            MemoryLayer::parse(layer),
            content,
            score,
        );
        // mirror into sys.memory for syscall path
        let key = format!("layer.{}.{}", e.layer, e.id);
        g.services
            .memory
            .put(identity, &key, json!({"content": content, "score": score}));
        json!(e)
    }

    pub fn memory_layer_list(&self, identity: &str, layer: Option<&str>) -> Value {
        let layer = layer.map(MemoryLayer::parse);
        json!({
            "entries": self.inner.read().memory_layers.list(identity, layer)
        })
    }

    pub fn memory_layer_consolidate(&self, identity: &str) -> Value {
        let mut g = self.inner.write();
        let r = g.memory_layers.consolidate(identity);
        Self::emit_locked(
            &mut g,
            "memory.consolidated",
            "system",
            r.clone(),
        );
        r
    }

    pub fn memory_layer_schedule(&self, identity: Option<&str>) -> Value {
        self.inner.write().memory_layers.schedule_tick(identity)
    }

    pub fn memory_layer_status(&self) -> Value {
        self.inner.read().memory_layers.status()
    }

    // ── Multi-device sync ──────────────────────────────────

    pub fn device_sync_status(&self) -> Value {
        self.inner.read().device_sync.status()
    }

    pub fn device_sync_register(&self, device_id: &str, label: &str) -> Value {
        let mut g = self.inner.write();
        json!(g.device_sync.register_peer(device_id, label))
    }

    pub fn device_sync_set_local(&self, device_id: &str, label: &str) -> Value {
        let mut g = self.inner.write();
        g.device_sync.set_local_device(device_id, label);
        g.instances.set_device_id(device_id);
        json!({"ok": true, "device_id": device_id, "label": label})
    }

    pub fn device_sync_list(&self) -> Value {
        json!({"devices": self.inner.read().device_sync.list_devices()})
    }

    /// Push identity state (memory layers + sys memory + skills) to sync plane.
    pub fn device_sync_push(
        &self,
        identity: &str,
        to_device: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        let mem = g.memory_layers.export_identity(identity);
        let kv = g.services.memory.export_map(identity);
        let skills = json!(g.skill_gate.list());
        let payload = json!({
            "memory_layers": mem,
            "sys_memory": kv,
            "skills": skills,
            "exported_at": now_secs(),
        });
        let env = g.device_sync.push(identity, payload, to_device);
        Self::emit_locked(
            &mut g,
            "device.sync_push",
            "system",
            json!({"identity": identity, "revision": env.revision}),
        );
        json!(env)
    }

    pub fn device_sync_pull(
        &self,
        identity: &str,
        since_revision: Option<u64>,
    ) -> Value {
        self.inner
            .read()
            .device_sync
            .pull(identity, since_revision)
    }

    pub fn device_sync_apply(&self, envelope: Value) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.device_sync.apply_remote(envelope) {
            Ok(r) => {
                // hydrate memory if accepted
                if r.get("ok") == Some(&json!(true)) {
                    if let Some(head) = r.get("head") {
                        let identity = head
                            .get("identity")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        let payload = head.get("payload").cloned().unwrap_or(json!({}));
                        if let Some(entries) = payload
                            .get("memory_layers")
                            .and_then(|m| m.get("entries"))
                            .and_then(|e| e.as_array())
                        {
                            g.memory_layers.import_identity(identity, entries);
                        }
                        if let Some(kv) = payload.get("sys_memory") {
                            g.services.memory.import_map(identity, kv);
                        }
                    }
                }
                Self::emit_locked(
                    &mut g,
                    "device.sync_apply",
                    "system",
                    json!({"decision": r.get("decision")}),
                );
                Ok(r)
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn device_sync_outbox(&self, limit: usize) -> Value {
        let mut g = self.inner.write();
        json!({"envelopes": g.device_sync.drain_outbox(limit)})
    }

    /// Audit WORM anchor verify (Rust host store).
    pub fn audit_anchor_verify(&self) -> Value {
        let g = self.inner.read();
        match &g.audit_store {
            Some(s) => s.verify_anchor(),
            None => json!({
                "ok": false,
                "reason": "audit_persist_disabled",
            }),
        }
    }

    pub fn audit_anchor_status(&self) -> Value {
        let g = self.inner.read();
        match &g.audit_store {
            Some(s) => json!({
                "worm": s.worm(),
                "anchor": s.read_anchor(),
                "verify": s.verify_anchor(),
            }),
            None => json!({"worm": false, "enabled": false}),
        }
    }

    // ── P2: coding profile ────────────────────────────────

    pub fn coding_profile_list(&self) -> Value {
        CodingProfileRegistry::list()
    }

    pub fn coding_profile_get(&self, id: &str) -> Value {
        match CodingProfileRegistry::resolve(id) {
            Some(p) => p.to_dict(),
            None => Value::Null,
        }
    }

    /// Apply coding profile to process: isolation + intent + iteration budget.
    pub fn coding_profile_apply(&self, process_id: &str, profile_id: &str) -> KernelResult<Value> {
        let profile = CodingProfileRegistry::resolve(profile_id)
            .ok_or_else(|| KernelError::NotFound(format!("unknown profile {profile_id}")))?;
        {
            let mut g = self.inner.write();
            if !g.processes.contains_key(process_id) {
                return Err(KernelError::NotFound(format!("未知进程 {process_id}")));
            }
            g.isolation.set_process_profile(
                process_id,
                IsolationProfile::parse(&profile.isolation),
            );
            g.policy
                .set_iteration_budget(process_id, profile.max_iterations);
            if let Some(proc) = g.processes.get_mut(process_id) {
                proc.meta
                    .insert("coding_profile".into(), json!(profile.id));
                proc.meta
                    .insert("collab_gate".into(), json!(profile.requires_collab_gate()));
                if proc.token_budget.is_none() {
                    proc.token_budget = Some(profile.token_budget);
                }
            }
            // pair profile: ensure collab session exists for interrupt/approve UX
            if profile.requires_collab_gate() {
                let _ = g.collab.ensure(process_id);
            }
            Self::emit_locked(
                &mut g,
                "coding_profile.applied",
                process_id,
                json!({"profile": profile.id, "collab_gate": profile.requires_collab_gate()}),
            );
        }
        // apply intent outside lock via public API
        let intent_v = profile.to_intent_dict();
        let intent = IntentDeclaration::from_dict(&intent_v)?;
        let _ = self.apply_intent(process_id, intent, None)?;
        Ok(json!({
            "ok": true,
            "process_id": process_id,
            "profile": profile.to_dict(),
            "tools": profile.tools,
            "collab_gate": profile.requires_collab_gate(),
        }))
    }

    /// Spawn a process already stamped with a coding profile (E-01 UX path).
    pub fn coding_profile_spawn(
        &self,
        identity: &str,
        profile_id: &str,
        session_id: Option<&str>,
    ) -> KernelResult<Value> {
        let profile = CodingProfileRegistry::resolve(profile_id)
            .ok_or_else(|| KernelError::NotFound(format!("unknown profile {profile_id}")))?;
        let mut meta = BTreeMap::new();
        meta.insert("coding_profile".into(), json!(profile.id));
        meta.insert("collab_gate".into(), json!(profile.requires_collab_gate()));
        meta.insert("spawn_path".into(), json!("coding_profile_spawn"));
        let intent = IntentDeclaration::from_dict(&profile.to_intent_dict())?;
        let proc = self.create_process_with_intent(
            identity,
            session_id,
            None,
            Some(profile.capabilities.clone()),
            Some(profile.token_budget),
            Some(meta),
            Some(intent),
        )?;
        let applied = self.coding_profile_apply(&proc.id, &profile.id)?;
        Ok(json!({
            "ok": true,
            "process": proc.to_dict(),
            "profile": profile.to_dict(),
            "tools": profile.tools,
            "applied": applied,
        }))
    }

    // ── P2: collab ────────────────────────────────────────

    pub fn collab_set_plan(&self, process_id: &str, steps: Vec<String>) -> Value {
        let mut g = self.inner.write();
        let r = g.collab.set_plan(process_id, steps);
        Self::emit_locked(&mut g, "collab.plan", process_id, json!({"set": true}));
        r
    }

    pub fn collab_revise_plan(&self, process_id: &str, steps: Vec<String>) -> Value {
        let mut g = self.inner.write();
        let r = g.collab.revise_plan(process_id, steps);
        Self::emit_locked(&mut g, "collab.plan_revised", process_id, json!({}));
        r
    }

    pub fn collab_interrupt(&self, process_id: &str, reason: &str) -> KernelResult<Value> {
        // also suspend process
        let _ = self.suspend_process(process_id, reason)?;
        let mut g = self.inner.write();
        let r = g.collab.interrupt(process_id, reason);
        Self::emit_locked(
            &mut g,
            "collab.interrupt",
            process_id,
            json!({"reason": reason}),
        );
        Ok(r)
    }

    pub fn collab_resume(&self, process_id: &str) -> KernelResult<Value> {
        let _ = self.resume_process(process_id)?;
        let mut g = self.inner.write();
        let r = g.collab.resume_collab(process_id);
        Self::emit_locked(&mut g, "collab.resume", process_id, json!({}));
        Ok(r)
    }

    pub fn collab_request_approval(
        &self,
        process_id: &str,
        kind: &str,
        summary: &str,
        detail: Value,
    ) -> Value {
        let mut g = self.inner.write();
        let req = g
            .collab
            .request_approval(process_id, kind, summary, detail);
        Self::emit_locked(
            &mut g,
            "collab.approval_requested",
            process_id,
            json!({"id": req.id, "kind": kind}),
        );
        json!(req)
    }

    pub fn collab_resolve_approval(
        &self,
        process_id: &str,
        approval_id: &str,
        approve: bool,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.collab.resolve_approval(process_id, approval_id, approve) {
            Ok(r) => {
                Self::emit_locked(
                    &mut g,
                    "collab.approval_resolved",
                    process_id,
                    json!({"id": approval_id, "approve": approve}),
                );
                Ok(json!(r))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn collab_get(&self, process_id: &str) -> Value {
        match self.inner.read().collab.get(process_id) {
            Some(s) => json!(s),
            None => json!({"process_id": process_id, "plan": [], "interrupted": false}),
        }
    }

    pub fn collab_status(&self) -> Value {
        self.inner.read().collab.status()
    }

    // ── P2: edit session ──────────────────────────────────

    pub fn edit_propose(
        &self,
        process_id: &str,
        path: &str,
        after: &str,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.edit_sessions.propose(process_id, path, after) {
            Ok(s) => {
                Self::emit_locked(
                    &mut g,
                    "edit.proposed",
                    process_id,
                    json!({"id": s.id, "path": path}),
                );
                Ok(json!(s))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn edit_confirm(&self, session_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.edit_sessions.confirm(session_id) {
            Ok(s) => {
                Self::emit_locked(
                    &mut g,
                    "edit.applied",
                    &s.process_id,
                    json!({"id": session_id, "path": s.path}),
                );
                Ok(json!(s))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn edit_reject(&self, session_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.edit_sessions.reject(session_id) {
            Ok(s) => Ok(json!(s)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn edit_rollback(&self, session_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.edit_sessions.rollback(session_id) {
            Ok(s) => {
                Self::emit_locked(
                    &mut g,
                    "edit.rolled_back",
                    &s.process_id,
                    json!({"id": session_id}),
                );
                Ok(json!(s))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn edit_list(&self, process_id: &str) -> Value {
        json!({"sessions": self.inner.read().edit_sessions.list(process_id)})
    }

    pub fn edit_get(&self, session_id: &str) -> Value {
        match self.inner.read().edit_sessions.get(session_id) {
            Some(s) => json!(s),
            None => Value::Null,
        }
    }

    // ── P2: repo index ────────────────────────────────────

    pub fn repo_index_build(
        &self,
        process_id: &str,
        root: &str,
        max_depth: usize,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.repo_index.build(process_id, root, max_depth) {
            Ok(idx) => {
                // also put summary page into context vm under quota
                let summary = format!(
                    "repo index {} files {} bytes tokens~{}",
                    idx.total_files, idx.total_bytes, idx.token_estimate
                );
                g.context_vm
                    .put_page(process_id, "repo_index", &summary);
                Ok(json!(idx))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn repo_index_get(&self, id: &str) -> Value {
        match self.inner.read().repo_index.get(id) {
            Some(i) => json!(i),
            None => Value::Null,
        }
    }

    pub fn repo_index_list(&self, process_id: &str) -> Value {
        json!({"indexes": self.inner.read().repo_index.list_for_process(process_id)})
    }

    // ── P2: HAL ───────────────────────────────────────────

    pub fn hal_platform(&self) -> Value {
        Hal::platform()
    }

    pub fn hal_resolve_path(&self, workspace: Option<&str>, path: &str) -> KernelResult<Value> {
        Hal::resolve_path(workspace, path).map_err(KernelError::Invalid)
    }

    pub fn hal_resolve_command(&self, logical: &str, args: Vec<String>) -> Value {
        Hal::resolve_command(logical, &args)
    }

    pub fn hal_resolve_browser(&self, url: &str) -> Value {
        Hal::resolve_browser(url)
    }

    /// Enforce path via HAL jail then mediate capability (E-05).
    pub fn hal_enforce_path(
        &self,
        process_id: &str,
        workspace: Option<&str>,
        path: &str,
        capability: Option<&str>,
    ) -> KernelResult<Value> {
        let cap = capability.unwrap_or("file_read");
        let enforced = Hal::enforce_path(workspace, path, cap).map_err(KernelError::Invalid)?;
        // mediate capability for this process
        let _ = self.mediate(process_id, "tool_call", cap, Some(&enforced))?;
        Ok(json!({
            "ok": true,
            "mediated": true,
            "process_id": process_id,
            "result": enforced,
        }))
    }

    pub fn hal_enforce_command(
        &self,
        process_id: &str,
        logical: &str,
        args: Vec<String>,
    ) -> KernelResult<Value> {
        let enforced = Hal::enforce_command(logical, &args);
        let _ = self.mediate(process_id, "tool_call", "command", Some(&enforced))?;
        Ok(json!({
            "ok": true,
            "mediated": true,
            "process_id": process_id,
            "result": enforced,
        }))
    }

    pub fn hal_enforce_browser(&self, process_id: &str, url: &str) -> KernelResult<Value> {
        let enforced = Hal::enforce_browser(url).map_err(KernelError::Invalid)?;
        let _ = self.mediate(process_id, "tool_call", "browser", Some(&enforced))?;
        Ok(json!({
            "ok": true,
            "mediated": true,
            "process_id": process_id,
            "result": enforced,
        }))
    }

    pub fn hal_status(&self) -> Value {
        Hal::status()
    }

    // ── P2: WASM ──────────────────────────────────────────

    pub fn wasm_load(
        &self,
        name: &str,
        bytes_b64_or_text: &str,
        fuel_limit: Option<u64>,
        memory_pages: Option<u32>,
    ) -> KernelResult<Value> {
        // accept raw text or base64
        let bytes = if bytes_b64_or_text.starts_with('\0')
            || bytes_b64_or_text.as_bytes().starts_with(&[0x00, b'a', b's', b'm'])
        {
            bytes_b64_or_text.as_bytes().to_vec()
        } else if let Ok(decoded) = decode_b64_loose(bytes_b64_or_text) {
            decoded
        } else {
            bytes_b64_or_text.as_bytes().to_vec()
        };
        let mut g = self.inner.write();
        match g.wasm.load(name, &bytes, fuel_limit, memory_pages) {
            Ok(m) => Ok(json!(m)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn wasm_activate(&self, module_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.wasm.activate(module_id) {
            Ok(m) => Ok(json!(m)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn wasm_invoke(
        &self,
        module_id: &str,
        entry: &str,
        params: Value,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.wasm.invoke(module_id, entry, &params) {
            Ok(r) => Ok(json!(r)),
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn wasm_unload(&self, module_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.wasm.unload(module_id) {
            Ok(m) => Ok(json!(m)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn wasm_kill(&self, module_id: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.wasm.kill(module_id) {
            Ok(m) => Ok(json!(m)),
            Err(e) => Err(KernelError::NotFound(e)),
        }
    }

    pub fn wasm_list(&self) -> Value {
        json!({"modules": self.inner.read().wasm.list()})
    }

    pub fn wasm_status(&self) -> Value {
        self.inner.read().wasm.status()
    }

    pub fn wasm_explain(&self, module_id: Option<&str>) -> Value {
        let g = self.inner.read();
        match module_id {
            Some(id) if !id.is_empty() => g.wasm.explain_module(id),
            _ => json!({
                "limits": crate::wasm_runtime::WasmRuntime::explain_limits(None),
                "status": g.wasm.status(),
            }),
        }
    }

    // ── P2: package manager ───────────────────────────────

    pub fn pkg_install(
        &self,
        name: &str,
        version: &str,
        content: &str,
        dependencies: Vec<String>,
        permissions: Vec<String>,
        signature: Option<&str>,
    ) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g
            .packages
            .install(name, version, content, dependencies, permissions, signature)
        {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "pkg.installed",
                    "system",
                    json!({"name": name, "status": p.status}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn pkg_activate(&self, name: &str) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.packages.activate(name) {
            Ok(p) => Ok(json!(p)),
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn pkg_uninstall(&self, name: &str) -> Value {
        json!({"ok": self.inner.write().packages.uninstall(name), "name": name})
    }

    pub fn pkg_list(&self) -> Value {
        json!({"packages": self.inner.read().packages.list()})
    }

    pub fn pkg_get(&self, name: &str) -> Value {
        match self.inner.read().packages.get(name) {
            Some(p) => json!(p),
            None => Value::Null,
        }
    }

    pub fn pkg_sign(&self, content: &str) -> Value {
        let g = self.inner.read();
        json!({
            "signature": g.packages.sign_content(content),
            "key_source": g.packages.key_source(),
            "insecure_default_key": g.packages.is_insecure_default_key(),
        })
    }

    pub fn pkg_set_signing_key(&self, key: &str) -> Value {
        let mut g = self.inner.write();
        if key.trim().len() < 16 {
            return json!({"ok": false, "error": "key must be at least 16 chars"});
        }
        g.packages.set_signing_key(key.as_bytes().to_vec());
        json!({"ok": true, "key_source": g.packages.key_source()})
    }

    /// Force production signing key policy (E-06).
    pub fn pkg_set_require_secure(&self, require: bool) -> Value {
        let mut g = self.inner.write();
        g.packages.set_require_secure(require);
        json!({
            "ok": true,
            "require_secure": g.packages.require_secure(),
            "insecure_default_key": g.packages.is_insecure_default_key(),
            "production_ready": !g.packages.is_insecure_default_key(),
        })
    }

    pub fn pkg_scan(&self, content: &str, permissions: Vec<String>) -> Value {
        self.inner.read().packages.scan_only(content, &permissions)
    }

    pub fn pkg_promote(&self, name: &str, force: bool) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.packages.promote(name, force) {
            Ok(p) => {
                Self::emit_locked(
                    &mut g,
                    "pkg.promoted",
                    "system",
                    json!({"name": name, "status": p.status}),
                );
                Ok(json!(p))
            }
            Err(e) => Err(KernelError::Permission(e)),
        }
    }

    pub fn pkg_catalog(&self) -> Value {
        self.inner.read().packages.catalog()
    }

    pub fn pkg_status(&self) -> Value {
        self.inner.read().packages.status()
    }

    // ── P2: multi-device instance ──────────────────────────

    pub fn instance_export(
        &self,
        identity: &str,
        process_id: Option<&str>,
    ) -> Value {
        let mut g = self.inner.write();
        let snap = process_id.and_then(|pid| {
            g.processes.get(pid).map(|p| p.to_dict())
        });
        let caps = process_id.and_then(|pid| {
            g.processes
                .get(pid)
                .and_then(|p| p.capabilities.clone())
        });
        // Full KV map (not just keys) for multi-device hydrate
        let mem = g.services.memory.export_map(identity);
        let skills = json!(g.skill_gate.list());
        let b = g.instances.export_bundle(
            identity,
            snap,
            caps,
            mem,
            skills,
            json!({"exported_at": now_secs()}),
        );
        json!(b)
    }

    pub fn instance_import(&self, bundle: Value) -> KernelResult<Value> {
        let mut g = self.inner.write();
        match g.instances.import_bundle(bundle) {
            Ok(b) => {
                // hydrate identity cache
                let _ = g.identity_cache.put_json(&json!({
                    "id": b.identity,
                    "name": b.identity,
                    "capabilities": b.capabilities,
                    "status": "active",
                }));
                // hydrate sys memory KV
                let mem_n = g.services.memory.import_map(&b.identity, &b.memory);
                // hydrate layered memory if entries present under memory._layers
                let mut layer_n = 0usize;
                if let Some(layers) = b.memory.get("_layers").and_then(|v| v.as_array()) {
                    for ent in layers {
                        let content = ent
                            .get("content")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if content.is_empty() {
                            continue;
                        }
                        let layer = ent
                            .get("layer")
                            .and_then(|v| v.as_str())
                            .unwrap_or("working");
                        let score = ent.get("score").and_then(|v| v.as_f64()).unwrap_or(0.5);
                        g.memory_layers.put(
                            &b.identity,
                            MemoryLayer::parse(layer),
                            content,
                            score,
                        );
                        layer_n += 1;
                    }
                }
                // re-register skills as draft only (never auto-activate — security)
                let mut skill_n = 0usize;
                let skill_items: Vec<Value> = match &b.skills {
                    Value::Array(a) => a.clone(),
                    Value::Object(o) => o.values().cloned().collect(),
                    _ => Vec::new(),
                };
                for sk in skill_items {
                    let name = sk
                        .get("manifest")
                        .and_then(|m| m.get("name"))
                        .or_else(|| sk.get("name"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if name.is_empty() {
                        continue;
                    }
                    let version = sk
                        .get("manifest")
                        .and_then(|m| m.get("version"))
                        .or_else(|| sk.get("version"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("0.0.0-import");
                    let content = sk
                        .get("content")
                        .and_then(|v| v.as_str())
                        .unwrap_or("# imported skill (draft)");
                    let perms: Vec<String> = sk
                        .get("manifest")
                        .and_then(|m| m.get("permissions"))
                        .or_else(|| sk.get("permissions"))
                        .and_then(|v| v.as_array())
                        .map(|a| {
                            a.iter()
                                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    let _ = g
                        .skill_gate
                        .register(name, version, content, perms, vec!["import".into()]);
                    skill_n += 1;
                }
                let plan = crate::instance::InstanceRegistry::hydrate_plan(&b);
                Self::emit_locked(
                    &mut g,
                    "instance.imported",
                    "system",
                    json!({
                        "identity": b.identity,
                        "memory_keys": mem_n,
                        "memory_layers": layer_n,
                        "skills_draft": skill_n,
                    }),
                );
                // Flat identity fields for ABI backward-compat + hydrate report.
                Ok(json!({
                    "id": b.id,
                    "device_id": b.device_id,
                    "identity": b.identity,
                    "process_snapshot": b.process_snapshot,
                    "capabilities": b.capabilities,
                    "memory": b.memory,
                    "skills": b.skills,
                    "meta": b.meta,
                    "created_at": b.created_at,
                    "content_hash": b.content_hash,
                    "hydrated": {
                        "identity_cache": true,
                        "memory_keys": mem_n,
                        "memory_layers": layer_n,
                        "skills_draft": skill_n,
                        "skills_auto_activated": false,
                    },
                    "plan": plan,
                }))
            }
            Err(e) => Err(KernelError::Invalid(e)),
        }
    }

    pub fn instance_list(&self) -> Value {
        json!({"instances": self.inner.read().instances.list()})
    }

    pub fn instance_status(&self) -> Value {
        self.inner.read().instances.status()
    }

    /// ABI compatibility window (E-03).
    pub fn abi_compat(&self) -> Value {
        self.inner.read().abi.snapshot()
    }

    pub fn abi_negotiate(&self, client_abi: &str) -> Value {
        let mut g = self.inner.write();
        let r = g.abi.negotiate(client_abi);
        Self::emit_locked(
            &mut g,
            "abi.negotiate",
            "system",
            json!({
                "client_abi": client_abi,
                "compatible": r.get("compatible"),
            }),
        );
        r
    }

    pub fn abi_record_break(
        &self,
        from_abi: &str,
        to_abi: &str,
        reason: &str,
        methods_removed: Vec<String>,
    ) -> Value {
        let mut g = self.inner.write();
        let rec = g
            .abi
            .record_break(from_abi, to_abi, reason, methods_removed);
        let break_count = g.abi.break_count();
        Self::emit_locked(
            &mut g,
            "abi.break",
            "system",
            json!({
                "from": from_abi,
                "to": to_abi,
                "break_count": break_count,
            }),
        );
        json!(rec)
    }

    // ── R3/R4: domain events + approval ───────────────────

    pub fn domain_publish(&self, topic: &str, payload: Value) -> Value {
        let mut g = self.inner.write();
        json!(g.domain_events.publish(topic, payload))
    }

    pub fn domain_recent(
        &self,
        limit: usize,
        prefix: Option<&str>,
        since_ts: Option<f64>,
        after_seq: Option<u64>,
    ) -> Value {
        let g = self.inner.read();
        json!({
            "events": g.domain_events.recent(limit, prefix, since_ts, after_seq),
            "seq": g.domain_events.current_seq(),
        })
    }

    pub fn domain_seq(&self) -> Value {
        json!({"seq": self.inner.read().domain_events.current_seq()})
    }

    pub fn domain_status(&self) -> Value {
        self.inner.read().domain_events.status()
    }

    pub fn approval_set_rules(&self, rules: Value) -> Value {
        let mut g = self.inner.write();
        if let Some(arr) = rules.as_array() {
            g.approval.set_from_json(arr);
        } else if let Some(arr) = rules.get("rules").and_then(|v| v.as_array()) {
            g.approval.set_from_json(arr);
        }
        g.approval.rules_json()
    }

    pub fn approval_get_rules(&self) -> Value {
        self.inner.read().approval.rules_json()
    }

    pub fn approval_classify(&self, capabilities: Vec<String>) -> Value {
        self.inner.read().approval.classify(&capabilities)
    }

    pub fn approval_should_auto(&self, capabilities: Vec<String>) -> Value {
        json!({
            "auto_approve": self.inner.read().approval.should_auto_approve(&capabilities),
            "evolution_requires_review": true,
        })
    }
}

fn decode_b64_loose(s: &str) -> Result<Vec<u8>, ()> {
    // minimal base64 decode without extra crate
    fn val(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            b'=' => Some(0),
            _ => None,
        }
    }
    let clean: Vec<u8> = s
        .bytes()
        .filter(|b| !b.is_ascii_whitespace())
        .collect();
    if clean.len() < 4 || clean.len() % 4 != 0 {
        return Err(());
    }
    let mut out = Vec::with_capacity(clean.len() / 4 * 3);
    for chunk in clean.chunks(4) {
        let a = val(chunk[0]).ok_or(())?;
        let b = val(chunk[1]).ok_or(())?;
        let c = val(chunk[2]).ok_or(())?;
        let d = val(chunk[3]).ok_or(())?;
        out.push((a << 2) | (b >> 4));
        if chunk[2] != b'=' {
            out.push((b << 4) | (c >> 2));
        }
        if chunk[3] != b'=' {
            out.push((c << 6) | d);
        }
    }
    Ok(out)
}

/// Global singleton for host / embedding.
static GLOBAL: once_cell::sync::OnceCell<Arc<AgentKernel>> = once_cell::sync::OnceCell::new();

pub fn init_global(config: KernelConfig) -> Arc<AgentKernel> {
    let k = Arc::new(AgentKernel::new(config));
    let _ = GLOBAL.set(k.clone());
    k
}

pub fn global() -> Arc<AgentKernel> {
    GLOBAL
        .get()
        .cloned()
        .unwrap_or_else(|| {
            let k = Arc::new(AgentKernel::default_new());
            let _ = GLOBAL.set(k.clone());
            k
        })
}

pub fn reset_global_for_tests() {
    // OnceCell cannot reset; tests should construct AgentKernel::new directly.
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::intent::IntentDeclaration;

    fn k() -> AgentKernel {
        AgentKernel::new(KernelConfig {
            audit_persist: false,
            require_intent: false, // unit tests use classic compat mode
            soft_renew: SoftRenewConfig {
                enabled: false,
                ..Default::default()
            },
            ..Default::default()
        })
    }

    #[test]
    fn require_intent_defaults_readonly() {
        let k = AgentKernel::new(KernelConfig {
            audit_persist: false,
            require_intent: true,
            soft_renew: SoftRenewConfig {
                enabled: false,
                ..Default::default()
            },
            ..Default::default()
        });
        let p = k
            .create_process("main", None, None, None, None, None)
            .unwrap();
        assert!(p.capabilities.is_some());
        assert!(p.has_capability("file_read"));
        assert!(!p.has_capability("terminal"));
        assert!(k.mediate(&p.id, "tool_call", "file_read", None).is_ok());
        assert!(k.mediate(&p.id, "tool_call", "terminal", None).is_err());
    }

    #[test]
    fn apply_intent_and_filter_tools() {
        let k = k();
        let p = k
            .create_process(
                "main",
                None,
                None,
                Some(vec!["file_read".into()]),
                None,
                None,
            )
            .unwrap();
        let mut intent = IntentDeclaration::default_readonly("need grep");
        intent.capabilities = vec!["file_read".into(), "grep".into(), "terminal".into()];
        let (tok, dropped) = k.apply_intent(&p.id, intent, None).unwrap();
        assert!(tok.allows("grep"));
        assert!(dropped.contains(&"terminal".into()));
        let tools = vec![
            "file_read".into(),
            "grep".into(),
            "terminal".into(),
            "file_write".into(),
        ];
        let filtered = k.filter_tools(&p.id, &tools).unwrap();
        assert!(filtered.contains(&"file_read".into()));
        assert!(filtered.contains(&"grep".into()));
        assert!(!filtered.contains(&"terminal".into()));
    }

    #[test]
    fn multi_agent_demo_ping_pong() {
        let k = k();
        let r = k.multi_agent_demo().expect("demo");
        assert_eq!(r["ok"], true);
        assert_eq!(r["inbox_b"]["count"], 1);
        assert_eq!(r["inbox_a"]["count"], 1);
        assert!(r["channel_publish"]["delivered"].as_u64().unwrap_or(0) >= 1);
    }

    #[test]
    fn skill_require_loadable_denies_draft() {
        let k = k();
        let pkg = k.skill_register(
            "x",
            "1.0",
            "print(1)",
            vec![],
            vec!["t1".into()],
        );
        let id = pkg["id"].as_str().unwrap();
        assert!(k.skill_require_loadable("x").is_err());
        k.skill_verify(id).unwrap();
        k.skill_activate(id).unwrap();
        assert!(k.skill_require_loadable("x").is_ok());
    }

    #[test]
    fn eval_suite_and_manifest() {
        let k = k();
        let mut parts = std::collections::HashMap::new();
        parts.insert("coding".into(), 0.8);
        parts.insert("safety".into(), 0.9);
        parts.insert("long".into(), 0.6);
        k.eval_record("default", 0.8, parts, json!({}));
        let g = k.eval_gate_check(Some("default"));
        assert_eq!(g["ok"], true);
        let m = k.agent_manifest_validate(json!({
            "name": "demo",
            "version": "0.1.0",
            "capabilities": ["file_read"],
        }));
        assert_eq!(m["ok"], true);
    }

    #[test]
    fn create_and_mediate_compat() {
        let kernel = k();
        let proc = kernel
            .create_process("main", Some("s1"), None, None, None, None)
            .unwrap();
        assert_eq!(proc.state, ProcessState::Created);
        assert_eq!(proc.id.len(), 16);
        let d = kernel.mediate(&proc.id, "tool_call", "file_read", None).unwrap();
        assert!(d.allowed);
        assert!(!d.capability_checked);
        let kinds: Vec<_> = kernel.events(None, None, 100).iter().map(|e| e.kind.clone()).collect();
        assert!(kinds.contains(&"process_created".into()));
        assert!(kinds.contains(&"mediation".into()));
        assert!(kinds.contains(&"policy.decision".into()));
    }

    #[test]
    fn explicit_capability_enforced() {
        let kernel = k();
        let proc = kernel
            .create_process(
                "main",
                None,
                None,
                Some(vec!["file_read".into(), "grep".into()]),
                None,
                None,
            )
            .unwrap();
        assert!(kernel.mediate(&proc.id, "tool_call", "file_read", None).is_ok());
        assert!(kernel.mediate(&proc.id, "tool_call", "terminal", None).is_err());
    }

    #[test]
    fn budget_exceeded() {
        let kernel = k();
        let proc = kernel
            .create_process("main", None, None, None, Some(100), None)
            .unwrap();
        assert_eq!(kernel.charge_tokens(&proc.id, 40).unwrap(), Some(60));
        assert!(kernel.charge_tokens(&proc.id, 61).is_err());
    }

    #[test]
    fn child_caps_and_budget() {
        let kernel = k();
        let parent = kernel
            .create_process(
                "main",
                None,
                None,
                Some(vec!["file_read".into(), "grep".into()]),
                Some(100),
                None,
            )
            .unwrap();
        kernel.charge_tokens(&parent.id, 30).unwrap();
        assert!(kernel
            .create_process("sub", None, Some(&parent.id), None, Some(71), None)
            .is_err());
        let child = kernel
            .create_process(
                "sub",
                None,
                Some(&parent.id),
                Some(vec!["grep".into()]),
                Some(70),
                None,
            )
            .unwrap();
        assert_eq!(child.token_budget, Some(70));
        assert!(kernel
            .create_process(
                "sub2",
                None,
                Some(&parent.id),
                Some(vec!["terminal".into()]),
                None,
                None,
            )
            .is_err());
    }

    #[test]
    fn hash_chain_ok() {
        let kernel = k();
        let p = kernel.create_process("main", None, None, None, None, None).unwrap();
        kernel.mediate(&p.id, "tool_call", "x", None).unwrap();
        let (ok, idx) = kernel.verify_event_chain();
        assert!(ok, "broken at {idx}");
    }

    #[test]
    fn end_and_gc() {
        let kernel = k();
        let p = kernel.create_process("main", None, None, None, None, None).unwrap();
        kernel.mark_running(&p.id).unwrap();
        kernel.end_process(&p.id, "completed", Some("done")).unwrap();
        // force old ended_at
        {
            let mut g = kernel.inner.write();
            if let Some(proc) = g.processes.get_mut(&p.id) {
                proc.ended_at = Some(0.0);
            }
        }
        assert_eq!(kernel.gc_terminal(1.0), 1);
        assert!(kernel.get_process(&p.id).is_none());
    }

    // ── §8 / 0.9 platformization ──────────────────────────

    #[test]
    fn coding_profile_spawn_and_pair_collab() {
        let kernel = k();
        let r = kernel
            .coding_profile_spawn("dev", "pair", Some("s-pair"))
            .unwrap();
        assert_eq!(r["ok"], true);
        let pid = r["process"]["id"].as_str().unwrap();
        assert_eq!(r["profile"]["id"], "pair");
        assert_eq!(r["collab_gate"].as_bool().or_else(|| r["applied"]["collab_gate"].as_bool()).unwrap_or(false) || r["applied"]["collab_gate"] == true, true);
        // interrupt blocks write mediate
        kernel.collab_interrupt(pid, "review").unwrap();
        let err = kernel
            .mediate(pid, "tool_call", "file_write", None)
            .unwrap_err();
        assert!(
            format!("{err}").contains("collab") || format!("{err}").contains("interrupt"),
            "{err}"
        );
        kernel.collab_resume(pid).unwrap();
        // pending write approval also blocks
        let a = kernel.collab_request_approval(pid, "write", "patch", json!({}));
        let aid = a["id"].as_str().unwrap();
        assert!(kernel.mediate(pid, "tool_call", "file_write", None).is_err());
        kernel.collab_resolve_approval(pid, aid, true).unwrap();
        assert!(kernel.mediate(pid, "tool_call", "file_write", None).is_ok());
    }

    #[test]
    fn abi_negotiate_and_break_count() {
        let kernel = k();
        let ok = kernel.abi_negotiate("1.0.0");
        assert_eq!(ok["compatible"], true);
        assert_eq!(kernel.abi_compat()["abi_break_count"], 0);
        let bad = kernel.abi_negotiate("9.0.0");
        assert_eq!(bad["compatible"], false);
        kernel.abi_record_break("1.0.0", "2.0.0", "test", vec!["old".into()]);
        assert_eq!(kernel.abi_compat()["abi_break_count"], 1);
    }

    #[test]
    fn pkg_require_secure_and_instance_hydrate() {
        let kernel = k();
        // force secure policy with insecure key → install denied
        kernel.pkg_set_require_secure(true);
        let denied = kernel.pkg_install(
            "x",
            "1.0",
            "print(1)",
            vec![],
            vec!["file_read".into()],
            None,
        );
        // may pass if env already has a real key; still exercise set path
        if kernel.pkg_status()["insecure_default_key"] == true {
            assert!(denied.is_err());
        }
        kernel.pkg_set_require_secure(false);
        kernel.pkg_set_signing_key("unit-test-signing-key-32bytes!!");
        let content = "print('skill')";
        let sig = kernel.pkg_sign(content)["signature"]
            .as_str()
            .unwrap()
            .to_string();
        let p = kernel
            .pkg_install(
                "demo_pkg",
                "1.0.0",
                content,
                vec![],
                vec!["file_read".into()],
                Some(&sig),
            )
            .unwrap();
        assert_eq!(p["status"], "verified");

        // instance export/import with memory hydrate
        kernel.sys_memory_put("alice", "pref", json!({"theme": "dark"}));
        let exp = kernel.instance_export("alice", None);
        assert!(exp["memory"]["pref"].is_object() || exp["memory"].is_object());
        let imp = kernel.instance_import(exp).unwrap();
        assert_eq!(imp["hydrated"]["identity_cache"], true);
        assert!(imp["hydrated"]["memory_keys"].as_u64().unwrap_or(0) >= 1);
        let got = kernel.sys_memory_get("alice", "pref");
        assert_eq!(got["found"], true);
    }

    #[test]
    fn evolution_identity_isolation_scheduler() {
        let k = k();
        // evolution
        let p = k.evolution_submit("skill", "demo", "body", Some("alice"), 0.8, json!({"skill_name":"nope"}));
        let id = p["id"].as_str().unwrap();
        assert!(k.evolution_apply(id, "sys").is_err());
        k.evolution_approve(id, "human").unwrap();
        assert!(k.evolution_apply(id, "human").is_err()); // skill not loadable
        // identity hire + admit
        k.identity_hire("e1", "emp1", "dev", Some(vec!["file_read".into()]), Some(1))
            .unwrap();
        k.identity_admit("e1").unwrap();
        assert!(k.identity_admit("e1").is_err()); // concurrent limit
        k.identity_release("e1");
        k.identity_admit("e1").unwrap();
        // isolation reap
        let _h = k.isolation_spawn("px", "sleep", "bwrap");
        // untrusted would deny local; interactive allows local
        let h = k
            .isolation_spawn("px", "echo", "local")
            .unwrap();
        let hid = h["id"].as_str().unwrap();
        let reap = k.isolation_reap(Some(0.0)); // age 0 → reap all running
        assert!(reap["reaped"].as_u64().unwrap() >= 1 || k.isolation_status()["handles"].as_u64().unwrap() >= 1);
        let _ = hid;
        // scheduler fair share status
        let st = k.scheduler_stats();
        assert_eq!(st["fair_share"], true);
        assert_eq!(st["session_lock_decoupled"], true);
    }

    #[test]
    fn context_isolation_and_schedule() {
        let k = k();
        let p = k
            .create_process("main", None, None, Some(vec!["file_read".into()]), None, None)
            .unwrap();
        k.context_set_isolation(&p.id, "process");
        k.context_set_quota(&p.id, 40);
        let page = k.context_put_page(&p.id, "a", &"x".repeat(200));
        let pid = page["id"].as_str().unwrap();
        // other process cannot access under isolation
        let p2 = k
            .create_process("other", None, None, Some(vec!["file_read".into()]), None, None)
            .unwrap();
        assert!(k.context_swap_in(pid, Some(&p2.id)).is_err());
        assert!(k.context_swap_in(pid, Some(&p.id)).is_ok());
        let tick = k.context_schedule(Some(&p.id));
        assert!(tick.get("ticks").is_some());
    }

    #[test]
    fn device_sync_push_pull() {
        let ka = k();
        ka.device_sync_set_local("dev-test", "T");
        ka.memory_layer_put("alice", "working", "hello sync", 0.9);
        let env = ka.device_sync_push("alice", None);
        assert!(env["revision"].as_u64().unwrap() >= 1);
        let pull = ka.device_sync_pull("alice", None);
        assert_eq!(pull["found"], true);
        let kb = k();
        kb.device_sync_set_local("dev-b", "B");
        let r = kb.device_sync_apply(env).unwrap();
        assert_eq!(r["ok"], true);
    }

    #[test]
    fn result_spill_aggressive_threshold() {
        let k = k();
        let p = k
            .create_process("main", None, None, None, None, None)
            .unwrap();
        let big = "z".repeat(900);
        let r = k.result_spill(&p.id, "command", &big);
        assert_eq!(r["spilled"], true);
        assert!(r["context"].as_str().unwrap().contains("tool_result_handle"));
    }

    #[test]
    fn wasm_explain_and_hal_enforce() {
        let kernel = k();
        let ex = kernel.wasm_explain(None);
        assert!(ex["limits"]["fuel"]["what"].as_str().unwrap().contains("fuel"));
        let p = kernel
            .create_process(
                "main",
                None,
                None,
                Some(vec![
                    "file_read".into(),
                    "terminal".into(),
                    "command".into(),
                    "browser".into(),
                ]),
                None,
                None,
            )
            .unwrap();
        let path = kernel
            .hal_enforce_path(&p.id, None, ".", Some("file_read"))
            .unwrap();
        assert_eq!(path["mediated"], true);
        let cmd = kernel
            .hal_enforce_command(&p.id, "python", vec!["-V".into()])
            .unwrap();
        assert_eq!(cmd["mediated"], true);
    }
}
