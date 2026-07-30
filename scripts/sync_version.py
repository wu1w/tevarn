#!/usr/bin/env python3
"""同步产品版本号：以 backend/VERSION 为权威，写回各 manifest。

用法:
  python scripts/sync_version.py          # 写入并对齐
  python scripts/sync_version.py --check  # 仅检查，不一致 exit 1（给 CI）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "backend" / "VERSION"


def read_authority() -> str:
    if not VERSION_FILE.is_file():
        raise SystemExit(f"missing authority file: {VERSION_FILE}")
    v = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not v or any(c.isspace() for c in v):
        raise SystemExit(f"invalid version in {VERSION_FILE!s}: {v!r}")
    return v


def _set_json_version(path: Path, version: str, *, check: bool) -> list[str]:
    errs: list[str] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    cur = str(data.get("version", ""))
    if cur != version:
        if check:
            errs.append(f"{path.relative_to(ROOT)}: {cur!r} != {version!r}")
        else:
            data["version"] = version
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"updated {path.relative_to(ROOT)} -> {version}")
    return errs


def _set_pyproject_version(path: Path, version: str, *, check: bool) -> list[str]:
    errs: list[str] = []
    text = path.read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        errs.append(f"{path.relative_to(ROOT)}: no version = \"...\" line")
        return errs
    cur = m.group(1)
    if cur != version:
        if check:
            errs.append(f"{path.relative_to(ROOT)}: {cur!r} != {version!r}")
        else:
            new = re.sub(
                r'(?m)^version\s*=\s*"[^"]+"',
                f'version = "{version}"',
                text,
                count=1,
            )
            path.write_text(new, encoding="utf-8")
            print(f"updated {path.relative_to(ROOT)} -> {version}")
    return errs


def _set_ts_version(path: Path, version: str, *, check: bool) -> list[str]:
    errs: list[str] = []
    if not path.is_file():
        return errs
    text = path.read_text(encoding="utf-8")
    # APP_VERSION = 'x' | "x"
    m = re.search(r"""(?:APP_VERSION|appVersion)\s*=\s*['"]([^'"]+)['"]""", text)
    if not m:
        # also accept export const APP_VERSION = "..."
        if version in text:
            return errs
        errs.append(f"{path.relative_to(ROOT)}: version literal not found")
        return errs
    cur = m.group(1)
    if cur != version:
        if check:
            errs.append(f"{path.relative_to(ROOT)}: {cur!r} != {version!r}")
        else:
            new = re.sub(
                r"""((?:APP_VERSION|appVersion)\s*=\s*)['"][^'"]+['"]""",
                rf"\1'{version}'",
                text,
                count=1,
            )
            path.write_text(new, encoding="utf-8")
            print(f"updated {path.relative_to(ROOT)} -> {version}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="only verify alignment")
    args = ap.parse_args()
    version = read_authority()
    errs: list[str] = []
    errs += _set_json_version(ROOT / "package.json", version, check=args.check)
    errs += _set_json_version(ROOT / "frontend" / "package.json", version, check=args.check)
    errs += _set_pyproject_version(ROOT / "pyproject.toml", version, check=args.check)
    be_py = ROOT / "backend" / "pyproject.toml"
    if be_py.is_file():
        errs += _set_pyproject_version(be_py, version, check=args.check)
    errs += _set_ts_version(ROOT / "frontend" / "lib" / "appVersion.ts", version, check=args.check)

    if errs:
        print("version mismatch:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.check:
        print(f"OK version aligned: {version}")
    else:
        print(f"OK synced to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
