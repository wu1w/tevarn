/**
 * Copy frontend export (dist/ or out/) into backend/static for monorepo serve.
 * Run: cd frontend && npm run sync:backend-static
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fe = path.join(root, "frontend");
const dest = path.join(root, "backend", "static");
const src = ["dist", "out"]
  .map((d) => path.join(fe, d))
  .find((d) => fs.existsSync(path.join(d, "index.html")));

if (!src) {
  console.error("no frontend/dist or frontend/out with index.html — run npm run build:export first");
  process.exit(1);
}
if (!fs.existsSync(path.join(src, "version.json"))) {
  console.error("missing version.json in export tree — write:static-version did not run");
  process.exit(1);
}

fs.rmSync(dest, { recursive: true, force: true });
fs.cpSync(src, dest, { recursive: true });
console.log("synced", src, "->", dest);
