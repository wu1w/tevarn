//! Loop thrash / fan-out guards (aligned with Claude Code max_turns + Grok explore caps).
//!
//! Authority lives in the kernel so Python cannot drift. Python consults these RPCs
//! before tool execution and after each tool round.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

const ORCH_TOOLS: &[&str] = &[
    "crew_steward",
    "delegate_task",
    "agent_call",
    "manage_sub_agent",
];

fn is_orch_tool(name: &str) -> bool {
    let n = name.trim();
    ORCH_TOOLS.iter().any(|t| *t == n)
}

/// Research vs implement vs chat/steward profiles.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RoleKind {
    #[default]
    Chat,
    Steward,
    Research,
    Implement,
}

impl RoleKind {
    pub fn parse(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "research" | "explore" | "researcher" | "鐮旂┒鍛? => Self::Research,
            "implement" | "engineer" | "impl" | "宸ョ▼甯? | "coding" => Self::Implement,
            "steward" | "ceo" | "main" => Self::Steward,
            _ => Self::Chat,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Thoroughness {
    Quick,
    #[default]
    Medium,
    VeryThorough,
}

impl Thoroughness {
    pub fn parse(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "quick" | "fast" | "娴? => Self::Quick,
            "very_thorough" | "very-thorough" | "thorough" | "deep" | "娣? => Self::VeryThorough,
            _ => Self::Medium,
        }
    }

    pub fn max_tool_rounds(self) -> u32 {
        match self {
            Self::Quick => 6,
            Self::Medium => 12,
            Self::VeryThorough => 16,
        }
    }

    pub fn max_file_reads(self) -> u32 {
        match self {
            Self::Quick => 8,
            Self::Medium => 20,
            Self::VeryThorough => 40,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopGuardConfig {
    pub workforce: bool,
    pub role_kind: RoleKind,
    pub thoroughness: Option<Thoroughness>,
    pub max_tool_rounds: u32,
    /// Total successful crew_steward (etc.) executions this run.
    pub max_crew_total: u32,
    /// Max orchestration tools executed per tool round.
    pub max_orch_per_round: u32,
    /// Force final when tokens_used / token_budget >= this (e.g. 0.85).
    pub budget_force_ratio: f64,
    pub max_file_reads: u32,
    /// Ban worker re-dispatch tools.
    pub ban_worker_orch: bool,
}

impl Default for LoopGuardConfig {
    fn default() -> Self {
        Self {
            workforce: false,
            role_kind: RoleKind::Chat,
            thoroughness: None,
            max_tool_rounds: 40,
            max_crew_total: 8,
            max_orch_per_round: 1,
            budget_force_ratio: 0.85,
            max_file_reads: 80,
            ban_worker_orch: false,
        }
    }
}

impl LoopGuardConfig {
    pub fn for_role(
        workforce: bool,
        role: RoleKind,
        thoroughness: Option<Thoroughness>,
    ) -> Self {
        let mut c = Self::default();
        c.workforce = workforce;
        c.role_kind = role;
        c.thoroughness = thoroughness;
        match role {
            RoleKind::Research => {
                let th = thoroughness.unwrap_or(Thoroughness::Medium);
                c.max_tool_rounds = th.max_tool_rounds();
                c.max_file_reads = th.max_file_reads();
                c.max_crew_total = 0;
                c.max_orch_per_round = 0;
                c.ban_worker_orch = true;
            }
            RoleKind::Implement => {
                c.max_tool_rounds = 20;
                c.max_file_reads = 50;
                c.max_crew_total = 0;
                c.max_orch_per_round = 0;
                c.ban_worker_orch = true;
            }
            RoleKind::Steward => {
                c.max_tool_rounds = 40;
                c.max_crew_total = 3;
                c.max_orch_per_round = 1;
                c.ban_worker_orch = false;
            }
            // Grok-style interactive thin: hard turn cap, no crew fan-out
            RoleKind::Chat => {
                c.max_tool_rounds = 8;
                c.max_file_reads = 16;
                c.max_crew_total = 0;
                c.max_orch_per_round = 0;
                c.ban_worker_orch = true;
            }
        }
        if workforce && matches!(role, RoleKind::Chat) {
            // Unknown workforce 鈫?treat as implement-ish, still ban recursive crew
            c.max_tool_rounds = 16;
            c.ban_worker_orch = true;
            c.max_crew_total = 0;
            c.max_orch_per_round = 0;
        }
        c
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "workforce": self.workforce,
            "role_kind": self.role_kind,
            "thoroughness": self.thoroughness,
            "max_tool_rounds": self.max_tool_rounds,
            "max_crew_total": self.max_crew_total,
            "max_orch_per_round": self.max_orch_per_round,
            "budget_force_ratio": self.budget_force_ratio,
            "max_file_reads": self.max_file_reads,
            "ban_worker_orch": self.ban_worker_orch,
        })
    }
}

fn contains_ci(hay: &str, needle: &str) -> bool {
    hay.to_ascii_lowercase()
        .contains(&needle.to_ascii_lowercase())
}

fn contains_any_ci(hay: &str, needles: &[&str]) -> bool {
    needles.iter().any(|n| contains_ci(hay, n))
}

/// Resolve RoleKind from identity / instruction / harness flags.
/// Authority for loop_guard 鈥?Python must not force-upgrade chat鈫抯teward.
pub fn resolve_role_kind(
    workforce: bool,
    identity_name: &str,
    identity_role: &str,
    instruction: &str,
    explicit_role: Option<&str>,
    light_loop: bool,
    ops_loop: bool,
) -> RoleKind {
    // Grok thin: harness light/ops always chat (ban crew)
    if light_loop || ops_loop {
        return RoleKind::Chat;
    }
    if let Some(ex) = explicit_role {
        let ex_l = ex.trim().to_ascii_lowercase();
        // harness tip "coding" is NOT an explicit RoleKind 鈥?skip so identity can win
        if ex_l != "coding" && ex_l != "code" {
            let p = RoleKind::parse(ex);
            if !matches!(p, RoleKind::Chat) || ex_l == "chat" {
                if workforce && matches!(p, RoleKind::Chat) {
                    // fall through
                } else {
                    return p;
                }
            }
        }
    }
    let blob = format!("{identity_name} {identity_role}");
    if contains_any_ci(&blob, &["鐮旂┒", "research", "explore", "鍒嗘瀽甯?, "鐮旂┒鍛?]) {
        return RoleKind::Research;
    }
    // Real steward/CEO identity only 鈥?never force plain chat to steward
    if !workforce
        && contains_any_ci(
            &blob,
            &["CEO", "ceo", "绠″", "steward", "Steward", "澶х瀹?],
        )
    {
        return RoleKind::Steward;
    }
    let instr = instruction;
    if workforce {
        if contains_any_ci(
            instr,
            &[
                "璋冪爺",
                "鐮旂┒",
                "research",
                "explore",
                "鍙",
                "read only",
                "read-only",
                "GitHub",
                "璇勪及",
                "鍒嗘瀽",
                "鎬庝箞瀹炵幇",
                "鏈€灏忔敼鍔?,
                "鎴愮啛鍋氭硶",
                "瀹樻柟",
            ],
        ) {
            return RoleKind::Research;
        }
        return RoleKind::Implement;
    }
    // Main-chat engineering (harness coding, non-steward): implement-like caps
    // so multi-step code work is not starved at Chat max_tool_rounds=15
    if contains_any_ci(
        instr,
        &[
            "閲嶆瀯",
            "鏀逛唬鐮?,
            "瀹炵幇",
            "淇?bug",
            "淇bug",
            "pytest",
            "缂栬瘧",
            "鐧诲綍椤?,
            "code review",
            "review pr",
            "apply_patch",
            "github.com",
            "pull request",
        ],
    ) {
        return RoleKind::Implement;
    }
    RoleKind::Chat
}

pub fn resolve_thoroughness(payload_th: Option<&str>, instruction: &str) -> Thoroughness {
    if let Some(t) = payload_th {
        return Thoroughness::parse(t);
    }
    if contains_any_ci(instruction, &["蹇€?, "绮楃湅", "quick", "鎵竴鐪?]) {
        return Thoroughness::Quick;
    }
    if contains_any_ci(
        instruction,
        &["娣卞叆", "绌峰敖", "鍏ㄩ潰", "very thorough", "very_thorough", "deep"],
    ) {
        return Thoroughness::VeryThorough;
    }
    Thoroughness::Medium
}

/// Full loop_guard plan from RPC params (role + config). No process mutation.
pub fn resolve_role_from_value(params: &Value) -> Value {
    let workforce = params
        .get("workforce")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let identity_name = params
        .get("identity_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let identity_role = params
        .get("identity_role")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let instruction = params
        .get("instruction")
        .or_else(|| params.get("text"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let light = params
        .get("light_loop")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let ops = params
        .get("ops_loop")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let explicit = params
        .get("role_kind")
        .or_else(|| params.get("role"))
        .and_then(|v| v.as_str())
        .or_else(|| {
            params
                .get("payload")
                .and_then(|p| p.get("role_kind").or_else(|| p.get("role")))
                .and_then(|v| v.as_str())
        });
    let th_s = params
        .get("thoroughness")
        .and_then(|v| v.as_str())
        .or_else(|| {
            params
                .get("payload")
                .and_then(|p| p.get("thoroughness"))
                .and_then(|v| v.as_str())
        });
    let role = resolve_role_kind(
        workforce,
        identity_name,
        identity_role,
        instruction,
        explicit,
        light,
        ops,
    );
    let th = if matches!(role, RoleKind::Research) {
        Some(resolve_thoroughness(th_s, instruction))
    } else {
        None
    };
    let mut cfg = LoopGuardConfig::for_role(workforce, role, th);
    // Optional max_iters override from harness
    if let Some(m) = params.get("max_tool_rounds").and_then(|v| v.as_u64()) {
        if m > 0 {
            cfg.max_tool_rounds = m as u32;
        }
    } else if let Some(m) = params.get("max_iters").and_then(|v| v.as_u64()) {
        if m > 0 && (light || ops) {
            cfg.max_tool_rounds = m as u32;
        }
    }
    // harness_mode=coding + non-thin 鈫?ensure engineering rounds
    let hmode = params
        .get("harness_mode")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !light
        && !ops
        && hmode.eq_ignore_ascii_case("coding")
        && matches!(role, RoleKind::Chat)
    {
        // non-steward coding main: implement-like budget
        cfg.role_kind = RoleKind::Implement;
        cfg.max_tool_rounds = 40;
        cfg.max_file_reads = 50;
        cfg.max_crew_total = 0;
        cfg.max_orch_per_round = 0;
        cfg.ban_worker_orch = true;
    }
    if !light
        && !ops
        && hmode.eq_ignore_ascii_case("coding")
        && matches!(role, RoleKind::Steward)
    {
        cfg.max_tool_rounds = cfg.max_tool_rounds.max(40);
    }
    if light || ops {
        cfg.role_kind = RoleKind::Chat;
        cfg.ban_worker_orch = true;
        cfg.max_crew_total = 0;
        cfg.max_orch_per_round = 0;
    }
    let mut d = cfg.to_dict();
    if let Some(obj) = d.as_object_mut() {
        obj.insert("authority".into(), json!("rust"));
        // serde may emit enum variant; normalize to plain string for Python
        obj.insert(
            "role_kind".into(),
            json!(match role {
                RoleKind::Chat => "chat",
                RoleKind::Steward => "steward",
                RoleKind::Research => "research",
                RoleKind::Implement => "implement",
            }),
        );
    }
    d
}

#[cfg(test)]
mod role_tests {
    use super::*;

    #[test]
    fn thin_never_steward() {
        let r = resolve_role_kind(false, "澶х瀹?, "CEO", "澶╂皵鎬庝箞鏍?, None, true, false);
        assert_eq!(r, RoleKind::Chat);
    }

    #[test]
    fn identity_steward_when_coding() {
        let r = resolve_role_kind(false, "澶х瀹?, "CEO", "閲嶆瀯鐧诲綍椤?, None, false, false);
        assert_eq!(r, RoleKind::Steward);
    }

    #[test]
    fn workforce_implement() {
        let r = resolve_role_kind(true, "宸ョ▼甯圓", "engineer", "瀹炵幇鎺ュ彛", None, false, false);
        assert_eq!(r, RoleKind::Implement);
    }
}

#[derive(Debug, Clone)]
struct TruncPath {
    times: u32,
    last_truncated: bool,
}

#[derive(Debug, Clone)]
struct GuardState {
    config: LoopGuardConfig,
    tool_rounds: u32,
    file_reads: u32,
    crew_total: u32,
    orch_this_round: u32,
    /// Sliding window of last N round family labels ("orch" | "read" | "other")
    round_families: Vec<String>,
    truncated_paths: HashMap<String, TruncPath>,
    force_final: bool,
    force_reason: String,
    force_code: String,
}

impl GuardState {
    fn new(config: LoopGuardConfig) -> Self {
        Self {
            config,
            tool_rounds: 0,
            file_reads: 0,
            crew_total: 0,
            orch_this_round: 0,
            round_families: Vec::new(),
            truncated_paths: HashMap::new(),
            force_final: false,
            force_reason: String::new(),
            force_code: String::new(),
        }
    }

    fn trip(&mut self, code: &str, reason: &str) {
        self.force_final = true;
        self.force_code = code.to_string();
        self.force_reason = reason.to_string();
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum GuardDecision {
    Allow {
        process_id: String,
    },
    Block {
        process_id: String,
        tool: String,
        code: String,
        reason: String,
        /// Synthetic tool result body for the model
        message: String,
    },
    ForceFinal {
        process_id: String,
        code: String,
        reason: String,
        action: String,
    },
}

impl GuardDecision {
    pub fn to_dict(&self) -> Value {
        match self {
            Self::Allow { process_id } => json!({
                "status": "allow",
                "process_id": process_id,
            }),
            Self::Block {
                process_id,
                tool,
                code,
                reason,
                message,
            } => json!({
                "status": "block",
                "process_id": process_id,
                "tool": tool,
                "code": code,
                "reason": reason,
                "message": message,
                "action": "skip_tool",
            }),
            Self::ForceFinal {
                process_id,
                code,
                reason,
                action,
            } => json!({
                "status": "force_final",
                "process_id": process_id,
                "code": code,
                "reason": reason,
                "action": action,
            }),
        }
    }

    pub fn is_blocking(&self) -> bool {
        !matches!(self, Self::Allow { .. })
    }
}

pub struct LoopGuardSupervisor {
    states: HashMap<String, GuardState>,
}

impl Default for LoopGuardSupervisor {
    fn default() -> Self {
        Self {
            states: HashMap::new(),
        }
    }
}

impl LoopGuardSupervisor {
    pub fn configure(&mut self, process_id: &str, config: LoopGuardConfig) {
        self.states
            .insert(process_id.to_string(), GuardState::new(config));
    }

    pub fn configure_from_value(&mut self, process_id: &str, v: &Value) {
        let workforce = v
            .get("workforce")
            .and_then(|x| x.as_bool())
            .unwrap_or(false);
        let role = v
            .get("role_kind")
            .or_else(|| v.get("role"))
            .and_then(|x| x.as_str())
            .map(RoleKind::parse)
            .unwrap_or(if workforce {
                RoleKind::Implement
            } else {
                RoleKind::Chat
            });
        let thoroughness = v
            .get("thoroughness")
            .and_then(|x| x.as_str())
            .map(Thoroughness::parse);
        let mut cfg = LoopGuardConfig::for_role(workforce, role, thoroughness);
        if let Some(n) = v.get("max_tool_rounds").and_then(|x| x.as_u64()) {
            cfg.max_tool_rounds = n as u32;
        }
        if let Some(n) = v.get("max_crew_total").and_then(|x| x.as_u64()) {
            cfg.max_crew_total = n as u32;
        }
        if let Some(n) = v.get("max_orch_per_round").and_then(|x| x.as_u64()) {
            cfg.max_orch_per_round = n as u32;
        }
        if let Some(r) = v.get("budget_force_ratio").and_then(|x| x.as_f64()) {
            if r > 0.0 && r <= 1.0 {
                cfg.budget_force_ratio = r;
            }
        }
        if let Some(b) = v.get("ban_worker_orch").and_then(|x| x.as_bool()) {
            cfg.ban_worker_orch = b;
        }
        if let Some(n) = v.get("max_file_reads").and_then(|x| x.as_u64()) {
            cfg.max_file_reads = n as u32;
        }
        self.configure(process_id, cfg);
    }

    fn ensure(&mut self, process_id: &str) -> &mut GuardState {
        if !self.states.contains_key(process_id) {
            self.states
                .insert(process_id.to_string(), GuardState::new(LoopGuardConfig::default()));
        }
        self.states.get_mut(process_id).unwrap()
    }

    /// Call once at start of a tool-using iteration (after LLM returns tools).
    pub fn begin_round(&mut self, process_id: &str, tool_names: &[String]) -> GuardDecision {
        let st = self.ensure(process_id);
        if st.force_final {
            return GuardDecision::ForceFinal {
                process_id: process_id.to_string(),
                code: st.force_code.clone(),
                reason: st.force_reason.clone(),
                action: "force_final_no_tools".into(),
            };
        }
        st.tool_rounds += 1;
        st.orch_this_round = 0;

        let n = tool_names.len().max(1) as f64;
        let orch = tool_names.iter().filter(|t| is_orch_tool(t)).count() as f64;
        let reads = tool_names
            .iter()
            .filter(|t| {
                matches!(
                    t.as_str(),
                    "file_read" | "grep" | "glob" | "read" | "doc_read"
                )
            })
            .count() as f64;
        let fam = if orch * 2.0 >= n {
            "orch"
        } else if reads * 2.0 >= n {
            "read"
        } else {
            "other"
        };
        st.round_families.push(fam.to_string());
        if st.round_families.len() > 8 {
            let excess = st.round_families.len() - 8;
            st.round_families.drain(0..excess);
        }

        // Sliding window: 鈮? orch-heavy in last 5 rounds 鈫?force final
        let window: Vec<&str> = st
            .round_families
            .iter()
            .rev()
            .take(5)
            .map(|s| s.as_str())
            .collect();
        let orch_n = window.iter().filter(|f| **f == "orch").count();
        if orch_n >= 3 {
            st.trip(
                "orch_window_thrash",
                &format!(
                    "orchestration-heavy rounds {orch_n}/5 in sliding window 鈥?stop dispatching"
                ),
            );
            return GuardDecision::ForceFinal {
                process_id: process_id.to_string(),
                code: "orch_window_thrash".into(),
                reason: st.force_reason.clone(),
                action: "force_final_no_tools".into(),
            };
        }

        if st.tool_rounds > st.config.max_tool_rounds {
            st.trip(
                "max_tool_rounds",
                &format!(
                    "tool rounds {} exceeded max {}",
                    st.tool_rounds, st.config.max_tool_rounds
                ),
            );
            return GuardDecision::ForceFinal {
                process_id: process_id.to_string(),
                code: "max_tool_rounds".into(),
                reason: st.force_reason.clone(),
                action: "force_final_no_tools".into(),
            };
        }
        GuardDecision::Allow {
            process_id: process_id.to_string(),
        }
    }

    /// Pre-tool gate. Returns Allow / Block / ForceFinal.
    pub fn pre_tool(
        &mut self,
        process_id: &str,
        tool: &str,
        args: &Value,
    ) -> GuardDecision {
        let st = self.ensure(process_id);
        if st.force_final {
            return GuardDecision::ForceFinal {
                process_id: process_id.to_string(),
                code: st.force_code.clone(),
                reason: st.force_reason.clone(),
                action: "force_final_no_tools".into(),
            };
        }

        let tool = tool.trim();

        // Worker: ban recursive orchestration (Claude Code: sub has no task tool)
        if st.config.ban_worker_orch && is_orch_tool(tool) {
            return GuardDecision::Block {
                process_id: process_id.to_string(),
                tool: tool.to_string(),
                code: "worker_orch_banned".into(),
                reason: "workforce workers cannot call crew_steward/delegate (no recursive fan-out)"
                    .into(),
                message: format!(
                    "[LoopGuard] 瀛愬伐鍗曠姝㈠啀璋冪敤 {tool}銆傝鐢ㄥ凡鏈夊伐鍏风洿鎺ュ畬鎴愪换鍔★紝\
                     鐢ㄤ腑鏂囩粰鍑虹粨璁?鍗＄偣锛涘嬁鍐嶆淳宸ユ垨 crew_steward銆?
                ),
            };
        }

        // Session crew total cap (steward/chat)
        if is_orch_tool(tool) {
            if st.config.max_orch_per_round == 0 {
                return GuardDecision::Block {
                    process_id: process_id.to_string(),
                    tool: tool.to_string(),
                    code: "orch_per_round_zero".into(),
                    reason: "max_orch_per_round=0".into(),
                    message: format!(
                        "[LoopGuard] 鏈?run 涓嶅厑璁哥紪鍒剁被宸ュ叿 {tool}銆傝鐩存帴骞叉椿鎴栦氦鍗枫€?
                    ),
                };
            }
            if st.orch_this_round >= st.config.max_orch_per_round {
                return GuardDecision::Block {
                    process_id: process_id.to_string(),
                    tool: tool.to_string(),
                    code: "orch_per_round_cap".into(),
                    reason: format!(
                        "orch tools this round {} >= max {}",
                        st.orch_this_round, st.config.max_orch_per_round
                    ),
                    message: format!(
                        "[Orchestration cap] 鏈疆 {tool} 宸茶揪涓婇檺 {}銆傝娑堝寲宸叉湁宸ュ崟缁撴灉锛屽嬁鎵归噺绌烘淳銆?,
                        st.config.max_orch_per_round
                    ),
                };
            }
            if st.crew_total >= st.config.max_crew_total {
                return GuardDecision::Block {
                    process_id: process_id.to_string(),
                    tool: tool.to_string(),
                    code: "crew_total_cap".into(),
                    reason: format!(
                        "crew_total {} >= max {}",
                        st.crew_total, st.config.max_crew_total
                    ),
                    message: format!(
                        "[LoopGuard] 鏈細璇濈紪鍒惰皟鐢ㄥ凡杈句笂闄?{}銆傝姹囨€诲凡鏈夊伐鍗曠粨鏋滅粰涓讳汉锛岀姝㈠啀娲俱€?,
                        st.config.max_crew_total
                    ),
                };
            }
        }

        // Truncated path re-read block (full re-read without offset after truncate)
        if tool == "file_read" || tool == "read" {
            let path = args
                .get("path")
                .or_else(|| args.get("file"))
                .or_else(|| args.get("file_path"))
                .or_else(|| args.get("filepath"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let has_offset = args.get("offset").and_then(|v| v.as_i64()).unwrap_or(0) > 0
                || args.get("start_line").and_then(|v| v.as_i64()).unwrap_or(0) > 0
                || args.get("limit").and_then(|v| v.as_i64()).is_some();
            if !path.is_empty() {
                if let Some(tp) = st.truncated_paths.get(&path) {
                    if tp.last_truncated && !has_offset {
                        return GuardDecision::Block {
                            process_id: process_id.to_string(),
                            tool: tool.to_string(),
                            code: "truncated_reread_blocked".into(),
                            reason: format!("path {path} was truncated; full re-read blocked"),
                            message: format!(
                                "[LoopGuard] 鏂囦欢宸叉埅鏂杩囷細{path}\n\
                                 绂佹鏁存枃浠堕噸璇汇€傝鏀圭敤 offset/limit 缁锛屾垨 grep 瀹氱偣锛沑
                                 鎴栫洿鎺ュ熀浜庡凡鏈夌墖娈典笅缁撹銆?
                            ),
                        };
                    }
                }
            }
            if st.file_reads >= st.config.max_file_reads {
                return GuardDecision::Block {
                    process_id: process_id.to_string(),
                    tool: tool.to_string(),
                    code: "max_file_reads".into(),
                    reason: format!(
                        "file_reads {} >= max {}",
                        st.file_reads, st.config.max_file_reads
                    ),
                    message: format!(
                        "[LoopGuard] 鏈?run file_read 娆℃暟宸茶揪涓婇檺 {}銆傝鍋滄鎵枃浠讹紝鐩存帴缁欏嚭缁撹涓庣己鍙ｃ€?,
                        st.config.max_file_reads
                    ),
                };
            }
        }

        GuardDecision::Allow {
            process_id: process_id.to_string(),
        }
    }

    /// After a tool actually ran (not blocked).
    pub fn post_tool(
        &mut self,
        process_id: &str,
        tool: &str,
        args: &Value,
        truncated: bool,
        result_len: u64,
    ) {
        let st = self.ensure(process_id);
        let tool = tool.trim();
        if is_orch_tool(tool) {
            st.orch_this_round = st.orch_this_round.saturating_add(1);
            st.crew_total = st.crew_total.saturating_add(1);
        }
        if tool == "file_read" || tool == "read" {
            st.file_reads = st.file_reads.saturating_add(1);
            let path = args
                .get("path")
                .or_else(|| args.get("file"))
                .or_else(|| args.get("file_path"))
                .or_else(|| args.get("filepath"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            // Heuristic: classic truncated length band or explicit flag
            let looks_trunc = truncated
                || (result_len >= 380 && result_len <= 420)
                || (result_len > 0 && result_len < 500 && result_len % 1 == 0);
            // Prefer explicit truncated flag; length band alone only if marked omitted in post
            let mark = truncated;
            if !path.is_empty() && mark {
                let e = st
                    .truncated_paths
                    .entry(path)
                    .or_insert(TruncPath {
                        times: 0,
                        last_truncated: false,
                    });
                e.times = e.times.saturating_add(1);
                e.last_truncated = true;
            }
            let _ = looks_trunc; // reserved for future soft scoring
        }
    }

    /// Token budget ratio check (call each iteration).
    pub fn budget_check(
        &mut self,
        process_id: &str,
        tokens_used: i64,
        token_budget: Option<i64>,
    ) -> GuardDecision {
        let st = self.ensure(process_id);
        if st.force_final {
            return GuardDecision::ForceFinal {
                process_id: process_id.to_string(),
                code: st.force_code.clone(),
                reason: st.force_reason.clone(),
                action: "force_final_no_tools".into(),
            };
        }
        if let Some(b) = token_budget {
            if b > 0 {
                let ratio = tokens_used as f64 / b as f64;
                if ratio >= st.config.budget_force_ratio {
                    st.trip(
                        "budget_ratio",
                        &format!(
                            "tokens_used/budget={ratio:.2} >= {}",
                            st.config.budget_force_ratio
                        ),
                    );
                    return GuardDecision::ForceFinal {
                        process_id: process_id.to_string(),
                        code: "budget_ratio".into(),
                        reason: st.force_reason.clone(),
                        action: "force_final_no_tools".into(),
                    };
                }
            }
        }
        GuardDecision::Allow {
            process_id: process_id.to_string(),
        }
    }

    pub fn status(&self, process_id: &str) -> Value {
        match self.states.get(process_id) {
            Some(st) => json!({
                "process_id": process_id,
                "config": st.config.to_dict(),
                "tool_rounds": st.tool_rounds,
                "file_reads": st.file_reads,
                "crew_total": st.crew_total,
                "orch_this_round": st.orch_this_round,
                "force_final": st.force_final,
                "force_code": st.force_code,
                "force_reason": st.force_reason,
                "truncated_paths": st.truncated_paths.len(),
                "round_families": st.round_families,
                "ts": now_secs(),
            }),
            None => json!({
                "process_id": process_id,
                "configured": false,
                "ts": now_secs(),
            }),
        }
    }

    pub fn drop_process(&mut self, process_id: &str) {
        self.states.remove(process_id);
    }

    pub fn global_status(&self) -> Value {
        json!({
            "tracked": self.states.len(),
            "ts": now_secs(),
        })
    }
}

/// Detect truncation markers in tool result text.
pub fn result_looks_truncated(text: &str) -> bool {
    let t = text;
    t.contains("omitted for LLM")
        || t.contains("chars omitted")
        || t.contains("[truncated]")
        || t.contains("persisted-output")
        || t.contains("鈥︾渷鐣?)
        || t.contains("...[")
        || t.contains("more lines]")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_bans_crew() {
        let mut g = LoopGuardSupervisor::default();
        g.configure(
            "p1",
            LoopGuardConfig::for_role(true, RoleKind::Research, Some(Thoroughness::Medium)),
        );
        let d = g.pre_tool("p1", "crew_steward", &json!({"action": "assign"}));
        assert!(matches!(d, GuardDecision::Block { .. }));
    }

    #[test]
    fn max_rounds_trips() {
        let mut g = LoopGuardSupervisor::default();
        let mut c = LoopGuardConfig::for_role(true, RoleKind::Research, Some(Thoroughness::Quick));
        c.max_tool_rounds = 2;
        g.configure("p1", c);
        assert!(matches!(
            g.begin_round("p1", &["file_read".into()]),
            GuardDecision::Allow { .. }
        ));
        assert!(matches!(
            g.begin_round("p1", &["file_read".into()]),
            GuardDecision::Allow { .. }
        ));
        // third round: tool_rounds becomes 3 > 2
        let d = g.begin_round("p1", &["file_read".into()]);
        assert!(matches!(d, GuardDecision::ForceFinal { .. }));
    }

    #[test]
    fn truncated_reread_blocked() {
        let mut g = LoopGuardSupervisor::default();
        g.configure(
            "p1",
            LoopGuardConfig::for_role(true, RoleKind::Implement, None),
        );
        g.post_tool(
            "p1",
            "file_read",
            &json!({"path": "a.py"}),
            true,
            399,
        );
        let d = g.pre_tool("p1", "file_read", &json!({"path": "a.py"}));
        assert!(matches!(d, GuardDecision::Block { code, .. } if code == "truncated_reread_blocked"));
        // with offset allowed
        let d2 = g.pre_tool(
            "p1",
            "file_read",
            &json!({"path": "a.py", "offset": 100}),
        );
        assert!(matches!(d2, GuardDecision::Allow { .. }));
    }

    #[test]
    fn budget_ratio_force() {
        let mut g = LoopGuardSupervisor::default();
        g.configure("p1", LoopGuardConfig::default());
        let d = g.budget_check("p1", 90_000, Some(100_000));
        assert!(matches!(d, GuardDecision::ForceFinal { code, .. } if code == "budget_ratio"));
    }

    #[test]
    fn steward_crew_cap() {
        let mut g = LoopGuardSupervisor::default();
        let mut c = LoopGuardConfig::for_role(false, RoleKind::Steward, None);
        c.max_crew_total = 2;
        c.max_orch_per_round = 1; // one per round; total still 2 across rounds
        g.configure("p1", c);
        g.begin_round("p1", &["crew_steward".into()]);
        assert!(matches!(
            g.pre_tool("p1", "crew_steward", &json!({})),
            GuardDecision::Allow { .. }
        ));
        g.post_tool("p1", "crew_steward", &json!({}), false, 10);
        // new round so per-round orch counter resets
        g.begin_round("p1", &["file_read".into()]);
        assert!(matches!(
            g.pre_tool("p1", "crew_steward", &json!({})),
            GuardDecision::Allow { .. }
        ));
        g.post_tool("p1", "crew_steward", &json!({}), false, 10);
        g.begin_round("p1", &["file_read".into()]);
        let d = g.pre_tool("p1", "crew_steward", &json!({}));
        assert!(
            matches!(
                d,
                GuardDecision::Block {
                    code: ref c,
                    ..
                } if c == "crew_total_cap"
            ),
            "got {:?}",
            d.to_dict()
        );
    }

    #[test]
    fn orch_window_force() {
        let mut g = LoopGuardSupervisor::default();
        g.configure(
            "p1",
            LoopGuardConfig::for_role(false, RoleKind::Steward, None),
        );
        for _ in 0..3 {
            let d = g.begin_round(
                "p1",
                &["crew_steward".into(), "crew_steward".into()],
            );
            if matches!(d, GuardDecision::ForceFinal { .. }) {
                return;
            }
        }
        // 3 orch rounds should trip
        let d = g.begin_round("p1", &["crew_steward".into()]);
        assert!(
            matches!(
                d,
                GuardDecision::ForceFinal {
                    code: ref c,
                    ..
                } if c == "orch_window_thrash" || c == "max_tool_rounds"
            ),
            "got {:?}",
            d.to_dict()
        );
    }
}
