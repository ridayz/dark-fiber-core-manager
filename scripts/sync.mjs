#!/usr/bin/env node
// Satu perintah: commit semua perubahan lalu push ke GitHub.
//
// Pemakaian:
//   node scripts/sync.mjs                  # pesan commit otomatis
//   node scripts/sync.mjs -m "feat: ..."   # pesan commit sendiri
//   node scripts/sync.mjs --dry-run        # lihat yang akan masuk, tanpa commit
//
// Aman dipakai: menolak file rahasia (.env, *.pem, *.key) dan file > 20 MB.

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MAX_FILE_MB = 20;
const SECRETS = [
  /(^|\/)\.env($|\.)/,
  /\.pem$/i,
  /\.key$/i,
  /\.pfx$/i,
  /\.p12$/i,
  /(^|\/)id_rsa/i,
];

const argv = process.argv.slice(2);
const dryRun = argv.includes("--dry-run") || argv.includes("-n");
const msgIndex = argv.findIndex((a) => a === "-m" || a === "--message");
const customMessage = msgIndex >= 0 ? (argv[msgIndex + 1] || "").trim() : "";

function git(args, env) {
  return execFileSync(
    "git",
    ["-c", `safe.directory=${ROOT.replace(/\\/g, "/")}`, "-C", ROOT, ...args],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env } },
  );
}

function tryGit(args, env) {
  try {
    return { ok: true, out: git(args, env) };
  } catch (err) {
    return { ok: false, out: `${err.stdout || ""}${err.stderr || ""}`.trim() };
  }
}

function fail(text) {
  console.error(`\n  GAGAL: ${text}\n`);
  process.exit(1);
}

const branch = tryGit(["rev-parse", "--abbrev-ref", "HEAD"]);
if (!branch.ok) fail(`folder ini bukan repo git.\n  ${branch.out}`);
const current = branch.out.trim();
if (current === "HEAD") fail("HEAD lepas (detached). Pindah ke branch dulu: git switch main");

if (!git(["status", "--porcelain"]).trim()) {
  console.log(`\n  Tidak ada perubahan. ${current} sudah sama dengan lokal.\n`);
  process.exit(0);
}

// Dry-run memakai index sementara supaya staging asli tidak tersentuh.
let tempIndex = null;
let env;
if (dryRun) {
  const dir = mkdtempSync(path.join(os.tmpdir(), "devteam-index-"));
  tempIndex = { dir, file: path.join(dir, "index") };
  env = { GIT_INDEX_FILE: tempIndex.file };
  git(["read-tree", "HEAD"], env);
}

git(["add", "-A"], env);
const staged = git(["diff", "--cached", "--name-only", "-z"], env)
  .split("\0")
  .filter(Boolean);

function cleanup() {
  if (tempIndex) rmSync(tempIndex.dir, { recursive: true, force: true });
}

if (staged.length === 0) {
  cleanup();
  console.log("\n  Tidak ada file yang perlu di-commit.\n");
  process.exit(0);
}

// --- Pemeriksaan keamanan -------------------------------------------------
const secrets = staged.filter((f) => SECRETS.some((re) => re.test(f)));
if (secrets.length) {
  cleanup();
  fail(`file rahasia ikut ter-stage:\n  ${secrets.join("\n  ")}\n  Tambahkan ke .gitignore, lalu ulangi.`);
}

const tooBig = [];
for (const file of staged) {
  try {
    const size = statSync(path.join(ROOT, file)).size;
    if (size > MAX_FILE_MB * 1024 * 1024) {
      tooBig.push(`${file} (${(size / 1024 / 1024).toFixed(1)} MB)`);
    }
  } catch {
    // file terhapus, tidak masalah
  }
}
if (tooBig.length) {
  cleanup();
  fail(`file terlalu besar untuk GitHub:\n  ${tooBig.join("\n  ")}\n  Simpan di luar repo atau pakai Git LFS.`);
}

// --- Ringkasan ------------------------------------------------------------
console.log(`\n  Branch : ${current}`);
console.log(`  File   : ${staged.length} berubah`);
for (const file of staged.slice(0, 25)) console.log(`    - ${file}`);
if (staged.length > 25) console.log(`    ... dan ${staged.length - 25} file lain`);

if (dryRun) {
  cleanup();
  console.log("\n  Mode dry-run: tidak ada yang di-commit atau di-push.\n");
  process.exit(0);
}

// --- Commit + push --------------------------------------------------------
const stamp = new Date()
  .toISOString()
  .slice(0, 16)
  .replace("T", " ");
const message = customMessage || `chore(sync): ${stamp}`;

const commit = tryGit(["commit", "-q", "-m", message]);
if (!commit.ok) fail(`commit gagal.\n  ${commit.out}`);
console.log(`\n  Commit : ${git(["rev-parse", "--short", "HEAD"]).trim()} — ${message}`);

const hasRemoteBranch = tryGit(["ls-remote", "--exit-code", "--heads", "origin", current]).ok;
if (hasRemoteBranch) {
  const pull = tryGit(["pull", "--rebase", "--autostash", "-q", "origin", current]);
  if (!pull.ok) fail(`pull --rebase gagal, selesaikan konflik dulu.\n  ${pull.out}`);
}

const push = tryGit(["push", "-q", "origin", current]);
if (!push.ok) fail(`push gagal (cek login GitHub / koneksi).\n  ${push.out}`);

const remote = tryGit(["remote", "get-url", "origin"]).out.trim().replace(/\.git$/, "");
console.log(`  Push   : ${current} → ${remote}\n  Selesai. Semua perubahan sudah di GitHub.\n`);
