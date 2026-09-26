#![cfg(unix)]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

fn free_loopback_addr() -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind an ephemeral loopback port");
    let addr = listener.local_addr().expect("read ephemeral port");
    drop(listener);
    addr
}

fn health_is_ready(addr: SocketAddr) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(250)) else {
        return false;
    };
    stream
        .set_read_timeout(Some(Duration::from_millis(250)))
        .expect("set probe read timeout");
    if stream
        .write_all(b"GET /v1/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }

    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

#[test]
fn sigterm_triggers_graceful_shutdown_and_successful_exit() {
    let addr = free_loopback_addr();
    let child = Command::new(env!("CARGO_BIN_EXE_memphant-server"))
        .env_remove("DATABASE_URL")
        .env_remove("MEMPHANT_APP_DATABASE_URL")
        .env_remove("MEMPHANT_AUTHN_DATABASE_URL")
        .env_remove("MEMPHANT_PROVISION_DATABASE_URL")
        .env("MEMPHANT_EMBEDDINGS", "off")
        .env("MEMPHANT_BIND", addr.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn memphant-server");
    let mut child = ChildGuard(child);

    let ready_deadline = Instant::now() + Duration::from_secs(30);
    while !health_is_ready(addr) {
        assert!(
            Instant::now() < ready_deadline,
            "memphant-server did not become healthy within 30 seconds"
        );
        assert!(
            child.0.try_wait().expect("poll server startup").is_none(),
            "memphant-server exited before becoming healthy"
        );
        thread::sleep(Duration::from_millis(50));
    }

    let pid = child.0.id().to_string();
    let kill_status = Command::new("kill")
        .args(["-TERM", pid.as_str()])
        .status()
        .expect("send SIGTERM");
    assert!(kill_status.success(), "kill -TERM failed: {kill_status}");

    let exit_deadline = Instant::now() + Duration::from_secs(10);
    let status = loop {
        if let Some(status) = child.0.try_wait().expect("poll server shutdown") {
            break status;
        }
        assert!(
            Instant::now() < exit_deadline,
            "memphant-server did not exit within 10 seconds after SIGTERM"
        );
        thread::sleep(Duration::from_millis(25));
    };

    let mut stderr = String::new();
    child
        .0
        .stderr
        .take()
        .expect("capture server stderr")
        .read_to_string(&mut stderr)
        .expect("read server stderr");

    assert!(status.success(), "server status={status}; stderr={stderr}");
    assert!(
        stderr.contains("draining in-flight requests"),
        "stderr={stderr}"
    );
    assert!(stderr.contains("shut down cleanly"), "stderr={stderr}");
}
