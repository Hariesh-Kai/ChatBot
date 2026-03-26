#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
  fs,
  io::{BufRead, BufReader, Write},
  net::{SocketAddr, TcpStream},
  path::{Path, PathBuf},
  process::{Child, Command, Stdio},
  sync::Mutex,
  time::Duration,
};

use tauri::{AppHandle, Manager, RunEvent};

struct BackendState {
  child: Mutex<Option<Child>>,
  log_path: Mutex<Option<PathBuf>>,
}

fn backend_listening() -> bool {
  let addr: SocketAddr = match "127.0.0.1:8000".parse() {
    Ok(addr) => addr,
    Err(_) => return false,
  };
  TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

fn append_log(log_path: &Path, message: &str) {
  if let Some(parent) = log_path.parent() {
    let _ = fs::create_dir_all(parent);
  }

  if let Ok(mut file) = fs::OpenOptions::new()
    .create(true)
    .append(true)
    .open(log_path)
  {
    let _ = writeln!(file, "{message}");
  }
}

fn default_log_path(app: &AppHandle) -> PathBuf {
  if let Ok(dir) = app.path().app_local_data_dir() {
    return dir.join("desktop.log");
  }
  std::env::temp_dir().join("chat-ui-base-desktop.log")
}

fn find_env_file(app: &AppHandle) -> Option<PathBuf> {
  let mut candidates = Vec::new();

  if let Ok(explicit) = std::env::var("CHAT_UI_ENV_FILE") {
    candidates.push(PathBuf::from(explicit));
  }

  if let Ok(dir) = app.path().app_local_data_dir() {
    candidates.push(dir.join(".env"));
  }

  if let Ok(dir) = app.path().resource_dir() {
    candidates.push(dir.join(".env"));
    candidates.push(dir.join("resources").join(".env"));
    candidates.push(dir.join("_up_").join(".env"));
    candidates.push(dir.join("_up_").join("_up_").join(".env"));
  }

  if let Ok(cwd) = std::env::current_dir() {
    candidates.push(cwd.join(".env"));
    candidates.push(cwd.join("..").join(".env"));
    candidates.push(cwd.join("..").join("..").join(".env"));
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(exe_dir) = exe.parent() {
      candidates.push(exe_dir.join(".env"));
      candidates.push(exe_dir.join("resources").join(".env"));
      candidates.push(exe_dir.join("_up_").join(".env"));
      candidates.push(exe_dir.join("_up_").join("_up_").join(".env"));
      candidates.push(exe_dir.join("..").join(".env"));
    }
  }

  candidates.into_iter().find(|p| p.is_file())
}

fn sidecar_names() -> Vec<&'static str> {
  #[cfg(target_os = "windows")]
  {
    return vec![
      "chat-ui-backend-x86_64-pc-windows-msvc.exe",
      "chat-ui-backend-aarch64-pc-windows-msvc.exe",
      "chat-ui-backend-i686-pc-windows-msvc.exe",
      "chat-ui-backend.exe",
    ];
  }

  #[cfg(not(target_os = "windows"))]
  {
    vec![
      "chat-ui-backend-x86_64-unknown-linux-gnu",
      "chat-ui-backend-aarch64-unknown-linux-gnu",
      "chat-ui-backend-x86_64-apple-darwin",
      "chat-ui-backend-aarch64-apple-darwin",
      "chat-ui-backend",
    ]
  }
}

fn find_sidecar_path(app: &AppHandle) -> Option<PathBuf> {
  let names = sidecar_names();

  let mut roots: Vec<PathBuf> = Vec::new();

  if let Ok(dir) = app.path().resource_dir() {
    roots.push(dir.clone());
    roots.push(dir.join("binaries"));
  }

  if let Ok(exe) = std::env::current_exe() {
    if let Some(exe_dir) = exe.parent() {
      roots.push(exe_dir.to_path_buf());
      roots.push(exe_dir.join("binaries"));
      roots.push(exe_dir.join("_up_"));
      roots.push(exe_dir.join("_up_").join("binaries"));
    }
  }

  if let Ok(cwd) = std::env::current_dir() {
    roots.push(cwd.clone());
    roots.push(cwd.join("binaries"));
    roots.push(cwd.join("src-tauri").join("binaries"));
    roots.push(cwd.join("..").join("src-tauri").join("binaries"));
  }

  for root in roots {
    for name in &names {
      let candidate = root.join(name);
      if candidate.is_file() {
        return Some(candidate);
      }
    }
  }

  None
}

