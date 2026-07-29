# Electron 唯一真源

**Canonical sources live here** (`electron/main.ts`, `electron/preload.ts`).

- Root `package.json`: `"main": "electron/dist/main.js"`, `build:electron` → this folder.
- `frontend/package.json`: builds this folder via `cd ../electron && tsc`, main points to `../electron/dist/main.js`.
- `frontend/electron/` is a **pointer only** (see README there). Do not fork logic.

Default Kernel port: **8090** (aligned with CLI / DEV_HANDBOOK).
