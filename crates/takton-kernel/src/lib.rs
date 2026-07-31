//! Takton Agent Kernel — Rust control plane.
//!
//! Process table · capability tokens · mediation · budget ledger ·
//! hash-chained audit · priority scheduler · resource accounts.

pub mod approval_rules;
pub mod audit;
pub mod cache_metrics;
pub mod coding_profile;
pub mod collab;
pub mod domain_events;
pub mod context_vm;
pub mod cost;
pub mod edit_session;
pub mod hal;
pub mod identity_cache;
pub mod inbox;
pub mod instance;
pub mod ipc;
pub mod marathon;
pub mod memory_layers;
pub mod package_mgr;
pub mod repo_index;
pub mod services;
pub mod skill_gate;
pub mod wasm_runtime;
pub mod capability;
pub mod court;
pub mod error;
pub mod intent;
pub mod kernel;
pub mod process;
pub mod process_snapshot;
pub mod resource;
pub mod result_store;
pub mod scheduler;
pub mod tool_catalog;
pub mod llm_admission;
pub mod isolation;
pub mod checkpoint;
pub mod policy;
pub mod run_gate;

pub use audit::{AuditEventStore, KernelEvent};
pub use capability::{CapabilityToken, HMAC_INFO};
pub use court::{decide_capability, CourtDecision};
pub use error::{KernelError, KernelResult};
pub use intent::{
    synthesize_capabilities, synthesize_token, IntentDeclaration, DEFAULT_GRANTABLE,
    IPC_CAPABILITIES, RISKY_CAPABILITIES,
};
pub use tool_catalog::{
    capability_matches, catalog_as_json, crew_cap_for_tool, filter_tool_names,
    tools_for_capabilities, TOOL_TO_CREW_CAP,
};
pub use llm_admission::{
    LlmAcquireResult, LlmAdmissionConfig, LlmAdmissionController, LlmLease, LlmLeaseRequest,
    LlmPriority,
};
pub use isolation::{IsolationHandle, IsolationProfile, IsolationSupervisor};
pub use checkpoint::{CheckpointStore, FileCheckpoint};
pub use court::CourtPolicy;
pub use process_snapshot::{ProcessSnapshot, ProcessSnapshotStore};
pub use result_store::{ResultHandle, ResultSpillStore};
pub use policy::{IterationBudgetState, PolicyDecision, PolicySupervisor};
pub use cache_metrics::CacheMetrics;
pub use cost::CostLedger;
pub use marathon::MarathonMetrics;
pub use ipc::{IpcBus, IpcMessage};
pub use services::{
    MemoryService, NotifyService, ServicePrivilege, ServiceRecord, ServiceSupervisor,
};
pub use identity_cache::{IdentityCache, IdentityRecord};
pub use inbox::{InboxItem, InboxQueue};
pub use skill_gate::{SkillGate, SkillPackage, EVOLUTION_AUTO_APPLY};
pub use context_vm::{ContextPage, ContextVm};
pub use memory_layers::{LayeredMemory, MemoryEntry, MemoryLayer};
pub use coding_profile::{CodingProfile, CodingProfileRegistry};
pub use collab::{ApprovalRequest, CollabHub, CollabSession};
pub use edit_session::{simple_diff, EditSession, EditSessionStore};
pub use repo_index::{RepoIndex, RepoIndexStore};
pub use hal::Hal;
pub use wasm_runtime::{WasmInvokeResult, WasmModule, WasmRuntime};
pub use package_mgr::{InstalledPackage, PackageManager};
pub use instance::{AgentInstanceBundle, InstanceRegistry};
pub use domain_events::{DomainEvent, DomainEventBus};
pub use approval_rules::{
    classify_caps, evolution_requires_review, should_auto_approve, ApprovalPolicy,
    ApprovalRules,
};
pub use run_gate::{RunGate, RunGateResult, RunLease};
pub use kernel::{
    global, init_global, AgentKernel, EscalationRequest, KernelConfig, MediationDecision,
    SoftRenewConfig,
};
pub use process::{AgentProcess, ProcessState};
pub use resource::{ResourceHandle, ResourceKind, ResourceManager};
pub use scheduler::{AgentScheduler, ScheduledTask};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Wire ABI version advertised by host (`abi_version` RPC).
pub const ABI_VERSION: &str = "1.0.0";

