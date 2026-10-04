#!/usr/bin/env node
// Easy Chain developer commands. They work the same on Windows, macOS and Linux.
//
//   pnpm run setup   (or: node scripts/tasks.mjs install)
//   pnpm dev | build | server | worker | test | e2e | lint | format | schema | golden | docker | clean
//
// `make <task>` runs the same tasks for people who prefer make.

import { spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, readdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const python = join(root, "python");
const isWindows = process.platform === "win32";

/**
 * spawn() arguments for a command. pnpm is a .cmd script on Windows, and Node only starts
 * those through the shell, so there the command goes to cmd.exe as one quoted line.
 */
function command(cmd, args) {
  if (!isWindows) return [cmd, args, {}];
  const quote = (a) => (/[\s"&|<>^]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
  return [[cmd, ...args].map(quote).join(" "), [], { shell: true }];
}

/** Run a command and wait for it; stop everything if it fails. */
function run(cmd, args, { cwd = root, env = {} } = {}) {
  console.log(`\n> ${cmd} ${args.join(" ")}`);
  const [file, argv, extra] = command(cmd, args);
  const result = spawnSync(file, argv, {
    cwd,
    stdio: "inherit",
    env: { ...process.env, ...env },
    ...extra,
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

const uv = (...args) => run("uv", args, { cwd: python });
const uvEnv = (env, ...args) => run("uv", args, { cwd: python, env });
const pnpm = (...args) => run("pnpm", args);

/** Run several long-lived commands together; Ctrl+C (or one of them exiting) stops them all. */
function together(commands) {
  const children = commands.map(({ cmd, args, cwd = root }) => {
    const [file, argv, extra] = command(cmd, args);
    return spawn(file, argv, { cwd, stdio: "inherit", ...extra });
  });
  const stopAll = () => {
    for (const child of children) {
      if (child.exitCode !== null) continue;
      if (isWindows) {
        // child.kill() would only stop the shell; end the whole process tree instead.
        spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      } else {
        child.kill("SIGTERM");
      }
    }
  };
  for (const child of children) {
    child.on("exit", (code) => {
      stopAll();
      process.exitCode = code ?? 0;
    });
  }
  process.on("SIGINT", stopAll);
  process.on("SIGTERM", stopAll);
}

const STATIC = join(python, "src", "easychain", "server", "static");

const tasks = {
  help() {
    console.log(`Easy Chain tasks (pnpm <task>, or node scripts/tasks.mjs <task>):
  setup    install Python and web dependencies   (node scripts/tasks.mjs install)
  dev      the API (port 8000) and the web dev server (http://localhost:5173)
  build    build the web app into the Python package (the one-process app)
  server   build, then run the app on http://127.0.0.1:8000
  worker   run a separate worker (start the server with EASYCHAIN_WORKER=off)
  test     Python tests, then web and client unit tests
  e2e      Playwright end-to-end tests (builds the web app first)
  lint     ruff and TypeScript checks
  format   format and auto-fix the Python code
  schema   regenerate spec/flow.schema.json
  golden   regenerate the compiler golden files (review the diff!)
  docker   docker compose up --build (Postgres + API + worker)
  clean    remove build output and test reports`);
  },
  install() {
    uv("sync", "--extra", "providers");
    pnpm("install");
  },
  dev() {
    console.log("Easy Chain: open http://localhost:5173");
    together([
      { cmd: "uv", args: ["run", "easychain", "dev", "--port", "8000", "--reload"], cwd: python },
      { cmd: "pnpm", args: ["--filter", "@easychain/web", "dev"] },
    ]);
  },
  web() {
    pnpm("--filter", "@easychain/web", "build");
  },
  build() {
    tasks.web();
    for (const name of ["assets", "index.html"]) {
      rmSync(join(STATIC, name), { recursive: true, force: true });
    }
    cpSync(join(root, "apps", "web", "dist"), STATIC, { recursive: true });
    console.log(`Copied the web app into ${STATIC}`);
  },
  server() {
    tasks.build();
    uv("run", "easychain", "dev", "--port", "8000");
  },
  worker() {
    uv("run", "easychain", "worker", "--verbose");
  },
  "test-python"() {
    uv("run", "pytest", "--cov=easychain", "--cov-report=term-missing:skip-covered");
  },
  "test-web"() {
    pnpm("--filter", "@easychain/web", "test");
  },
  "test-client"() {
    pnpm("--filter", "@easychain/client", "test");
  },
  test() {
    tasks["test-python"]();
    tasks["test-web"]();
    tasks["test-client"]();
  },
  e2e() {
    tasks.web();
    pnpm("--filter", "@easychain/web", "e2e");
  },
  lint() {
    uv("run", "ruff", "check", "src", "tests");
    uv("run", "ruff", "format", "--check", "src", "tests");
    pnpm("--filter", "@easychain/web", "typecheck");
    pnpm("--filter", "@easychain/client", "typecheck");
  },
  format() {
    uv("run", "ruff", "format", "src", "tests");
    uv("run", "ruff", "check", "--fix", "src", "tests");
  },
  schema() {
    uv("run", "easychain", "schema", "-o", join("..", "spec", "flow.schema.json"));
  },
  golden() {
    uvEnv({ UPDATE_GOLDEN: "1" }, "run", "pytest", join("tests", "test_compiler_golden.py"));
  },
  docker() {
    run("docker", ["compose", "up", "--build"]);
  },
  clean() {
    const web = join(root, "apps", "web");
    for (const path of [
      join(web, "dist"),
      join(web, "test-results"),
      join(web, "playwright-report"),
      join(python, ".pytest_cache"),
      join(python, ".coverage"),
    ]) {
      rmSync(path, { recursive: true, force: true });
    }
    if (existsSync(STATIC)) {
      for (const name of readdirSync(STATIC)) {
        if (name !== ".gitkeep") rmSync(join(STATIC, name), { recursive: true, force: true });
      }
    }
  },
};
tasks.setup = tasks.install;

const name = process.argv[2] ?? "help";
if (!tasks[name]) {
  console.error(`Unknown task "${name}".`);
  tasks.help();
  process.exit(2);
}
tasks[name]();
