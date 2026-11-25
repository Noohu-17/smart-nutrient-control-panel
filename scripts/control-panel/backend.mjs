// npm run dev at the repo root runs this first: the real back end in Docker (infra/docker-compose.dev.yml)
// for the control panel, then the app talks to it (app/.env.real). DEV ONLY.
//
// Its own compose project and ports, so it never touches another checkout's sna-dev stack:
//   API http://localhost:8010 · simulator control 8110 · PostgreSQL 5442 · MQTT 1893
// client_B (the Coimbatore farm), every value simulated: both zones through the zone controller
// service (profile rig, simulator/sim/rig/README.md) from the stand-in rig (profile fake-rig, rig
// zones 1 and 2), the house climate and power from the simulator (profile sim). For the real rig,
// drop fake-rig and point the rig topics at its gateway (infra/.env, never in git).
// On Windows, Docker runs in WSL (Ubuntu); elsewhere, docker is called directly.
//
// npm run backend:stop stops it (the database is kept); npm run backend:logs shows the api log.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ENV = {
  CLIENT: "client_B",
  API_PORT: "8010",
  SIM_CONTROL_PORT: "8110",
  POSTGRES_PORT: "5442",
  MQTT_PORT: "1893",
};
const API = `http://localhost:${ENV.API_PORT}`;
const WSL = process.platform === "win32";

function compose(args, { input, quiet } = {}) {
  const base = ["compose", "-p", "control-panel", "-f", "infra/docker-compose.dev.yml", "--profile", "sim", "--profile", "rig", "--profile", "fake-rig", ...args];
  const env = Object.entries(ENV).map(([k, v]) => `${k}=${v}`);
  const [cmd, argv] = WSL
    ? ["wsl", ["-d", "Ubuntu", "-u", "root", "--cd", ROOT, "--", "env", ...env, "docker", ...base]]
    : ["docker", base];
  const run = spawnSync(cmd, argv, {
    cwd: ROOT,
    input,
    stdio: [input ? "pipe" : "inherit", quiet ? "pipe" : "inherit", "inherit"],
    env: { ...process.env, ...ENV },
  });
  if (run.status !== 0) {
    console.error(`\nThe back end didn't start (docker ${args.join(" ")}). Is Docker running in WSL?`);
    process.exit(run.status ?? 1);
  }
}

const command = process.argv[2] ?? "up";
if (command === "stop") {
  compose(["stop"]);
  process.exit(0);
}
if (command === "logs") {
  compose(["logs", "--tail", "100", "-f", "api"]);
  process.exit(0);
}

console.log("Starting the back end (first time: a few minutes to build)…");
compose(["up", "-d"]);

process.stdout.write("Waiting for the API");
let healthy = false;
for (let i = 0; i < 90 && !healthy; i++) {
  healthy = await fetch(`${API}/api/health`)
    .then((r) => r.ok)
    .catch(() => false);
  if (!healthy) {
    process.stdout.write(".");
    await new Promise((r) => setTimeout(r, 2000));
  }
}
console.log(healthy ? " up" : " not answering");
if (!healthy) process.exit(1);

console.log("Database: migrations, the farm layout, the sign-in accounts");
compose(["exec", "-T", "api", "alembic", "upgrade", "head"], { quiet: true });
compose(["exec", "-T", "api", "python", "-m", "app.db.seed"]);
compose(["exec", "-T", "api", "python", "-"], {
  input: readFileSync(join(ROOT, "scripts", "control-panel", "users.py")),
});
console.log(`Back end ready on ${API}\n`);
