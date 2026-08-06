#!/usr/bin/env node
/**
 * Pre-pack hygiene: strip developer API / OAuth secrets from the pack process
 * environment and refuse to ship known secret files under backend/vendor.
 *
 * Usage (auto-run by dist scripts):
 *   node scripts/pack-sanitize-env.mjs
 *   node scripts/pack-sanitize-env.mjs --check-only
 */
import { existsSync, readdirSync, statSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const checkOnly = process.argv.includes('--check-only');

const STRIP_EXACT = [
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
  'AZURE_OPENAI_API_KEY',
  'GOOGLE_API_KEY',
  'GEMINI_API_KEY',
  'XAI_API_KEY',
  'GROK_API_KEY',
  'COHERE_API_KEY',
  'MISTRAL_API_KEY',
  'TOGETHER_API_KEY',
  'FIREWORKS_API_KEY',
  'DEEPSEEK_API_KEY',
  'HF_TOKEN',
  'HUGGINGFACE_HUB_TOKEN',
  'TAKTON_LLM_API_KEY',
  'TAKTON_EMBEDDING_API_KEY',
  'TAKTON_RERANKER_API_KEY',
  'TAKTON_IMAGE_API_KEY',
  'TAKTON_OPENAI_CHATGPT_ACCOUNT_ID',
  'LLM_API_KEY',
  'API_KEY',
  'AWS_SECRET_ACCESS_KEY',
  'AWS_ACCESS_KEY_ID',
  'AWS_SESSION_TOKEN',
];

const STRIP_RE =
  /^(?:TAKTON_)?(?:LLM_|OPENAI_|ANTHROPIC_|AZURE_|GEMINI_|GOOGLE_|XAI_|GROK_|COHERE_|MISTRAL_|TOGETHER_|FIREWORKS_|DEEPSEEK_|CLAUDE_|HF_|HUGGINGFACE_)?(?:API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|OAUTH_.*|.*_SECRET|.*_TOKEN)$/i;

const leakedEnv = [];
for (const k of Object.keys(process.env)) {
  if (STRIP_EXACT.includes(k) || STRIP_RE.test(k)) {
    // Keep non-secret TAKTON_* operational vars (JWT is re-generated in Electron)
    if (
      k === 'TAKTON_JWT_SECRET' ||
      k === 'TAKTON_API_KEY' ||
      k === 'TAKTON_SETTINGS_ENCRYPTION_SALT' ||
      k === 'TAKTON_DESKTOP_PERMISSION_SECRET'
    ) {
      // These would pin product crypto to packager machine — strip for pack.
      leakedEnv.push(k);
      continue;
    }
    if (k.endsWith('_SECRET') || k.includes('API_KEY') || k.includes('TOKEN') || k.includes('OAUTH')) {
      leakedEnv.push(k);
    }
  }
}

// Always strip known secrets from this process (and children inherit if we export)
for (const k of leakedEnv) {
  delete process.env[k];
}
for (const k of STRIP_EXACT) {
  delete process.env[k];
}
// Product pack must not inherit packager dotenv path
delete process.env.TAKTON_ENV_FILE;
process.env.TAKTON_LOAD_DOTENV = '0';
process.env.TAKTON_PACKAGED = process.env.TAKTON_PACKAGED || '0';

const bannedNames = new Set([
  '.env',
  'secrets.json',
  'takton.db',
  'initial-credentials.txt',
  'initial_admin_password',
]);
const bannedSuffixes = ['.db', '.db-wal', '.db-shm', '.env', '.pem', '.key'];
const bannedContains = ['.env.', 'oauth_token', 'credentials.json'];

function isBanned(name) {
  const lower = name.toLowerCase();
  if (bannedNames.has(lower)) return true;
  if (lower === '.env.example') return false;
  for (const s of bannedSuffixes) {
    if (lower.endsWith(s) && lower !== '.env.example') return true;
  }
  for (const c of bannedContains) {
    if (lower.includes(c) && !lower.endsWith('.example') && !lower.includes('example')) return true;
  }
  return false;
}

const offenders = [];
function walk(dir, depth = 0) {
  if (depth > 4) return;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const ent of entries) {
    const name = ent.name;
    if (name === 'node_modules' || name === '.git' || name === 'target' || name === 'win-python') continue;
    const full = join(dir, name);
    if (ent.isDirectory()) {
      if (name === '.takton' || name === '.computers' || name === 'uploads') {
        offenders.push(full + '/');
        continue;
      }
      walk(full, depth + 1);
    } else if (isBanned(name)) {
      // .env.example is allowed
      if (name === '.env.example') continue;
      offenders.push(full);
    }
  }
}

// Scan pack-relevant roots
for (const rel of ['backend', 'vendor', 'frontend', '.']) {
  const d = join(root, rel);
  if (existsSync(d)) walk(d, rel === '.' ? 3 : 0);
}

// Soft content sniff for .env-like files that slipped naming
const sniffRoots = [join(root, 'backend'), root];
for (const d of sniffRoots) {
  try {
    for (const name of readdirSync(d)) {
      if (!name.startsWith('.env')) continue;
      if (name === '.env.example') continue;
      const full = join(d, name);
      if (existsSync(full) && statSync(full).isFile()) {
        if (!offenders.includes(full)) offenders.push(full);
      }
    }
  } catch {
    /* ignore */
  }
}

console.log('[pack-sanitize] stripped env keys:', leakedEnv.length ? leakedEnv.join(', ') : '(none present)');
if (offenders.length) {
  console.error('[pack-sanitize] REFUSING to pack — secret/local data files present:');
  for (const o of offenders.slice(0, 40)) console.error('  -', o);
  console.error(
    'Remove these before dist/pack (API keys, OAuth tokens, DB, .env). ' +
      'Desktop product must ship empty of developer credentials.',
  );
  process.exit(1);
}

if (checkOnly) {
  console.log('[pack-sanitize] OK (check-only)');
  process.exit(0);
}

// Write a small marker env file for child npm scripts to source if needed
console.log('[pack-sanitize] OK — pack environment cleaned (no secret files under backend/vendor)');
// Export via stdout for shells that eval: not required; parent already mutated if same node process.
// For npm scripts, this script is run as a separate process — so we also write unset hints:
const unsetFile = join(root, '.pack-env-unset');
const lines = [
  '# generated by pack-sanitize-env.mjs — do not commit',
  ...STRIP_EXACT.map((k) => k),
  'TAKTON_ENV_FILE',
  'TAKTON_LLM_API_KEY',
  'TAKTON_OPENAI_CHATGPT_ACCOUNT_ID',
];
import { writeFileSync } from 'node:fs';
writeFileSync(unsetFile, lines.join('\n') + '\n', 'utf8');
console.log('[pack-sanitize] wrote', unsetFile);
