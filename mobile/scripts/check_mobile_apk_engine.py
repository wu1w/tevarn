#!/usr/bin/env python3
"""Fail CI/local build if APK is missing the native mobile engine .so."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED_ANY = (
    "lib/arm64-v8a/libtevarn_mobile_ffi.so",
    "lib/arm64-v8a/libtakton_mobile_ffi.so",  # transitional 0.4.0 name
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_mobile_apk_engine.py <path-to.apk>", file=sys.stderr)
        return 2
    apk = Path(argv[1])
    if not apk.is_file():
        print(f"missing apk: {apk}", file=sys.stderr)
        return 2
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
    found = [n for n in REQUIRED_ANY if n in names]
    arm = sorted(n for n in names if n.startswith("lib/arm64-v8a/"))
    print("arm64-v8a libs:")
    for n in arm:
        print(" ", n)
    if not found:
        print(
            "ERROR: APK missing native engine .so "
            f"(need one of {REQUIRED_ANY})",
            file=sys.stderr,
        )
        return 1
    print("OK engine so:", ", ".join(found))
    # Soft warning: tevarn dart prefers tevarn so name
    if "lib/arm64-v8a/libtevarn_mobile_ffi.so" not in names:
        print(
            "WARN: only legacy libtakton_mobile_ffi.so present; "
            "Dart dual-loader will still work, prefer shipping libtevarn_mobile_ffi.so",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
