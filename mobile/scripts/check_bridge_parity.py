#!/usr/bin/env python3
"""Fail if Flutter call() methods are missing from HTTP and FFI dispatch tables."""
import re, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
tb = (root / "flutter_app/lib/bridge/takton_bridge.dart").read_text()
hb = (root / "flutter_app/lib/bridge/http_bridge.dart").read_text()
ffi = (root / "crates/ffi/src/lib.rs").read_text()
calls = set(re.findall(r"call\('([a-z0-9_]+)'", tb))
http = set(re.findall(r"case '([a-z0-9_]+)'", hb))
# include OR-arms: "mesh" | "mesh_status" =>
ffi_m = set(re.findall(r'"([a-z0-9_]+)"', re.search(r'async fn dispatch[\s\S]*?\n}', ffi).group(0)))
# only method names that appear as match arms before =>
ffi_arms = set()
for m in re.finditer(r'((?:"[a-z0-9_]+"\s*\|\s*)*"[a-z0-9_]+")\s*=>', ffi):
    ffi_arms.update(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
miss_http = sorted(calls - http)
miss_ffi = sorted(calls - ffi_arms)
if miss_http or miss_ffi:
    print("BRIDGE PARITY FAIL")
    if miss_http: print("  missing HTTP:", miss_http)
    if miss_ffi: print("  missing FFI:", miss_ffi)
    sys.exit(1)
print(f"BRIDGE PARITY OK · {len(calls)} methods")
