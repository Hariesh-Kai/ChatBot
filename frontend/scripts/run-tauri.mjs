import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

function detectCargo() {
  const candidates = ["cargo"];

  if (process.platform === "win32" && process.env.USERPROFILE) {
    candidates.push(path.join(process.env.USERPROFILE, ".cargo", "bin", "cargo.exe"));
  }

  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["--version"], { stdio: "ignore" });
    if (probe.status === 0) {
      return candidate;
    }
  }

  return null;
}

const cargo = detectCargo();
if (!cargo) {
  console.error(
    "Rust cargo was not found. Install Rust from https://rustup.rs/ or add cargo to PATH."
  );
  process.exit(1);
}

const tauriArgs = process.argv.slice(2);
const env = { ...process.env };
if (!env.CARGO_TARGET_DIR) {
  // Keep Rust build artifacts on project drive to avoid filling C:\\Temp.
  env.CARGO_TARGET_DIR = path.resolve(process.cwd(), ".cargo-target", "chat-ui-base-tauri-target");
}
fs.mkdirSync(env.CARGO_TARGET_DIR, { recursive: true });

const child = spawn(cargo, ["tauri", ...tauriArgs], {
  stdio: "inherit",
  shell: false,
  env,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
