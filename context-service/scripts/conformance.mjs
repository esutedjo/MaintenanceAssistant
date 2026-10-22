// Runs the official Meterless conformance suites against the engines this service loads.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const posix = (p) => p.split(path.sep).join("/");
const suites = [
  { name: "H-MEM", runner: "vendor/conformance/hmem/runner.ts", env: "HMEM_IMPL", impl: "vendor/hmem/src/index.ts" },
  { name: "World Model", runner: "vendor/conformance/world-model/runner.ts", env: "WORLD_MODEL_IMPL", impl: "vendor/world-model/src/index.ts" },
];

let failed = false;
for (const s of suites) {
  console.log(`\n=== ${s.name} conformance (${s.impl})`);
  const result = spawnSync("npx", ["tsx", s.runner], {
    cwd: root,
    stdio: "inherit",
    shell: process.platform === "win32",
    env: { ...process.env, [s.env]: posix(path.join(root, s.impl)) },
  });
  if (result.status !== 0) failed = true;
}
process.exit(failed ? 1 : 0);
