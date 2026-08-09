#!/usr/bin/env python3
"""Agent SDK pack/validate（T8）。

Usage:
  python scripts/tevarn_sdk_pack.py <agent-dir>
  python scripts/tevarn_sdk_pack.py <agent-dir> --out dist/
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

REQUIRED = ("name", "version", "entry", "permissions")
RISKY = frozenset({"terminal", "command", "bash", "shell", "*", "python", "process"})


def validate(root: Path) -> dict:
    man_path = root / "agent.json"
    if not man_path.is_file():
        return {"ok": False, "error": f"missing {man_path}"}
    try:
        data = json.loads(man_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"invalid json: {e}"}
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        return {"ok": False, "error": f"missing fields {missing}"}
    entry = root / str(data["entry"])
    if not entry.is_file():
        return {"ok": False, "error": f"entry not found: {entry}"}
    if not isinstance(data.get("permissions"), list):
        return {"ok": False, "error": "permissions must be list"}
    for sk in data.get("skills") or []:
        p = root / str(sk.get("path") or "")
        if sk.get("path") and not p.is_file():
            return {"ok": False, "error": f"skill path missing {p}"}
    perms = [str(p) for p in data["permissions"]]
    risky = sorted(set(perms) & RISKY)
    resources = data.get("resources") or {}
    isolation = str(resources.get("isolation") or "interactive")
    return {
        "ok": True,
        "name": data["name"],
        "version": data["version"],
        "permissions": perms,
        "skills": len(data.get("skills") or []),
        "risky_permissions": risky,
        "isolation": isolation,
        "auto_apply_forbidden": True,
        "note": "activate requires skill_verify + human gate (auto_apply=false)",
        "manifest": data,
    }


def pack_zip(root: Path, out_dir: Path, meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{meta['name']}-{meta['version']}.tevarn-agent.zip"
    dest = out_dir / name
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(root.rglob("*")):
            if f.is_dir() or f.is_symlink():
                continue
            if any(p in f.parts for p in (".git", "__pycache__", "node_modules", "dist")):
                continue
            zf.write(f, arcname=f"{meta['name']}/{f.relative_to(root).as_posix()}")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate/pack Tevarn agent package")
    ap.add_argument("agent_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="write zip to directory")
    args = ap.parse_args()
    root = args.agent_dir.resolve()
    if not root.is_dir():
        print(f"FAIL: not a directory: {root}")
        return 2
    meta = validate(root)
    if not meta.get("ok"):
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print("=== Agent package FAIL ===")
        return 1
    if args.out:
        zpath = pack_zip(root, args.out.resolve(), meta)
        meta["zip"] = str(zpath)
    print(json.dumps({k: v for k, v in meta.items() if k != "manifest"}, ensure_ascii=False, indent=2))
    print("=== Agent package OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
