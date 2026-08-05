//! Takton mobile host library — embeddable axum server + API router.
//! Used by the binary and by the Flutter FFI crate.

pub mod routes;
pub mod state;

use anyhow::Context;
use axum::body::Body;
use axum::http::{header, Request, Response};
use axum::Router;
use state::AppState;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::task::{Context as TaskContext, Poll};
use takton_mobile_core::{AppConfig, TaktonClient};
use tower::{Layer, Service};
use tower_http::cors::{Any, CorsLayer};
use tower_http::services::{ServeDir, ServeFile};
use tower_http::trace::TraceLayer;

static HOST_RUNNING: AtomicBool = AtomicBool::new(false);

/// Inject Cache-Control: no-cache on static UI responses so Flutter rebuilds stick.
#[derive(Clone)]
struct NoCacheLayer;

impl<S> Layer<S> for NoCacheLayer {
    type Service = NoCacheService<S>;
    fn layer(&self, inner: S) -> Self::Service {
        NoCacheService { inner }
    }
}

#[derive(Clone)]
struct NoCacheService<S> {
    inner: S,
}

impl<S, ReqBody> Service<Request<ReqBody>> for NoCacheService<S>
where
    S: Service<Request<ReqBody>, Response = Response<Body>> + Clone + Send + 'static,
    S::Future: Send + 'static,
    S::Error: Send + 'static,
    ReqBody: Send + 'static,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<Self::Response, Self::Error>> + Send>,
    >;

    fn poll_ready(&mut self, cx: &mut TaskContext<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request<ReqBody>) -> Self::Future {
        let path = req.uri().path().to_string();
        let mut inner = self.inner.clone();
        Box::pin(async move {
            let mut res = inner.call(req).await?;
            // Only force no-cache on SPA entry assets (not canvaskit blobs)
            let bust = path == "/"
                || path.ends_with("index.html")
                || path.ends_with("flutter_bootstrap.js")
                || path.ends_with("flutter.js")
                || path.ends_with("main.dart.js")
                || path.ends_with("version.json")
                || path.ends_with("flutter_service_worker.js")
                || path.ends_with(".json");
            if bust {
                res.headers_mut().insert(
                    header::CACHE_CONTROL,
                    header::HeaderValue::from_static("no-cache, no-store, must-revalidate"),
                );
            }
            Ok(res)
        })
    }
}

/// Build the full axum app (API + static UI).
pub fn build_app(state: AppState, ui_dir: PathBuf) -> Router {
    let index = ui_dir.join("index.html");
    let static_files = ServeDir::new(&ui_dir)
        .append_index_html_on_directories(true)
        .not_found_service(ServeFile::new(index));

    Router::new()
        .merge(routes::api_router())
        .fallback_service(static_files)
        .layer(NoCacheLayer)
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

/// Start the host on the given bind/port. Returns the actual bound port.
pub async fn start_host(
    config: AppConfig,
    ui_dir: PathBuf,
) -> anyhow::Result<(u16, tokio::task::JoinHandle<()>)> {
    if HOST_RUNNING.swap(true, Ordering::SeqCst) {
        anyhow::bail!("host already running");
    }
    let result = async {
        std::fs::create_dir_all(&config.data_dir).ok();
        let client = TaktonClient::new(config.clone()).context("init client")?;
        let state = AppState::new(client, config.clone()).context("init app state")?;

        {
            let c = state.client.clone();
            tokio::spawn(async move {
                if c.is_authenticated() {
                    return;
                }
                let _ = c.auto_login().await;
            });
        }

        let app = build_app(state, ui_dir);
        let addr = SocketAddr::new(
            config.host_bind.parse().unwrap_or([0, 0, 0, 0].into()),
            config.host_port,
        );
        let listener = tokio::net::TcpListener::bind(addr).await?;
        let port = listener.local_addr()?.port();
        let handle = tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
            HOST_RUNNING.store(false, Ordering::SeqCst);
        });
        Ok((port, handle))
    }
    .await;

    if result.is_err() {
        HOST_RUNNING.store(false, Ordering::SeqCst);
    }
    result
}

pub fn resolve_ui_dir() -> PathBuf {
    if let Ok(p) = std::env::var("TAKTON_MOBILE_UI") {
        return PathBuf::from(p);
    }
    let candidates = [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../flutter_app/build/web"),
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../ui/flutter-web"),
        PathBuf::from("flutter_app/build/web"),
        PathBuf::from("ui/flutter-web"),
        PathBuf::from("/workspace/takton-mobile/flutter_app/build/web"),
        PathBuf::from("/workspace/takton-mobile/ui/flutter-web"),
        // legacy dioxus fallback
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../ui/dioxus"),
        PathBuf::from("/workspace/takton-mobile/ui/dioxus"),
    ];
    for c in candidates {
        if c.join("index.html").exists() {
            return c;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../flutter_app/build/web")
}

/// Shared engine handle for FFI — points at loopback host base.
#[derive(Clone)]
pub struct EngineHandle {
    pub base: Arc<String>, // e.g. http://127.0.0.1:8765
}

impl EngineHandle {
    pub fn new(port: u16) -> Self {
        Self {
            base: Arc::new(format!("http://127.0.0.1:{port}")),
        }
    }

    /// Invoke a mobile API method by path.
    pub async fn invoke(&self, method: &str, path: &str, body: Option<&str>) -> anyhow::Result<String> {
        let url = format!("{}{}", self.base, path);
        let client = reqwest::Client::new();
        let resp = match method.to_ascii_uppercase().as_str() {
            "GET" => client.get(&url).send().await?,
            "POST" => {
                let mut r = client.post(&url).header("Content-Type", "application/json");
                if let Some(b) = body {
                    r = r.body(b.to_string());
                } else {
                    r = r.body("{}");
                }
                r.send().await?
            }
            other => anyhow::bail!("unsupported method {other}"),
        };
        let text = resp.text().await?;
        Ok(text)
    }
}
