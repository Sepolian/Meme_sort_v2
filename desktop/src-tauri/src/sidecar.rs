use std::{
    env,
    error::Error,
    fmt,
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};

use serde::Deserialize;
use tauri::{AppHandle, Manager};

#[cfg(debug_assertions)]
use std::path::Path;

const PROTOCOL_VERSION: u32 = 1;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(15);
#[cfg(not(debug_assertions))]
const SIDECAR_BINARY_NAME: &str = "memesort-sidecar-x86_64-pc-windows-msvc.exe";

#[derive(Debug)]
pub struct SidecarError(String);

impl SidecarError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for SidecarError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl Error for SidecarError {}

#[derive(Debug, Deserialize)]
struct Handshake {
    protocol_version: u32,
    origin: String,
    bootstrap_url: String,
    library_root: String,
}

pub struct SidecarSession {
    child: Child,
    stdin: ChildStdin,
    #[allow(dead_code)]
    origin: String,
    #[allow(dead_code)]
    session_cookie: String,
}

pub struct SidecarState(Mutex<Option<SidecarSession>>);

impl SidecarState {
    pub fn new(session: SidecarSession) -> Self {
        Self(Mutex::new(Some(session)))
    }
}

impl SidecarSession {
    pub fn start(app: &AppHandle) -> Result<Self, SidecarError> {
        let launch = SidecarLaunch::for_current_build(app)?;
        let mut child = launch.spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| SidecarError::new("Sidecar stdin was not available."))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| SidecarError::new("Sidecar stdout was not available."))?;

        // Sidecar stdout is a single protocol message, never a log stream.
        let (sender, receiver) = mpsc::sync_channel(1);
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout).read_line(&mut line).map(|_| line);
            let _ = sender.send(result);
        });

        if let Some(stderr) = child.stderr.take() {
            // Keep a noisy or failing sidecar from blocking on its stderr pipe.
            // It intentionally does not forward diagnostic text to the WebView.
            thread::spawn(move || {
                let _ = std::io::copy(&mut BufReader::new(stderr), &mut std::io::sink());
            });
        }

        let line = match receiver.recv_timeout(HANDSHAKE_TIMEOUT) {
            Ok(Ok(line)) => line,
            Ok(Err(error)) => {
                terminate_child(&mut child);
                return Err(SidecarError::new(format!(
                    "Could not read the sidecar handshake: {error}"
                )));
            }
            Err(_) => {
                terminate_child(&mut child);
                return Err(SidecarError::new(
                    "Timed out waiting for the sidecar handshake.",
                ));
            }
        };

        let handshake = match parse_handshake(&line) {
            Ok(handshake) => handshake,
            Err(error) => {
                terminate_child(&mut child);
                return Err(error);
            }
        };
        let session_cookie = match consume_bootstrap(&handshake) {
            Ok(cookie) => cookie,
            Err(error) => {
                terminate_child(&mut child);
                return Err(error);
            }
        };

        Ok(Self {
            child,
            stdin,
            origin: handshake.origin,
            session_cookie,
        })
    }

    pub fn shutdown(&mut self) -> Result<(), SidecarError> {
        self.stdin
            .write_all(b"{\"command\":\"shutdown\"}\n")
            .and_then(|_| self.stdin.flush())
            .map_err(|error| SidecarError::new(format!("Could not stop sidecar: {error}")))?;

        let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
        while Instant::now() < deadline {
            if self
                .child
                .try_wait()
                .map_err(|error| SidecarError::new(format!("Could not wait for sidecar: {error}")))?
                .is_some()
            {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(50));
        }

        terminate_child(&mut self.child);
        Err(SidecarError::new(
            "Sidecar did not stop before the shutdown deadline.",
        ))
    }
}

pub fn shutdown_managed_sidecar(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    let Ok(mut session) = state.0.lock() else {
        return;
    };
    let Some(mut session) = session.take() else {
        return;
    };
    if let Err(error) = session.shutdown() {
        eprintln!("MemeSort sidecar shutdown failed: {error}");
    }
}

struct SidecarLaunch {
    executable: PathBuf,
    arguments: Vec<String>,
    working_directory: PathBuf,
}

impl SidecarLaunch {
    fn for_current_build(_app: &AppHandle) -> Result<Self, SidecarError> {
        let library_root = default_library_root()?;
        let log_dir = library_root.join("logs");

        #[cfg(debug_assertions)]
        {
            let repository_root = repository_root()?;
            let python = env::var_os("MEMESORT_SIDECAR_PYTHON")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    repository_root
                        .join(".venv")
                        .join("Scripts")
                        .join("python.exe")
                });
            if !python.is_file() {
                return Err(SidecarError::new(format!(
                    "MemeSort sidecar Python is missing at {}. Run uv sync first.",
                    python.display()
                )));
            }
            Ok(Self {
                executable: python,
                arguments: vec![
                    "-m".into(),
                    "memesort_worker.sidecar_entry".into(),
                    "--library-root".into(),
                    library_root.display().to_string(),
                    "--log-dir".into(),
                    log_dir.display().to_string(),
                ],
                working_directory: repository_root,
            })
        }

        #[cfg(not(debug_assertions))]
        {
            let resource_dir = _app.path().resource_dir().map_err(|error| {
                SidecarError::new(format!(
                    "Could not resolve the Tauri resource directory: {error}"
                ))
            })?;
            let executable = resource_dir.join(SIDECAR_BINARY_NAME);
            if !executable.is_file() {
                return Err(SidecarError::new(format!(
                    "MemeSort sidecar is missing at {}.",
                    executable.display()
                )));
            }
            Ok(Self {
                executable,
                arguments: vec![
                    "--library-root".into(),
                    library_root.display().to_string(),
                    "--log-dir".into(),
                    log_dir.display().to_string(),
                ],
                working_directory: resource_dir,
            })
        }
    }

    fn spawn(&self) -> Result<Child, SidecarError> {
        Command::new(&self.executable)
            .args(&self.arguments)
            .current_dir(&self.working_directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| SidecarError::new(format!("Could not start the sidecar: {error}")))
    }
}

