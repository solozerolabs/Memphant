use std::net::SocketAddr;

use memphant_types::TenantId;
use uuid::Uuid;

#[cfg(unix)]
async fn shutdown_signal() {
    use tokio::signal::unix::{SignalKind, signal};

    let mut sigterm = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    let signal_name = tokio::select! {
        _ = sigterm.recv() => "SIGTERM",
        _ = tokio::signal::ctrl_c() => "interrupt",
    };
    eprintln!("memphant-server: {signal_name} — draining in-flight requests");
}

#[cfg(not(unix))]
async fn shutdown_signal() {
    tokio::signal::ctrl_c()
        .await
        .expect("install interrupt handler");
    eprintln!("memphant-server: interrupt — draining in-flight requests");
}

#[tokio::main]
async fn main() {
    if std::env::args().nth(1).as_deref() == Some("--openapi-json") {
        println!(
            "{}",
            serde_json::to_string_pretty(&memphant_server::openapi_document())
                .expect("OpenAPI serializes")
        );
        return;
    }

    let store = memphant_runtime::build_app_store()
        .await
        .expect("memphant-server: store construction failed");
    let store_name = store.name();
    eprintln!("memphant-server: store={store_name}");
    let service = memphant_runtime::build_service(store);
    let mut state = memphant_server::AppState::from_service(service, store_name);
    if let Ok(dev_tenant) = std::env::var("MEMPHANT_DEV_TENANT") {
        let uuid = Uuid::parse_str(&dev_tenant).expect("MEMPHANT_DEV_TENANT must be a UUID");
        state = state.with_dev_tenant(TenantId::from_u128(uuid.as_u128()));
    }

    let bind = std::env::var("MEMPHANT_BIND").unwrap_or_else(|_| "127.0.0.1:3000".to_string());
    let addr: SocketAddr = bind.parse().expect("valid bind address");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("bind memphant-server");
    axum::serve(listener, memphant_server::app(state))
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("serve memphant-server");
    eprintln!("memphant-server: shut down cleanly");
}
