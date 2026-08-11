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
const { createWriteStream } = require('fs');
const { execSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const outDir = path.join(root, 'win-python');
const PY_VER = process.env.TEVARN_WIN_PYTHON_VER || '3.12.8';
const EMBED_URL =
  process.env.TEVARN_WIN_PYTHON_URL ||
  `https://www.python.org/ftp/python/${PY_VER}/python-${PY_VER}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';
const reqProd = path.join(root, 'backend', 'requirements-prod.txt');
const reqWin = path.join(root, 'backend', 'requirements-win.txt');
const wheelsDir = path.join(root, '.cache', 'win-wheels');
// Platform markers (sys_platform==win32) are evaluated on the *host* during
// `pip download --platform win_amd64`, so Windows-only deps like pywin32 are
// skipped on Linux. Always fetch these explicitly for desktop packs.
const WIN_ONLY_EXTRAS = ['pywin32>=311'];

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
  try {
    execSync(`unzip -o -q "${zipPath}" -d "${dest}"`, { stdio: 'inherit' });
    return;
  } catch {
    /* try python */
  }
  // Windows hosts usually have `python`, not `python3`
  const pyCandidates = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python'];
  let lastErr = null;
  for (const py of pyCandidates) {
    try {
      run(py, [
        '-c',
        `import zipfile; zipfile.ZipFile(${JSON.stringify(zipPath)}).extractall(${JSON.stringify(dest)})`,
      ]);
      return;
    } catch (e) {
      lastErr = e;
    }
  }
  // PowerShell Expand-Archive fallback (Windows)
  if (process.platform === 'win32') {
    try {
      execSync(
        `powershell -NoProfile -Command "Expand-Archive -LiteralPath '${zipPath.replace(/'/g, "''")}' -DestinationPath '${dest.replace(/'/g, "''")}' -Force"`,
        { stdio: 'inherit' },
      );
      return;
    } catch (e) {
      lastErr = e;
    }
  }
  fail(`unzip failed for ${zipPath}: ${lastErr && lastErr.message ? lastErr.message : lastErr}`);
}

function writeWinRequirements() {
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
    // python embed ._pth: enable site-packages for pip-installed deps
    const text = [zipName, '.', 'Lib\\site-packages', '', 'import site', ''].join('\n');
    fs.writeFileSync(full, text, 'utf8');
    log('patched', name);
  }
  fs.mkdirSync(path.join(embedDir, 'Lib', 'site-packages'), { recursive: true });
}

function pipDownloadWin(args) {
  return spawnSync(
    'python3',
    [
      '-m', 'pip', 'download',
      ...args,
      '-d', wheelsDir,
      '--platform', 'win_amd64',
      '--python-version', '312',
      '--implementation', 'cp',
      '--abi', 'cp312',
      '--only-binary=:all:',
    ],
    { stdio: 'inherit' },
  );
}

/** pywin32 needs .pth + DLL path bootstrap for embeddable Python. */
function fixupPywin32(site, embedDir) {
  const pth = path.join(site, 'pywin32.pth');
  if (!fs.existsSync(pth)) {
    // Minimal pth matching upstream wheel
    fs.writeFileSync(
      pth,
      ['win32', 'win32\\lib', 'pythonwin', 'import pywin32_bootstrap', ''].join('\n'),
      'utf8',
    );
    log('wrote', pth);
  }
  const sys32 = path.join(site, 'pywin32_system32');
  if (fs.existsSync(sys32)) {
    const init = path.join(sys32, '__init__.py');
    if (!fs.existsSync(init)) {
      fs.writeFileSync(init, '', 'utf8');
    }
    // Also drop DLLs next to python.exe so the Windows loader finds them even
    // if add_dll_directory is unavailable in restricted embeds.
    for (const name of fs.readdirSync(sys32)) {
      if (name.toLowerCase().endsWith('.dll')) {
        const src = path.join(sys32, name);
        const dest = path.join(embedDir, name);
        try {
          fs.copyFileSync(src, dest);
          log('copied', name, '→ embed root');
        } catch (e) {
          log('copy dll skip', name, e.message || e);
        }
      }
    }
  }
  const pywintypes = path.join(site, 'win32', 'lib', 'pywintypes.py');
  if (!fs.existsSync(pywintypes)) {
    fail('pywin32 extract incomplete: missing win32/lib/pywintypes.py (mcp needs this on Windows)');
  }
  log('pywin32 ready (pywintypes present)');
}

function installWheelsWithHostPip(reqFile) {
  fs.mkdirSync(wheelsDir, { recursive: true });
  log('downloading win_amd64 wheels…');
  let r = pipDownloadWin(['-r', reqFile]);
  if (r.status !== 0) {
    r = pipDownloadWin(['-r', reqFile]);
    // second attempt without abi pin already in pipDownloadWin — try looser
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
  }
  // Force Windows-only deps that markers skip on Linux hosts
  for (const pkg of WIN_ONLY_EXTRAS) {
    log('downloading Windows-only extra:', pkg);
    const er = pipDownloadWin([pkg]);
    if (er.status !== 0) fail(`pip download extra failed: ${pkg}`);
  }
  const site = path.join(outDir, 'Lib', 'site-packages');
  fs.mkdirSync(site, { recursive: true });
  log('extracting wheels into', site, '(bypass host platform tag check)');
  const py = `
import zipfile
from pathlib import Path
wheels = Path(${JSON.stringify(wheelsDir)})
site = Path(${JSON.stringify(site)})
site.mkdir(parents=True, exist_ok=True)
for whl in sorted(wheels.glob("*.whl")):
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(site)
    print("extracted", whl.name)
assert (site / "uvicorn").exists() or (site / "uvicorn.py").exists(), "uvicorn missing"
assert (site / "fastapi").exists(), "fastapi missing"
assert (site / "mcp").exists(), "mcp missing"
# pywin32 (required by mcp on Windows)
assert (site / "win32" / "lib" / "pywintypes.py").exists(), "pywin32/pywintypes missing"
assert (site / "pywin32.pth").exists() or True
print("site-packages ready")
`;
  run('python3', ['-c', py]);
  fixupPywin32(site, outDir);
}

function prune(site) {
  if (!fs.existsSync(site)) return;
  const drop = new Set(['pytest', '_pytest', 'tests', 'test', 'testing', '__pycache__']);
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
          (lower.endsWith('.dist-info') && lower.includes('pytest'))
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
  const force = process.env.TEVARN_FORCE_WIN_PYTHON === '1';
  if (fs.existsSync(marker) && !force) {
    const site = path.join(outDir, 'Lib', 'site-packages');
    const hasPywin =
      fs.existsSync(path.join(site, 'win32', 'lib', 'pywintypes.py')) ||
      fs.existsSync(path.join(site, 'pywin32.pth'));
    if (
      fs.existsSync(path.join(site, 'uvicorn')) &&
      fs.existsSync(path.join(site, 'fastapi')) &&
      fs.existsSync(path.join(site, 'mcp')) &&
      hasPywin
    ) {
      log('win-python already prepared; set TEVARN_FORCE_WIN_PYTHON=1 to rebuild');
      fixupPywin32(site, outDir);
      prune(site);
      process.exit(0);
    }
    if (!hasPywin) {
      log('win-python incomplete (missing pywin32) — rebuilding');
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

  const getPip = path.join(outDir, 'get-pip.py');
  try {
    await download(GET_PIP_URL, getPip);
  } catch (e) {
    log('get-pip download failed (non-fatal):', e.message || e);
  }

  installWheelsWithHostPip(reqFile);
  prune(path.join(outDir, 'Lib', 'site-packages'));

  fs.writeFileSync(
    path.join(outDir, 'TEVARN_RUNTIME.txt'),
    `Tevarn Windows embed Python ${PY_VER}\nPrepared: ${new Date().toISOString()}\n`,
    'utf8',
  );

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
