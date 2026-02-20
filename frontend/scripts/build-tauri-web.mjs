import { spawnSync } from "node:child_process";
import path from "node:path";

const projectRoot = process.cwd();
const env = { ...process.env, TAURI_BUILD: "1" };

const nextBin = path.join(projectRoot, "node_modules", "next", "dist", "bin", "next");
const build = spawnSync(process.execPath, [nextBin, "build", "--webpack"], {
  cwd: projectRoot,
  env,
  stdio: "inherit",
  shell: false,
});

if ((build.status ?? 1) !== 0) {
  process.exit(build.status ?? 1);
}