#[cfg(debug_assertions)]
fn repository_root() -> Result<PathBuf, SidecarError> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .ok_or_else(|| SidecarError::new("Could not resolve the MemeSort repository root."))
}

fn default_library_root() -> Result<PathBuf, SidecarError> {
    if let Some(root) = env::var_os("MEMESORT_LIBRARY_ROOT") {
        return Ok(PathBuf::from(root));
    }
    let appdata = env::var_os("APPDATA").ok_or_else(|| {
        SidecarError::new("APPDATA is required to resolve the MemeSort Library Root.")
    })?;
    Ok(PathBuf::from(appdata).join("MemeSort"))
}

fn parse_handshake(line: &str) -> Result<Handshake, SidecarError> {
    let handshake: Handshake = serde_json::from_str(line)
        .map_err(|error| SidecarError::new(format!("Invalid sidecar handshake: {error}")))?;
    if handshake.protocol_version != PROTOCOL_VERSION {
        return Err(SidecarError::new(format!(
            "Unsupported sidecar protocol version {}.",
            handshake.protocol_version
        )));
    }
    let origin_host = handshake
        .origin
        .strip_prefix("http://127.0.0.1:")
        .ok_or_else(|| SidecarError::new("Sidecar origin must be a 127.0.0.1 HTTP endpoint."))?;
    if origin_host.parse::<u16>().is_err()
        || !handshake.bootstrap_url.starts_with(&handshake.origin)
    {
        return Err(SidecarError::new(
            "Invalid sidecar origin or bootstrap URL.",
        ));
    }
    if !handshake.bootstrap_url.contains("?bootstrap=") || handshake.library_root.is_empty() {
        return Err(SidecarError::new("Incomplete sidecar handshake."));
    }
    Ok(handshake)
}

fn consume_bootstrap(handshake: &Handshake) -> Result<String, SidecarError> {
    let port = handshake
        .origin
        .strip_prefix("http://127.0.0.1:")
        .and_then(|port| port.parse::<u16>().ok())
        .ok_or_else(|| SidecarError::new("Invalid sidecar origin."))?;
    let path = handshake
        .bootstrap_url
        .strip_prefix(&handshake.origin)
        .filter(|path| path.starts_with('/'))
        .ok_or_else(|| SidecarError::new("Bootstrap URL does not belong to the sidecar origin."))?;

    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|error| SidecarError::new(format!("Could not connect to sidecar: {error}")))?;
    stream
        .write_all(
            format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .map_err(|error| SidecarError::new(format!("Could not bootstrap sidecar: {error}")))?;

    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|error| {
        SidecarError::new(format!("Could not read bootstrap response: {error}"))
    })?;
    let (headers, _) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| SidecarError::new("Malformed sidecar bootstrap response."))?;
    let mut lines = headers.lines();
    let status = lines
        .next()
        .ok_or_else(|| SidecarError::new("Missing sidecar bootstrap status."))?;
    if !status.contains(" 303 ") {
        return Err(SidecarError::new("Sidecar bootstrap was not accepted."));
    }
    lines
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            if !name.eq_ignore_ascii_case("set-cookie") {
                return None;
            }
            value.trim().split(';').next().map(str::to_owned)
        })
        .filter(|cookie| cookie.starts_with("memesort_session="))
        .ok_or_else(|| SidecarError::new("Sidecar bootstrap did not return a session cookie."))
}

fn terminate_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::parse_handshake;

    #[test]
    fn accepts_the_versioned_loopback_handshake() {
        let handshake = parse_handshake(
            r#"{"protocol_version":1,"origin":"http://127.0.0.1:43123","bootstrap_url":"http://127.0.0.1:43123/?bootstrap=secret","library_root":"C:\\Library"}"#,
        )
        .expect("handshake should be accepted");

        assert_eq!(handshake.protocol_version, 1);
    }

    #[test]
    fn rejects_a_non_loopback_handshake() {
        let error = parse_handshake(
            r#"{"protocol_version":1,"origin":"http://example.test:43123","bootstrap_url":"http://example.test:43123/?bootstrap=secret","library_root":"C:\\Library"}"#,
        )
        .expect_err("foreign origin must be rejected");

        assert!(error.to_string().contains("127.0.0.1"));
    }
}
