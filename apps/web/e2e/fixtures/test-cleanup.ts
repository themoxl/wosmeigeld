import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../../..");
const venvPython =
  process.platform === "win32"
    ? join(repoRoot, "services", "api", ".venv", "Scripts", "python.exe")
    : join(repoRoot, "services", "api", ".venv", "bin", "python");

/** Entfernt exakt die Testidentitaet dieses Browsertests samt Testhaushalt. */
export async function cleanupTestIdentity(email: string): Promise<void> {
  const python = process.env.PYTHON ?? (existsSync(venvPython) ? venvPython : "python");
  await execFileAsync(
    python,
    [join(repoRoot, "scripts", "cleanup_test_identity.py"), "--email", email],
    { cwd: repoRoot, env: process.env },
  );
}
