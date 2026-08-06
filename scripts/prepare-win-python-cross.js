/**
 * Cross-prepare Windows embeddable Python + prod wheels from Linux/macOS.
 * Output: <repo>/win-python/ ready for electron-builder extraResources → python/
 *
 * Why: scripts/prepare-win-python.js only runs on win32. Desktop CI / Linux
 * agents need this path for true "double-click on any Windows PC" packages.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');
const { createWriteStream, createReadStream } = require('fs');
const { pipeline } = require('stream/promises');
const { execSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const outDir = path.join(root, 'win-python');
const PY_VER = process.env.TAKTON_WIN_PYTHON_VER || '3.12.8';
const PY_SHORT = PY_VER.split('.').slice(0, 2).join(''); // 3.12
const EMBED_URL =
  process.env.TAKTON_WIN_PYTHON_URL ||
  `https://www.python.org/ftp/python/${PY_VER}/python-${PY_VER}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';
const reqProd = path.join(root, 'backend', 'requirements-prod.txt');
const reqWin = path.join(root, 'backend', 'requirements-win.txt');
const wheelsDir = path.join(root, '.cache', 'win-wheels');

function log(...a) {
  console.log('[prepare-win-python-cross]', ...a);
}

function fail(msg) {
  console.error('[prepare-win-python-cross] FAIL:', msg);
  process.exit(1);
}

function rmrf(p) {
  try {
    fs.rmSync(p, { recursive: true, force: true });
  } catch {
    /* ignore */
  }
}

function download(url, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  return new Promise((resolve, reject) => {
    const go = (u, redirects = 0) => {
      if (redirects > 8) return reject(new Error('too many redirects'));
      https
        .get(u, (res) => {
          if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
            res.resume();
            return go(res.headers.location, redirects + 1);
          }
          if (res.statusCode !== 200) {
            res.resume();
            return reject(new Error(`HTTP ${res.statusCode} for ${u}`));
          }
          const f = createWriteStream(dest);
          res.pipe(f);
          f.on('finish', () => f.close(() => resolve(dest)));
          f.on('error', reject);
        })
        .on('error', reject);
    };
    go(url);
  });
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, {
    stdio: 'inherit',
    encoding: 'utf8',
    ...opts,
  });
  if (r.status !== 0) fail(`${cmd} ${args.join(' ')} exited ${r.status}`);
}

function unzip(zipPath, dest) {
  fs.mkdirSync(dest, { recursive: true });
  // prefer unzip, then powershell, then python zipfile
  try {
    execSync(`unzip -o -q "${zipPath}" -d "${dest}"`, { stdio: 'inherit' });
    return;
  } catch {
    /* try python */
  }
  run('python3', [
    '-c',
    `import zipfile; zipfile.ZipFile(r"${zipPath}").extractall(r"${dest}")`,
  ]);
}

function writeWinRequirements() {
  // Prefer checked-in requirements-win.txt (no uvicorn[standard] marker trap).
  if (fs.existsSync(reqWin)) {
    log('using', reqWin);
    return reqWin;
  }
  if (fs.existsSync(reqProd)) {
    log('fallback to requirements-prod.txt');
    return reqProd;
  }
  fail('no requirements-win.txt or requirements-prod.txt');
}

