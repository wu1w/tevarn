//! Tevarn Runtime layer (Rust).
//!
//! Owns the kernel singleton, resource policy defaults, and a service
//! registry for Python-side adapters (identity / inbox / dispatcher).
//!
//! Business adapters that need SQLAlchemy remain in Python and register
//! themselves via the JSON-RPC host; the runtime only holds handles.

use std::collections::HashMap;
use std::sync::Arc;

use parking_lot::RwLock;
use serde_json::{json, Value};
use tevarn_kernel::{
    global, init_global, AgentKernel, KernelConfig, SoftRenewConfig, VERSION as KERNEL_VERSION,
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Runtime configuration (env / host CLI).
#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    pub kernel: KernelConfig,
    pub profile: String,
    pub single_user: bool,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            kernel: KernelConfig {
                audit_persist: true,
                soft_renew: SoftRenewConfig::default(),
                ..Default::default()
            },
            profile: std::env::var("TEVARN_AIOS_PROFILE").unwrap_or_else(|_| "aios-dev".into()),
            single_user: std::env::var("TEVARN_SINGLE_USER_MODE")
                .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                .unwrap_or(true),
        }
    }
}

/// Opaque service handles registered by Python adapters.
#[derive(Default)]
struct ServiceRegistry {
    /// name -> JSON metadata (connection info, status)
    services: HashMap<String, Value>,
}

pub struct Runtime {
    kernel: Arc<AgentKernel>,
    config: RuntimeConfig,
    services: RwLock<ServiceRegistry>,
}

impl Runtime {
    pub fn bootstrap(config: RuntimeConfig) -> Arc<Self> {
        let kernel = init_global(config.kernel.clone());
        Arc::new(Self {
            kernel,
            config,
            services: RwLock::new(ServiceRegistry::default()),
        })
    }

    pub fn kernel(&self) -> Arc<AgentKernel> {
        self.kernel.clone()
    }

    pub fn profile(&self) -> &str {
        &self.config.profile
    }

    pub fn register_service(&self, name: &str, meta: Value) {
        self.services
            .write()
            .services
            .insert(name.to_string(), meta);
    }

    pub fn get_service(&self, name: &str) -> Option<Value> {
        self.services.read().services.get(name).cloned()
    }

    pub fn list_services(&self) -> Value {
        let g = self.services.read();
        json!(g.services)
    }

    pub fn health(&self) -> Value {
        let procs = self.kernel.list_processes(false);
        let (chain_ok, _) = self.kernel.verify_event_chain();
        json!({
            "ok": true,
            "runtime_version": VERSION,
            "kernel_version": KERNEL_VERSION,
            "profile": self.config.profile,
            "single_user": self.config.single_user,
            "live_processes": procs.len(),
            "audit_chain_ok": chain_ok,
            "services": self.list_services(),
        })
    }

    /// Apply resource defaults from a settings-like JSON object.
    pub fn apply_resource_policy(&self, policy: &Value) {
        // Future: tune ResourceManager defaults via kernel API.
        let _ = policy;
    }
}

static RUNTIME: once_cell::sync::OnceCell<Arc<Runtime>> = once_cell::sync::OnceCell::new();

pub fn init_runtime(config: RuntimeConfig) -> Arc<Runtime> {
    let rt = Runtime::bootstrap(config);
    let _ = RUNTIME.set(rt.clone());
    rt
}

pub fn runtime() -> Arc<Runtime> {
    RUNTIME
        .get()
        .cloned()
        .unwrap_or_else(|| init_runtime(RuntimeConfig::default()))
}

pub fn get_kernel() -> Arc<AgentKernel> {
    runtime().kernel()
}

/// Ensure kernel global exists even without full runtime (tests / embed).
pub fn ensure_kernel() -> Arc<AgentKernel> {
    global()
}