fn stream_to_log<R: std::io::Read + Send + 'static>(reader: R, log_path: PathBuf, prefix: &'static str) {
  std::thread::spawn(move || {
    let buf = BufReader::new(reader);
    for line in buf.lines().map_while(Result::ok) {
      append_log(&log_path, &format!("{prefix}{line}"));
    }
  });
}

fn kill_backend(app: &AppHandle) {
  let state = app.state::<BackendState>();
  if let Some(mut child) = state.child.lock().ok().and_then(|mut guard| guard.take()) {
    let _ = child.kill();
  }
}

fn launch_backend(app: &AppHandle) {
  let log_path = default_log_path(app);
  append_log(&log_path, "[desktop] launching backend sidecar");

  if backend_listening() {
    append_log(&log_path, "[chat-ui-backend] backend already listening on 127.0.0.1:8000");
    return;
  }

  let sidecar_path = match find_sidecar_path(app) {
    Some(path) => path,
    None => {
      append_log(
        &log_path,
        "[chat-ui-backend] sidecar binary not found in resource/app directories",
      );
      return;
    }
  };

  let temp_base = app
    .path()
    .app_local_data_dir()
    .unwrap_or_else(|_| std::env::temp_dir());
  let temp_dir = temp_base.join("sidecar-temp");
  let _ = fs::create_dir_all(&temp_dir);

  let mut command = Command::new(&sidecar_path);
  command
    .args(["--host", "127.0.0.1", "--port", "8000"])
    .env("TMP", &temp_dir)
    .env("TEMP", &temp_dir)
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());

  if let Some(env_file) = find_env_file(app) {
    if let Some(parent) = env_file.parent() {
      command.current_dir(parent);
    }
    command.env("CHAT_UI_ENV_FILE", &env_file);
    append_log(
      &log_path,
      &format!("[chat-ui-backend] using env file {}", env_file.to_string_lossy()),
    );
  } else {
    append_log(
      &log_path,
      "[chat-ui-backend] no .env file found; set CHAT_UI_ENV_FILE or place .env near app/resources",
    );
  }

  match command.spawn() {
    Ok(mut child) => {
      append_log(
        &log_path,
        &format!(
          "[chat-ui-backend] started sidecar pid {} ({})",
          child.id(),
          sidecar_path.to_string_lossy()
        ),
      );

      if let Some(stdout) = child.stdout.take() {
        stream_to_log(stdout, log_path.clone(), "[chat-ui-backend][stdout] ");
      }
      if let Some(stderr) = child.stderr.take() {
        stream_to_log(stderr, log_path.clone(), "[chat-ui-backend][stderr] ");
      }

      {
        let state = app.state::<BackendState>();
        if let Ok(mut child_slot) = state.child.lock() {
          *child_slot = Some(child);
        };
      }
      {
        let state = app.state::<BackendState>();
        if let Ok(mut log_slot) = state.log_path.lock() {
          *log_slot = Some(log_path);
        };
      }
    }
    Err(err) => {
      append_log(
        &log_path,
        &format!("[chat-ui-backend] failed to spawn sidecar: {err}"),
      );
    }
  }
}

fn main() {
  tauri::Builder::default()
    .manage(BackendState {
      child: Mutex::new(None),
      log_path: Mutex::new(None),
    })
    .setup(|app| {
      #[cfg(desktop)]
      {
        launch_backend(&app.handle());
      }
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app, event| match event {
      RunEvent::ExitRequested { .. } | RunEvent::Exit => {
        kill_backend(app);
      }
      _ => {}
    });
}