/// Canonical method names for ABI v1 (host must implement all).
pub const ABI_METHODS: &[&str] = &[
    "abi_version",
    "list_methods",
    "ping",
    "health",
    "register_service",
    "create_process",
    "end_process",
    "mark_running",
    "suspend_process",
    "resume_process",
    "get_process",
    "list_processes",
    "live_processes_for_identity",
    "retire_live_identity_processes",
    "gc_terminal",
    "mediate",
    "charge_tokens",
    "top_up_budget",
    "try_soft_renew_budget",
    "issue_token",
    "request_escalation",
    "approve_escalation",
    "deny_escalation",
    "list_escalations",
    "get_escalation",
    "events",
    "verify_event_chain",
    "emit",
    "resource_charge",
    "resource_usage",
    "resource_report_rss",
    "scheduler_submit",
    "scheduler_next",
    "scheduler_stats",
    "scheduler_complete",
    "scheduler_cancel_process",
    "capability_narrow",
    "synthesize_intent",
    "apply_intent",
    "synthesize_and_issue",
    "filter_tools",
    "tools_for_process",
    "tool_catalog",
    "schedule_run",
    "llm_try_acquire",
    "llm_poll",
    "llm_release",
    "llm_cancel_wait",
    "llm_charge_quota",
    "llm_status",
    "llm_set_config",
    "run_acquire",
    "run_release",
    "run_gate_try",
    "run_gate_poll",
    "run_gate_release",
    "run_gate_status",
    "run_gate_set_max",
    "decide_tool",
    "set_court_policy",
    "isolation_resolve",
    "isolation_set_profile",
    "isolation_spawn",
    "isolation_complete",
    "checkpoint_begin",
    "checkpoint_restore",
    "checkpoint_list",
    "export_decision_trail",
    // P0.5
    "process_snapshot",
    "process_snapshot_latest",
    "process_snapshot_list",
    "process_recovery_plan",
    "result_spill",
    "result_load",
    "result_store_status",
    "iteration_set_budget",
    "iteration_consume",
    "iteration_refund",
    "iteration_status",
    "doom_record",
    "doom_reset",
    "doom_status",
    "policy_status",
    "cache_record",
    "cache_metrics",
    "cost_charge",
    "cost_panel",
    "cost_process",
    "marathon_record",
    "marathon_metrics",
    "reclaim_process_tree",
    // P1-A
    "ipc_send",
    "ipc_recv",
    "ipc_status",
    "service_register",
    "service_list",
    "service_health",
    "service_status",
    "sys_memory_put",
    "sys_memory_get",
    "sys_memory_list",
    "sys_notify_push",
    "sys_notify_list",
    "sys_notify_ack",
    "identity_cache_put",
    "identity_cache_get",
    "identity_cache_list",
    "inbox_submit",
    "inbox_claim",
    "inbox_complete",
    "inbox_fail",
    "inbox_release",
    "inbox_list",
    "inbox_status",
    // P1-B
    "skill_register",
    "skill_verify",
    "skill_activate",
    "skill_rollback",
    "skill_get_active",
    "skill_list",
    "skill_is_loadable",
    "skill_gate_status",
    "evolution_policy",
    "context_set_quota",
    "context_put_page",
    "context_swap_in",
    "context_swap_out",
    "context_list_pages",
    "context_status",
    "memory_layer_put",
    "memory_layer_list",
    "memory_layer_consolidate",
    "memory_layer_status",
    // P2
    "coding_profile_list",
    "coding_profile_get",
    "coding_profile_apply",
    "collab_set_plan",
    "collab_revise_plan",
    "collab_interrupt",
    "collab_resume",
    "collab_request_approval",
    "collab_resolve_approval",
    "collab_get",
    "edit_propose",
    "edit_confirm",
    "edit_reject",
    "edit_rollback",
    "edit_list",
    "edit_get",
    "repo_index_build",
    "repo_index_get",
    "repo_index_list",
    "hal_platform",
    "hal_resolve_path",
    "hal_resolve_command",
    "hal_resolve_browser",
    "hal_status",
    "wasm_load",
    "wasm_activate",
    "wasm_invoke",
    "wasm_unload",
    "wasm_kill",
    "wasm_list",
    "wasm_status",
    "pkg_install",
    "pkg_activate",
    "pkg_uninstall",
    "pkg_list",
    "pkg_get",
    "pkg_sign",
    "pkg_set_signing_key",
    "pkg_scan",
    "pkg_promote",
    "pkg_catalog",
    "pkg_status",
    "instance_export",
    "instance_import",
    "instance_list",
    "instance_status",
    "abi_compat",
    // R3 de-dualize
    "domain_publish",
    "domain_recent",
    "domain_seq",
    "domain_status",
    "approval_set_rules",
    "approval_get_rules",
    "approval_classify",
    "approval_should_auto",
];
