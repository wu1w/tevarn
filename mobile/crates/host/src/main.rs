//! Takton Mobile Host binary — Flutter shell UI + Rust API bridge.

use takton_mobile_core::AppConfig;
use takton_mobile_host::{build_app, resolve_ui_dir, state::AppState};
use takton_mobile_core::TaktonClient;
use std::net::SocketAddr;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let config = AppConfig::default();
    let ui_dir = resolve_ui_dir();
    tracing::info!(?ui_dir, base = %config.base_url, "takton-mobile starting");

    if !ui_dir.join("index.html").exists() {
        anyhow::bail!(
            "UI index.html missing under {:?}; build Flutter web first (flutter build web) or set TAKTON_MOBILE_UI",
            ui_dir
        );
    }

    std::fs::create_dir_all(&config.data_dir).ok();
    let client = TaktonClient::new(config.clone())?;
    let state = AppState::new(client, config.clone());

    {
        let c = state.client.clone();
        tokio::spawn(async move {
            if c.is_authenticated() {
                return;
            }
            match c.auto_login().await {
                Ok(s) => tracing::info!(user = %s.user.email, "auto-login ok"),
                Err(e) => tracing::info!("auto-login skipped: {e}"),
            }
        });
    }

    let app = build_app(state, ui_dir);
    let addr = SocketAddr::new(
        config.host_bind.parse().unwrap_or([0, 0, 0, 0].into()),
        config.host_port,
    );
    tracing::info!("listening on http://{addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
