#!/usr/bin/env node
/**
 * Ensure vendor/takton-kernel-host binary is staged before Electron pack.
 * Does not commit binaries (gitignored). Fails pack if missing unless
 * TAKTON_SKIP_VENDOR_HOST=1.
 */
import { existsSync, readdirSync, statSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const vendorDir = join(root, 'vendor', 'takton-kernel-host');
const names = ['takton-kernel-host.exe', 'takton-kernel-host'];

function findVendorBin() {
  for (const n of names) {
    const p = join(vendorDir, n);
    if (existsSync(p) && statSync(p).isFile()) return p;
  }
  return null;
}

function findTargetBin() {
  for (const prof of ['release', 'debug']) {
    for (const n of names) {
      const p = join(root, 'target', prof, n);
      if (existsSync(p) && statSync(p).isFile()) return p;
    }
  }
  return null;
}

function stageFrom(src) {
  mkdirSync(vendorDir, { recursive: true });
  const destName = src.endsWith('.exe') ? 'takton-kernel-host.exe' : 'takton-kernel-host';
  const dest = join(vendorDir, destName);
  copyFileSync(src, dest);
  const meta = {
    staged_at: new Date().toISOString(),
    source: src,
    dest,
    note: 'Binary is gitignored; rebuild with scripts/build-kernel-host.ps1 -Release',
  };
  writeFileSync(join(vendorDir, 'STAGED.json'), JSON.stringify(meta, null, 2));
  console.log(`OK: staged ${dest}`);
  return dest;
}

const skip = process.env.TAKTON_SKIP_VENDOR_HOST === '1';
let bin = findVendorBin();
if (!bin) {
  const target = findTargetBin();
  if (target) {
    console.log(`vendor host missing; staging from ${target}`);
    bin = stageFrom(target);
  }
}

if (!bin) {
  // try build
  const isWin = process.platform === 'win32';
  const script = isWin
    ? join(root, 'scripts', 'build-kernel-host.ps1')
    : join(root, 'scripts', 'build-kernel-host.sh');
  if (existsSync(script)) {
    console.log('Building kernel host…');
    const r = isWin
      ? spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, '-Release'], {
          cwd: root,
          stdio: 'inherit',
        })
      : spawnSync('bash', [script, '--release'], { cwd: root, stdio: 'inherit' });
    if (r.status === 0) bin = findVendorBin() || findTargetBin();
    if (bin && !findVendorBin()) bin = stageFrom(bin);
  }
}

if (!bin) {
  const msg =
    'takton-kernel-host not found under vendor/ or target/.\n' +
    '  Build: .\\scripts\\build-kernel-host.ps1 -Release\n' +
    '  Or:    cargo build -p takton-kernel-host --release\n' +
    '  Skip:  TAKTON_SKIP_VENDOR_HOST=1 (dev only)';
  if (skip) {
    console.warn('WARN:', msg);
    process.exit(0);
  }
  console.error('FAIL:', msg);
  process.exit(1);
}

console.log('OK: vendor host ready →', bin);
// list vendor dir for pack diagnostics
try {
  console.log(
    'vendor contents:',
    readdirSync(vendorDir).filter((f) => !f.startsWith('.')),
  );
} catch {
  /* ignore */
}
