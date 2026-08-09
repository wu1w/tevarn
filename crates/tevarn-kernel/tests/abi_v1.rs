//! ABI v1 contract tests (in-process, no host).
//!
//! Documents the golden path that Python host tests must mirror.

use tevarn_kernel::{
    AgentKernel, KernelConfig, SoftRenewConfig, ABI_METHODS, ABI_VERSION, VERSION,
};

fn kernel() -> AgentKernel {
    AgentKernel::new(KernelConfig {
        audit_persist: false,
        require_intent: false,
        soft_renew: SoftRenewConfig {
            enabled: false,
            ..Default::default()
        },
        ..Default::default()
    })
}

#[test]
fn abi_version_constants() {
    assert_eq!(ABI_VERSION, "1.0.0");
    assert!(!VERSION.is_empty());
    assert!(ABI_METHODS.contains(&"create_process"));
    assert!(ABI_METHODS.contains(&"mediate"));
    assert!(ABI_METHODS.contains(&"abi_version"));
    assert!(ABI_METHODS.contains(&"get_escalation"));
    assert!(ABI_METHODS.contains(&"scheduler_complete"));
    assert!(ABI_METHODS.len() >= 30);
}

#[test]
fn golden_create_mediate_charge_chain() {
    let k = kernel();
    let p = k
        .create_process(
            "abi",
            Some("s1"),
            None,
            Some(vec!["file_read".into(), "grep".into()]),
            Some(1000),
            None,
        )
        .expect("create");
    assert_eq!(p.id.len(), 16);
    assert_eq!(p.state.as_str(), "created");

    k.mark_running(&p.id).unwrap();
    let d = k.mediate(&p.id, "tool_call", "file_read", None).unwrap();
    assert!(d.allowed);
    assert!(d.capability_checked);

    assert!(k.mediate(&p.id, "tool_call", "terminal", None).is_err());

    let rem = k.charge_tokens(&p.id, 100).unwrap();
    assert_eq!(rem, Some(900));

    let (ok, idx) = k.verify_event_chain();
    assert!(ok, "chain broken at {idx}");

    let kinds: Vec<_> = k
        .events(None, None, 100)
        .into_iter()
        .map(|e| e.kind)
        .collect();
    assert!(kinds.contains(&"process_created".into()));
    assert!(kinds.contains(&"mediation".into()));
    assert!(kinds.contains(&"policy.decision".into()));
}

#[test]
fn golden_escalation_and_scheduler() {
    let k = kernel();
    let p = k
        .create_process(
            "abi",
            None,
            None,
            Some(vec!["file_read".into()]),
            None,
            None,
        )
        .unwrap();
    let req = k
        .request_escalation(&p.id, vec!["terminal".into()], "need shell")
        .unwrap();
    assert_eq!(req.status, "pending");
    assert!(k.get_escalation(&req.id).is_some());

    let approved = k.approve_escalation(&req.id, "test").unwrap();
    assert_eq!(approved.status, "approved");
    let after = k.get_process(&p.id).unwrap();
    assert!(after
        .capabilities
        .as_ref()
        .unwrap()
        .iter()
        .any(|c| c == "terminal"));

    let task = k.scheduler_submit(&p.id, serde_json::json!({"op": "x"}), 5);
    let tid = task["id"].as_str().unwrap().to_string();
    let next = k.scheduler_next().unwrap();
    assert_eq!(next["id"], tid);
    k.scheduler_complete(&tid, false);
    let stats = k.scheduler_stats();
    assert!(stats["done"].as_u64().unwrap_or(0) >= 1);
}

#[test]
fn golden_live_identity_and_retire() {
    let k = kernel();
    let a = k
        .create_process("wf:alice", None, None, None, None, None)
        .unwrap();
    let _b = k
        .create_process("wf:alice", None, None, None, None, None)
        .unwrap();
    let live = k.live_processes_for_identity("wf:alice");
    assert_eq!(live.len(), 2);
    let killed = k.retire_live_identity_processes("wf:alice", "new job", Some(&a.id));
    assert_eq!(killed.len(), 1);
    assert_ne!(killed[0], a.id);
    let still = k.live_processes_for_identity("wf:alice");
    assert_eq!(still.len(), 1);
    assert_eq!(still[0].id, a.id);
}
