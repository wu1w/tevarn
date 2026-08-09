"""Windows MSVC / vcvars environment for cargo/rustc under Job sandbox.

Agent JobBackend uses a thin PATH (no Developer Command Prompt). Without
INCLUDE/LIB/link.exe on PATH, cargo fails with ``linker `link.exe` not found``.

This module discovers ``vcvars64.bat`` (VS Build Tools / full VS) once, loads
the resulting env via ``cmd /c call … && set``, and merges into job env.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from functools import lru_cache
from typing import Mapping

logger = logging.getLogger(__name__)

# Keys that must come from vcvars for the MSVC linker to work.
_MSVC_OVERRIDE_KEYS = frozenset(
    {
        "INCLUDE",
        "LIB",
        "LIBPATH",
        "WINDOWSSDKDIR",
        "WINDOWSSDKVERSION",
        "WINDOWSSDKLIBVERSION",
        "VCINSTALLDIR",
        "VCTOOLSINSTALLDIR",
        "VCTOOLSVERSION",
        "VSCMD_ARG_TGT_ARCH",
        "VSCMD_ARG_HOST_ARCH",
        "VSCMD_VER",
        "UNIVERSALCRTSDKDIR",
        "UCRTVERSION",
        "DEVENVDIR",
        "FRAMEWORKDIR",
        "FRAMEWORKVERSION",
        "FRAMEWORKDIR64",
        "FRAMEWORKVERSION64",
        "NETFXSDKDIR",
        "EXTERNAL_INCLUDE",
    }
)

_VCVARS_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2019\Community\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Preview\VC\Auxiliary\Build\vcvars64.bat",
)


def discover_vcvars64() -> str | None:
    """Return path to vcvars64.bat if present."""
    for p in _VCVARS_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    # vswhere fallback
    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio",
        "Installer",
        "vswhere.exe",
    )
    if not os.path.isfile(vswhere):
        return None
    try:
        out = subprocess.check_output(
            [
                vswhere,
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            text=True,
            timeout=15,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            cand = os.path.join(out, "VC", "Auxiliary", "Build", "vcvars64.bat")
            if os.path.isfile(cand):
                return cand
    except Exception as e:
        logger.debug("vswhere vcvars lookup failed: %s", e)
    return None


def _parse_set_output(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip("\r")
        if not line or "=" not in line:
            continue
        # cmd `set` is KEY=VALUE; keys are typically UPPER but not always.
        k, _, v = line.partition("=")
        if not k:
            continue
        # Skip cmd noise
        if k.startswith("PROMPT") or k in (".",):
            continue
        env[k] = v
    return env


def _fallback_msvc_paths(vcvars: str) -> dict[str, str]:
    """If vcvars dump fails, still put link.exe on PATH via known layout."""
    # vcvars: .../VC/Auxiliary/Build/vcvars64.bat → install root = parents[3]
    try:
        build_dir = os.path.dirname(os.path.abspath(vcvars))
        # .../VC/Auxiliary/Build
        vc_dir = os.path.dirname(os.path.dirname(build_dir))  # .../VC
        vs_root = os.path.dirname(vc_dir)  # BuildTools or VS root
    except Exception:
        return {}
    msvc_tools = os.path.join(vc_dir, "Tools", "MSVC")
    host_bin = ""
    if os.path.isdir(msvc_tools):
        versions = sorted(
            (d for d in os.listdir(msvc_tools) if os.path.isdir(os.path.join(msvc_tools, d))),
            reverse=True,
        )
        for ver in versions:
            cand = os.path.join(msvc_tools, ver, "bin", "Hostx64", "x64")
            if os.path.isfile(os.path.join(cand, "link.exe")):
                host_bin = cand
                break
    extras = [p for p in (host_bin,) if p]
    # Common Windows SDK bin
    kits = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Windows Kits",
        "10",
        "bin",
    )
    if os.path.isdir(kits):
        try:
            vers = sorted(
                (d for d in os.listdir(kits) if d[0:1].isdigit()),
                reverse=True,
            )
            for ver in vers:
                cand = os.path.join(kits, ver, "x64")
                if os.path.isdir(cand):
                    extras.append(cand)
                    break
        except Exception:
            pass
    if not extras:
        return {}
    return {"PATH": os.pathsep.join(extras), "Path": os.pathsep.join(extras)}


@lru_cache(maxsize=1)
def load_msvc_env() -> Mapping[str, str]:
    """Load MSVC env dict once per process. Empty if vcvars missing/fails."""
    if os.name != "nt":
        return {}
    vcvars = discover_vcvars64()
    if not vcvars:
        logger.info("msvc_env: vcvars64.bat not found — cargo may fail without link.exe")
        return {}
    # Temp bat is more reliable than inline `call … >nul && set`
    # (redirect on call often yields exit 1 / empty dump under Python subprocess).
    import tempfile

    try:
        fd, bat_path = tempfile.mkstemp(prefix="takton_vcvars_", suffix=".bat")
        os.close(fd)
        # ASCII bat: call vcvars (banner to nul) then dump env
        body = (
            "@echo off\r\n"
            f'call "{vcvars}" >nul 2>&1\r\n'
            "set\r\n"
        )
        with open(bat_path, "w", encoding="ascii", errors="replace", newline="") as f:
            f.write(body)
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", bat_path],
            capture_output=True,
            timeout=90,
            # system code page — utf-8 often mis-decodes `set` on zh-CN
            text=False,
        )
        raw_b = proc.stdout or b""
        if not raw_b.strip() and proc.stderr:
            raw_b = proc.stderr
        raw = raw_b.decode("utf-8", errors="replace")
        if "INCLUDE=" not in raw.upper() and "Path=" not in raw and "PATH=" not in raw:
            # try mbcs (ANSI code page)
            raw = raw_b.decode("mbcs", errors="replace")
        env = _parse_set_output(raw)
        path_val = env.get("PATH") or env.get("Path") or ""
        has_include = any(k.upper() == "INCLUDE" for k in env)
        if not path_val or not has_include:
            fb = _fallback_msvc_paths(vcvars)
            if fb:
                logger.warning(
                    "msvc_env: vcvars dump incomplete (exit=%s keys=%s); using path fallback",
                    proc.returncode,
                    len(env),
                )
                # Prefer dump PATH if any, else fallback
                if not path_val:
                    env.update(fb)
                elif "PATH" not in env and "Path" in env:
                    pass
                if not has_include:
                    # still better than nothing for link.exe discovery
                    pass
                path_val = env.get("PATH") or env.get("Path") or ""
            if not path_val:
                logger.warning(
                    "msvc_env: vcvars produced empty PATH (exit=%s vcvars=%s)",
                    proc.returncode,
                    vcvars,
                )
                return {}
        # Normalize Path → PATH for consumers
        if "PATH" not in env and "Path" in env:
            env["PATH"] = env["Path"]
        logger.info(
            "msvc_env: loaded from %s (keys=%s has_INCLUDE=%s)",
            vcvars,
            len(env),
            any(k.upper() == "INCLUDE" for k in env),
        )
        return env
    except Exception as e:
        logger.warning("msvc_env: failed to load vcvars: %s", e)
        fb = _fallback_msvc_paths(vcvars) if vcvars else {}
        return fb
    finally:
        try:
            if "bat_path" in locals() and bat_path and os.path.isfile(bat_path):
                os.remove(bat_path)
        except Exception:
            pass


def merge_msvc_env(base: dict[str, str]) -> dict[str, str]:
    """Merge vcvars into *base* (PATH prepended; INCLUDE/LIB overridden)."""
    msvc = load_msvc_env()
    if not msvc:
        return base
    out = dict(base)
    # Normalize path key to PATH
    base_path = out.get("PATH") or out.get("Path") or ""
    msvc_path = msvc.get("PATH") or msvc.get("Path") or ""
    if msvc_path:
        # Prepend MSVC tools so link.exe wins
        if base_path:
            # Avoid doubling if already merged
            if msvc_path.lower() not in base_path.lower()[: min(len(base_path), len(msvc_path) + 8)]:
                out["PATH"] = msvc_path + os.pathsep + base_path
            else:
                out["PATH"] = base_path
        else:
            out["PATH"] = msvc_path
        out.pop("Path", None)

    for k, v in msvc.items():
        ku = k.upper()
        if ku in ("PATH", "PATHEXT"):
            continue
        if ku in _MSVC_OVERRIDE_KEYS or ku.startswith("VSCMD") or ku.startswith("VC"):
            out[k] = v
        else:
            # Keep host values for unrelated keys; fill gaps only
            if k not in out and k.upper() not in {x.upper() for x in out}:
                out[k] = v
    return out


def prepend_vcvars_call(command: str) -> str:
    """Optional: wrap a cmd string so vcvars runs first (belt-and-suspenders).

    Use ``&`` not ``&&``: vcvars often leaves errorlevel≠0 even on success,
    which would skip cargo under ``&&`` and produce empty agent thrash.
    """
    if os.name != "nt":
        return command
    c = (command or "").strip()
    if not c:
        return command
    # Already wrapped
    if re.search(r"(?i)vcvars(64|all)\.bat", c):
        return command
    vcvars = discover_vcvars64()
    if not vcvars:
        return command
    # Reset errorlevel then run user command
    return f'call "{vcvars}" >nul 2>&1 & {c}'


def needs_msvc_toolchain(command: str) -> bool:
    """True when command likely needs MSVC link.exe / INCLUDE."""
    c = command or ""
    return bool(
        re.search(
            r"(?i)(?:^|[\s;&|])(?:cargo|rustc)(?:\.exe)?\b",
            c,
        )
    )


# ── Host Rust toolchain resolution (avoid broken RUSTUP_HOME thrash) ──


def _rustup_toolchain_ok(rustup_home: str, *, prefer_msvc: bool = True) -> bool:
    """True if rustup_home has at least one toolchain with rustc.exe."""
    tc_root = os.path.join(rustup_home, "toolchains")
    if not os.path.isdir(tc_root):
        return False
    try:
        names = os.listdir(tc_root)
    except OSError:
        return False
    # Prefer msvc when available
    ordered = sorted(
        names,
        key=lambda n: (
            0 if prefer_msvc and "msvc" in n.lower() else 1,
            n.lower(),
        ),
    )
    for name in ordered:
        rustc = os.path.join(tc_root, name, "bin", "rustc.exe")
        if os.path.isfile(rustc):
            return True
    return False


def _rustup_has_msvc(rustup_home: str) -> bool:
    tc_root = os.path.join(rustup_home, "toolchains")
    if not os.path.isdir(tc_root):
        return False
    try:
        for name in os.listdir(tc_root):
            if "msvc" not in name.lower():
                continue
            if os.path.isfile(os.path.join(tc_root, name, "bin", "rustc.exe")):
                return True
    except OSError:
        return False
    return False


def discover_scoop_rust_bin(host_home: str = "") -> str | None:
    """Absolute dir containing host scoop standalone cargo.exe + rustc.exe."""
    home = (host_home or os.environ.get("USERPROFILE") or "").strip()
    candidates = []
    if home:
        candidates.append(os.path.join(home, "scoop", "apps", "rust", "current", "bin"))
    # also common install roots
    candidates.append(r"C:\Users\wuyw\scoop\apps\rust\current\bin")
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "cargo.exe")) and os.path.isfile(
            os.path.join(d, "rustc.exe")
        ):
            return d
    return None


def _cmd_safe_exe(path: str) -> str:
    """Format an absolute exe path for ``cmd.exe /d /c <command>``.

    Critical Windows quirk: when JobBackend runs
    ``cmd.exe /d /c <one-arg-string>``, quoting a path *without spaces*
    (e.g. ``\"C:\\Users\\…\\cargo.exe\" -V``) makes cmd treat the token as
    ``'\"C:\\…\\cargo.exe\"'`` and fail with “不是内部或外部命令”.

    Only quote when the path contains whitespace; otherwise use the bare path.
    """
    p = (path or "").strip()
    if not p:
        return p
    if re.search(r"[\s&()^]", p):
        # Prefer short 8.3 path to avoid quote hell under cmd /c
        try:
            import ctypes
            from ctypes import wintypes

            GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW  # type: ignore[attr-defined]
            GetShortPathNameW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                wintypes.DWORD,
            ]
            GetShortPathNameW.restype = wintypes.DWORD
            buf = ctypes.create_unicode_buffer(520)
            n = GetShortPathNameW(p, buf, 520)
            if n and n < 520 and buf.value and " " not in buf.value:
                return buf.value
        except Exception:
            pass
        return f'"{p}"'
    return p


def rewrite_cargo_to_absolute(command: str, host_home: str = "") -> str:
    """Force ``cargo``/``rustc`` tokens to scoop absolute paths (avoid rustup proxy)."""
    bin_dir = discover_scoop_rust_bin(host_home)
    if not bin_dir or not command:
        return command
    cargo = _cmd_safe_exe(os.path.join(bin_dir, "cargo.exe"))
    rustc = _cmd_safe_exe(os.path.join(bin_dir, "rustc.exe"))

    c = command
    # Only bare cargo/rustc tokens, not paths already absolute
    c = re.sub(
        r'(?i)(?<![A-Za-z0-9_./\\:"\'-])(?P<exe>cargo(?:\.exe)?)(?![A-Za-z0-9_])',
        lambda m: cargo,
        c,
    )
    c = re.sub(
        r'(?i)(?<![A-Za-z0-9_./\\:"\'-])(?P<exe>rustc(?:\.exe)?)(?![A-Za-z0-9_])',
        lambda m: rustc,
        c,
    )
    # Strip agent attempts to force broken rustup homes in the command itself
    c = re.sub(
        r"(?i)(?:set\s+)?RUSTUP_HOME\s*=\s*[^\s&|;]+",
        "set RUSTUP_HOME=",
        c,
    )
    c = re.sub(
        r"(?i)(?:set\s+)?RUSTUP_TOOLCHAIN\s*=\s*[^\s&|;]+",
        "set RUSTUP_TOOLCHAIN=",
        c,
    )
    return c


def apply_host_rust_env(env: dict[str, str], host_home: str = "") -> dict[str, str]:
    """Fix PATH / RUSTUP_HOME / CARGO_HOME for agent Job + background cargo.

    Problem: broken ``stable-…-msvc`` under scoop rustup (no rustc) + process
    RUSTUP_HOME pointing at it → ``Missing manifest in toolchain`` → agent
    thrash on where/dir/rustup diagnosis.

    Strategy:
    1. Prefer scoop standalone ``apps/rust/current/bin`` on PATH (works without rustup)
    2. Only set RUSTUP_HOME when a toolchain with real **msvc** rustc.exe exists
    3. Always clear broken RUSTUP_HOME; pin CARGO/RUSTC absolute when possible
    """
    out = dict(env)
    home = (host_home or out.get("USERPROFILE") or os.environ.get("USERPROFILE") or "").strip()

    # 1) Standalone scoop rust FIRST on PATH (before shims / rustup proxies)
    path = out.get("PATH") or out.get("Path") or ""
    scoop_bin = discover_scoop_rust_bin(home)
    extras: list[str] = []
    if scoop_bin:
        extras.append(scoop_bin)
    if home:
        extras.extend(
            [
                os.path.join(home, "scoop", "shims"),
                os.path.join(home, "scoop", "persist", "rustup", ".cargo", "bin"),
                os.path.join(home, ".cargo", "bin"),
            ]
        )
    # Prepend in reverse so first extras ends up leftmost
    for p in reversed(extras):
        if p and os.path.isdir(p):
            # remove existing occurrence then prepend
            parts = [x for x in path.split(os.pathsep) if x and x.lower() != p.lower()]
            path = p + os.pathsep + os.pathsep.join(parts)
    out["PATH"] = path
    out.pop("Path", None)

    # Pin absolute cargo/rustc so rustup proxy cannot win.
    # Also clear RUSTUP_TOOLCHAIN: mixed scoop-standalone + rustup home caused
    # agent-visible E0463 "can't find crate for std" even when msvc std exists.
    if scoop_bin:
        out["CARGO"] = os.path.join(scoop_bin, "cargo.exe")
        out["RUSTC"] = os.path.join(scoop_bin, "rustc.exe")
        rustdoc = os.path.join(scoop_bin, "rustdoc.exe")
        if os.path.isfile(rustdoc):
            out["RUSTDOC"] = rustdoc
        out["RUSTUP_TOOLCHAIN"] = ""

    # 2) Pick healthy RUSTUP_HOME (msvc only) or UNSET
    candidates: list[str] = []
    cur = (out.get("RUSTUP_HOME") or os.environ.get("RUSTUP_HOME") or "").strip()
    if cur:
        candidates.append(cur)
    if home:
        candidates.extend(
            [
                os.path.join(home, "scoop", "persist", "rustup", ".rustup"),
                os.path.join(home, "scoop", "apps", "rustup", "current", ".rustup"),
                os.path.join(home, ".rustup"),
            ]
        )
    chosen: str | None = None
    for c in candidates:
        if c and _rustup_has_msvc(c):
            chosen = c
            break

    if chosen and not scoop_bin:
        # Prefer rustup home only when we are NOT pinning scoop standalone bins
        out["RUSTUP_HOME"] = chosen
        logger.info("rust_env: RUSTUP_HOME=%s (msvc ok)", chosen)
    elif scoop_bin:
        # Scoop standalone rustc has its own sysroot; keep RUSTUP_HOME only as
        # cargo registry home companion — do not let it override RUSTC selection.
        if chosen:
            out["RUSTUP_HOME"] = chosen
        out["RUSTUP_TOOLCHAIN"] = ""
        logger.info(
            "rust_env: scoop rust bin=%s RUSTC pinned; RUSTUP_TOOLCHAIN cleared",
            scoop_bin,
        )
    else:
        if out.get("RUSTUP_HOME") or cur:
            logger.warning(
                "rust_env: no healthy msvc toolchain (had %s); "
                "unset RUSTUP_HOME, use scoop rust bin=%s",
                out.get("RUSTUP_HOME") or cur or "(none)",
                scoop_bin,
            )
        out.pop("RUSTUP_HOME", None)
        # Prevent rustup from re-reading a default via empty override in child
        out["RUSTUP_TOOLCHAIN"] = ""

    # 3) CARGO_HOME — host scoop persist only (never sandbox agent_home mirror)
    cargo_home = ""
    if home:
        for c in (
            os.path.join(home, "scoop", "persist", "rustup", ".cargo"),
            os.path.join(home, "scoop", "apps", "rustup", "current", ".cargo"),
            os.path.join(home, ".cargo"),
        ):
            if os.path.isdir(c):
                cargo_home = c
                break
    if cargo_home:
        out["CARGO_HOME"] = cargo_home
    # Drop sandbox-ish CARGO_HOME if present
    ch = (out.get("CARGO_HOME") or "").replace("/", "\\").lower()
    if ".computers" in ch and "\\home\\.cargo" in ch:
        if cargo_home:
            out["CARGO_HOME"] = cargo_home
        else:
            out.pop("CARGO_HOME", None)

    return out
