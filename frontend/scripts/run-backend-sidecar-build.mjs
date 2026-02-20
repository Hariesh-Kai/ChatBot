import { spawnSync } from "node:child_process";
import path from "node:path";

const cwd = process.cwd();
const repoRoot = path.resolve(cwd, "..");

const pythonCandidates = [];

if (process.platform === "win32") {
  pythonCandidates.push(path.join(repoRoot, "venv", "Scripts", "python.exe"));
}
pythonCandidates.push("python");

const args = ["scripts/build-backend-sidecar.py", ...process.argv.slice(2)];

let selectedPython = null;
for (const candidate of pythonCandidates) {
  const probe = spawnSync(candidate, ["--version"], { stdio: "ignore", cwd });
  if (probe.status === 0) {
    selectedPython = candidate;
    break;
  }
}

if (!selectedPython) {
  console.error(
    "Python was not found. Install Python or create venv at ../venv before building sidecar."
  );
  process.exit(1);
}

const run = spawnSync(selectedPython, args, {
  stdio: "inherit",
  cwd,
  env: process.env,
});

process.exit(run.status ?? 1);
