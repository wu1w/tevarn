# Deprecated local sources

**Do not edit Electron logic here.**

Canonical implementation: **`../../electron/`** (repo root).

| Action | Command |
|--------|---------|
| Build | `npm run build:electron` (from `frontend/`, compiles `../electron`) |
| Main entry | `../electron/dist/main.js` (see `frontend/package.json` `"main"`) |

Stub files `main.ts` / `preload.ts` remain only so old paths fail loudly if compiled by mistake.
