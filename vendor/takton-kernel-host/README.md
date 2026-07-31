# takton-kernel-host (vendor)

Product / Electron discovery path for the Rust kernel host binary.

## Stage (required before `npm run pack` / `dist`)

```powershell
# Windows
.\scripts\build-kernel-host.ps1 -Release

# or ensure + auto-build
node scripts/ensure-vendor-host.mjs
```

```bash
# Linux / macOS
./scripts/build-kernel-host.sh --release
```

Writes:

- `takton-kernel-host` / `takton-kernel-host.exe` (**gitignored**)
- `STAGED.json` (metadata; safe to commit)

## Discovery order

| Consumer | Order |
|----------|--------|
| `start.py` / `backend.kernel_rust.client` | `TAKTON_KERNEL_HOST_BIN` → `target/release` → `target/debug` → **vendor** (newest mtime within tier) |
| Electron packaged | `resources/takton-kernel-host/*` (from this folder via `extraResources`) |

`npm run pack` / `dist` run `ensure-vendor-host` first and **fail** if the binary is missing (`TAKTON_SKIP_VENDOR_HOST=1` to bypass in pure FE dev).

See `docs/kernel-abi-v1.md`.
