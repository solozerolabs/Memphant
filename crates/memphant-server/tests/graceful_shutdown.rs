#![cfg(unix)]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

struct ServerProcess(Child);

impl Drop for ServerProcess {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn free_loopback_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind ephemeral loopback port");
    listener.local_addr().expect("read bound address").port()
}

fn health_is_ok(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().expect("valid address"),
        Duration::from_millis(250),
    ) else {
        return false;
    };
    stream
        .set_read_timeout(Some(Duration::from_millis(250)))
        .expect("set HTTP read timeout");
    stream
        .set_write_timeout(Some(Duration::from_millis(250)))
        .expect("set HTTP write timeout");
    if stream
        .write_all(b"GET /v1/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }

    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && response
            .lines()
            .next()
            .is_some_and(|status| status.contains(" 200 "))
}

fn wait_until_healthy(port: u16) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if health_is_ok(port) {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    panic!("memphant-server did not become healthy within 30 seconds");
}

fn assert_graceful_shutdown(signal: &str, expected_signal_name: &str) {
    let port = free_loopback_port();
    let mut server = ServerProcess(
        Command::new(env!("CARGO_BIN_EXE_memphant-server"))
            .env_remove("DATABASE_URL")
            .env_remove("MEMPHANT_APP_DATABASE_URL")
            .env_remove("MEMPHANT_AUTHN_DATABASE_URL")
            .env_remove("MEMPHANT_PROVISION_DATABASE_URL")
            .env("MEMPHANT_EMBEDDINGS", "off")
            .env("MEMPHANT_CROSS_RERANK", "off")
            .env("MEMPHANT_BIND", format!("127.0.0.1:{port}"))
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn memphant-server"),
    );

    wait_until_healthy(port);

    let pid = server.0.id().to_string();
    let kill_status = Command::new("kill")
        .args([signal, &pid])
        .status()
        .expect("run kill command");
    assert!(kill_status.success(), "kill {signal} {pid} failed");

    let deadline = Instant::now() + Duration::from_secs(10);
    let status = loop {
        if let Some(status) = server.0.try_wait().expect("poll memphant-server exit") {
            break status;
        }
        assert!(
            Instant::now() < deadline,
            "memphant-server did not exit within 10 seconds after {signal}"
        );
        thread::sleep(Duration::from_millis(25));
    };

    let mut stderr = String::new();
    server
        .0
        .stderr
        .take()
        .expect("captured server stderr")
        .read_to_string(&mut stderr)
        .expect("read server stderr");

    assert!(status.success(), "status={status:?}, stderr={stderr}");
    assert!(
        stderr.contains(&format!(
            "memphant-server: {expected_signal_name} — draining in-flight requests"
        )),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains("memphant-server: shut down cleanly"),
        "stderr={stderr}"
    );
}

#[test]
fn sigterm_drains_and_exits_cleanly() {
    assert_graceful_shutdown("-TERM", "SIGTERM");
}

#[test]
fn sigint_drains_and_exits_cleanly() {
    assert_graceful_shutdown("-INT", "interrupt");
}
