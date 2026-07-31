# takton-kernel-host (vendor)

Place the built `takton-kernel-host` / `takton-kernel-host.exe` here for product discovery.

```powershell
.\scripts\build-kernel-host.ps1 -Release
```

This directory is preferred over `target/debug` by `backend/kernel_rust` and `start.py`.

See `docs/kernel-abi-v1.md`.