function enableSite(embedDir) {
  const candidates = fs.readdirSync(embedDir).filter((f) => f.endsWith('._pth'));
  for (const name of candidates) {
    const full = path.join(embedDir, name);
    const zipName = name.replace(/\._pth$/, '.zip');
    const text = [zipName, '.', 'Lib\site-packages', '', 'import site', ''].join('
');
    fs.writeFileSync(full, text, 'utf8');
    log('patched', name);
  }
  fs.mkdirSync(path.join(embedDir, 'Lib', 'site-packages'), { recursive: true });
}


function installWheelsWithHostPip(reqFile) {
  fs.mkdirSync(wheelsDir, { recursive: true });
  log('downloading win_amd64 wheels…');
  const r = spawnSync(
    'python3',
    [
      '-m', 'pip', 'download',
      '-r', reqFile,
      '-d', wheelsDir,
      '--platform', 'win_amd64',
      '--python-version', '312',
      '--implementation', 'cp',
      '--abi', 'cp312',
      '--only-binary=:all:',
    ],
    { stdio: 'inherit' },
  );
  if (r.status !== 0) {
    const r2 = spawnSync(
      'python3',
      [
        '-m', 'pip', 'download',
        '-r', reqFile,
        '-d', wheelsDir,
        '--platform', 'win_amd64',
        '--python-version', '312',
        '--only-binary=:all:',
      ],
      { stdio: 'inherit' },
    );
    if (r2.status !== 0) fail('pip download for win_amd64 failed');
  }
  const site = path.join(outDir, 'Lib', 'site-packages');
  fs.mkdirSync(site, { recursive: true });
  log('extracting wheels into', site, '(bypass host platform tag check)');
  // Python zipfile extract — pip install rejects win_amd64 on Linux
  run('python3', [
    '-c',
    `
import zipfile, sys
from pathlib import Path
wheels = Path(r"${wheelsDir.replace(/\/g, '/')}")
site = Path(r"${site.replace(/\/g, '/')}")
site.mkdir(parents=True, exist_ok=True)
for whl in sorted(wheels.glob("*.whl")):
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(site)
    print("extracted", whl.name)
assert (site / "uvicorn").exists() or (site / "uvicorn.py").exists(), "uvicorn missing"
assert (site / "fastapi").exists(), "fastapi missing"
print("site-packages ready")
`.trim(),
  ]);
}



function prune(site) {
  if (!fs.existsSync(site)) return;
  const drop = new Set([
    'pytest',
    '_pytest',
    'tests',
    'test',
    'testing',
    '__pycache__',
  ]);
  const walk = (dir, depth = 0) => {
    if (depth > 5 || !fs.existsSync(dir)) return;
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      let st;
      try {
        st = fs.statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) {
        const lower = name.toLowerCase();
        if (
          drop.has(lower) ||
          lower.startsWith('pytest') ||
          lower.endsWith('.dist-info') && lower.includes('pytest')
        ) {
          rmrf(full);
        } else {
          walk(full, depth + 1);
        }
      } else if (name.endsWith('.pdb') || name.endsWith('.chm')) {
        try {
          fs.unlinkSync(full);
        } catch {
          /* ignore */
        }
      }
    }
  };
  walk(site);
}

async function main() {
  if (!fs.existsSync(reqProd) && !fs.existsSync(reqWin)) {
    fail('backend requirements missing');
  }
  const reqFile = writeWinRequirements();

  const marker = path.join(outDir, 'python.exe');
  const force = process.env.TAKTON_FORCE_WIN_PYTHON === '1';
  if (fs.existsSync(marker) && !force) {
    // verify critical modules present as files
    const site = path.join(outDir, 'Lib', 'site-packages');
    if (fs.existsSync(path.join(site, 'uvicorn')) && fs.existsSync(path.join(site, 'fastapi'))) {
      log('win-python already prepared; set TAKTON_FORCE_WIN_PYTHON=1 to rebuild');
      prune(site);
      process.exit(0);
    }
  }

  rmrf(outDir);
  fs.mkdirSync(outDir, { recursive: true });
  const zipPath = path.join(root, '.cache', `python-${PY_VER}-embed-amd64.zip`);
  if (!fs.existsSync(zipPath) || force) {
    log('downloading', EMBED_URL);
    await download(EMBED_URL, zipPath);
  } else {
    log('using cached embed zip');
  }
  log('extracting to', outDir);
  unzip(zipPath, outDir);
  if (!fs.existsSync(path.join(outDir, 'python.exe'))) {
    fail('python.exe missing after extract');
  }
  enableSite(outDir);

  // get-pip for first-run fallback on user machines
  const getPip = path.join(outDir, 'get-pip.py');
  try {
    await download(GET_PIP_URL, getPip);
  } catch (e) {
    log('get-pip download failed (non-fatal):', e.message || e);
  }

  installWheelsWithHostPip(reqFile);
  prune(path.join(outDir, 'Lib', 'site-packages'));

  // write a tiny README for support
  fs.writeFileSync(
    path.join(outDir, 'TAKTON_RUNTIME.txt'),
    `Takton Windows embed Python ${PY_VER}\nPrepared: ${new Date().toISOString()}\n`,
    'utf8',
  );

  // size report
  try {
    const du = execSync(`du -sh "${outDir}"`, { encoding: 'utf8' }).trim();
    log('done →', du);
  } catch {
    log('done →', outDir);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
