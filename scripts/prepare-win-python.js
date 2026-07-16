/**
 * Pre-install backend requirements into win-python site-packages
 * so Windows installers ship a ready-to-run backend (no first-run pip).
 *
 * Uses requirements-prod.txt (no pytest/asyncpg/dev tools).
 * Prunes test/cache junk after install to shrink the embed tree.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const python = path.join(root, 'win-python', 'python.exe');
const reqProd = path.join(root, 'backend', 'requirements-prod.txt');
const reqFallback = path.join(root, 'backend', 'requirements.txt');
const req = fs.existsSync(reqProd) ? reqProd : reqFallback;
const sitePackages = path.join(root, 'win-python', 'Lib', 'site-packages');

if (process.platform !== 'win32') {
  console.log('[prepare-win-python] skip (not Windows)');
  process.exit(0);
}

if (!fs.existsSync(python)) {
  console.warn('[prepare-win-python] win-python/python.exe not found — skip');
  process.exit(0);
}

if (!fs.existsSync(req)) {
  console.error('[prepare-win-python] requirements file missing:', req);
  process.exit(1);
}

function hasModule(mod) {
  const result = spawnSync(
    python,
    ['-c', `import ${mod}`],
    {
      encoding: 'utf-8',
      env: {
        ...process.env,
        PYTHONNOUSERSITE: '1',
        PYTHONPATH: '',
      },
    },
  );
  return result.status === 0;
}

function rmrf(target) {
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // ignore
  }
}

function pruneSitePackages() {
  if (!fs.existsSync(sitePackages)) return;
  const pruneNames = [
    'pytest',
    '_pytest',
    'pytest_asyncio',
    'asgi_lifespan',
    'asyncpg',
    'asyncpg-*.dist-info',
  ];
  for (const name of fs.readdirSync(sitePackages)) {
    const lower = name.toLowerCase();
    if (
      lower === 'pytest' ||
      lower === '_pytest' ||
      lower.startsWith('pytest') ||
      lower.startsWith('asgi_lifespan') ||
      lower === 'asyncpg' ||
      lower.startsWith('asyncpg-') ||
      lower.endsWith('.pdb') ||
      lower.endsWith('.chm') ||
      name === '__pycache__'
    ) {
      console.log(`[prepare-win-python] prune ${name}`);
      rmrf(path.join(sitePackages, name));
    }
  }
  // Drop common test trees inside heavy packages
  const walkPrune = (dir, depth = 0) => {
    if (depth > 4 || !fs.existsSync(dir)) return;
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (
          ent.name === 'tests' ||
          ent.name === 'test' ||
          ent.name === '__pycache__' ||
          ent.name === 'testing'
        ) {
          rmrf(full);
        } else {
          walkPrune(full, depth + 1);
        }
      }
    }
  };
  walkPrune(sitePackages, 0);
}

const requiredModules = ['uvicorn', 'fastapi', 'mcp'];
const missing = requiredModules.filter((m) => !hasModule(m));
const forceReinstall = process.env.TAKTON_FORCE_PIP === '1';

if (!forceReinstall && missing.length === 0 && fs.existsSync(path.join(sitePackages, 'uvicorn'))) {
  console.log('[prepare-win-python] critical deps present (uvicorn/fastapi/mcp), prune only');
  pruneSitePackages();
  process.exit(0);
}

console.log(
  `[prepare-win-python] Installing prod deps into win-python from ${path.basename(req)} (missing: ${missing.join(', ') || 'force'}) ...`,
);
const result = spawnSync(
  python,
  ['-m', 'pip', 'install', '-r', req, '--no-warn-script-location', '--disable-pip-version-check'],
  {
    stdio: 'inherit',
    cwd: path.dirname(python),
    env: {
      ...process.env,
      PYTHONNOUSERSITE: '1',
      PYTHONPATH: '',
    },
  },
);

if (result.status !== 0) {
  console.error('[prepare-win-python] pip install failed');
  process.exit(result.status || 1);
}

if (!hasModule('mcp')) {
  console.log('[prepare-win-python] mcp still missing, installing mcp>=1.12.0 ...');
  const mcpResult = spawnSync(
    python,
    ['-m', 'pip', 'install', 'mcp>=1.12.0', '--no-warn-script-location', '--disable-pip-version-check'],
    {
      stdio: 'inherit',
      cwd: path.dirname(python),
      env: {
        ...process.env,
        PYTHONNOUSERSITE: '1',
        PYTHONPATH: '',
      },
    },
  );
  if (mcpResult.status !== 0) {
    console.error('[prepare-win-python] mcp install failed');
    process.exit(mcpResult.status || 1);
  }
}

pruneSitePackages();
console.log('[prepare-win-python] done');
