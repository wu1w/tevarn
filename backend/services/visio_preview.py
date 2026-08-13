"""Visio (.vsd / .vsdx / .vsdm / .vdx) preview.

VSDX/VSDM is a ZIP of XML — we render pages to SVG without extra software.
Binary .vsd uses LibreOffice Draw or libvisio ``vsd2svg`` when present.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VISIO_EXTENSIONS = frozenset(
    {".vsd", ".vsdx", ".vsdm", ".vdx", ".vdw", ".vss", ".vssx", ".vst", ".vstx"}
)
_ZIP_EXTS = frozenset({".vsdx", ".vsdm", ".vssx", ".vstx"})
_DPI = 96.0
_MAX_PAGES = 40
_MAX_SHAPES = 400

_SHAPE_FILL = {
    "process": "#dbeafe",
    "decision": "#fef3c7",
    "start": "#dcfce7",
    "end": "#fee2e2",
    "terminator": "#dcfce7",
    "document": "#e0e7ff",
    "data": "#f3e8ff",
    "subprocess": "#cffafe",
    "database": "#e2e8f0",
}


def is_visio_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VISIO_EXTENSIONS


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _f(val: str | None, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def find_soffice() -> str | None:
    """Locate LibreOffice soffice binary (Windows/macOS/Linux)."""
    env = (os.environ.get("LIBREOFFICE_PATH") or os.environ.get("SOFFICE_PATH") or "").strip()
    if env and Path(env).is_file():
        return env
    names = ["soffice", "soffice.com", "libreoffice"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    candidates: list[Path] = []
    if sys.platform == "win32":
        for root in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ):
            candidates.append(Path(root) / "LibreOffice" / "program" / "soffice.com")
            candidates.append(Path(root) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    else:
        candidates.extend(
            [
                Path("/usr/bin/soffice"),
                Path("/usr/bin/libreoffice"),
                Path("/usr/lib/libreoffice/program/soffice"),
            ]
        )
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def find_vsd2svg() -> str | None:
    return shutil.which("vsd2svg") or shutil.which("vsd2xhtml")


def converter_status() -> dict[str, Any]:
    soffice = find_soffice()
    vsd2svg = find_vsd2svg()
    return {
        "soffice": soffice,
        "vsd2svg": vsd2svg,
        "native_vsdx": True,
        "binary_vsd_ready": bool(soffice or vsd2svg),
        "install_hint": _install_hint(),
    }


def _install_hint() -> str:
    if sys.platform == "win32":
        return (
            "旧版 .vsd 图形预览需要 LibreOffice Draw。"
            " PowerShell: winget install --id TheDocumentFoundation.LibreOffice -e"
            " --accept-package-agreements --accept-source-agreements"
        )
    if sys.platform == "darwin":
        return "旧版 .vsd 图形预览需要 LibreOffice：brew install --cask libreoffice"
    return "旧版 .vsd 图形预览需要：sudo apt install -y libreoffice-draw libvisio-tools"


def preview_visio(path: Path) -> dict[str, Any]:
    """Return a JSON-serializable preview payload for a Visio file."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    ext = path.suffix.lower()
    if ext not in VISIO_EXTENSIONS:
        raise ValueError(f"not a visio file: {path.name}")

    status = converter_status()
    if ext in _ZIP_EXTS or _looks_like_zip(path):
        pages = extract_zip_visio(path)
        return {
            "ok": True,
            "format": ext.lstrip("."),
            "converter": "native",
            "name": path.name,
            "pages": pages,
            "pdf_available": False,
            "hint": None,
            "converters": status,
        }

    if ext == ".vdx" or _looks_like_xml(path):
        pages = extract_vdx(path)
        return {
            "ok": True,
            "format": "vdx",
            "converter": "native",
            "name": path.name,
            "pages": pages,
            "pdf_available": False,
            "hint": None,
            "converters": status,
        }

    # Binary .vsd / .vss / .vst
    converted = convert_binary_visio(path)
    if converted:
        pages = converted["pages"]
        return {
            "ok": True,
            "format": ext.lstrip("."),
            "converter": converted["converter"],
            "name": path.name,
            "pages": pages,
            "pdf_path": converted.get("pdf_path"),
            "pdf_available": bool(converted.get("pdf_path")),
            "hint": None,
            "converters": status,
        }

    text = extract_ole_strings(path)
    return {
        "ok": True,
        "format": ext.lstrip("."),
        "converter": "strings",
        "name": path.name,
        "pages": [
            {
                "name": path.stem,
                "text": text or "(no extractable text)",
                "svg": None,
            }
        ],
        "pdf_available": False,
        "hint": status["install_hint"],
        "converters": status,
    }


def _looks_like_zip(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4).startswith(b"PK")
    except OSError:
        return False


def _looks_like_xml(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(64).lstrip()
            return head.startswith(b"<?xml") or head.startswith(b"<")
    except OSError:
        return False


def extract_zip_visio(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        def _page_num(n: str) -> int:
            m = re.search(r"page(\d+)", n, re.I)
            return int(m.group(1)) if m else 0

        page_files = sorted(
            [n for n in names if re.search(r"visio/pages/page\d+\.xml$", n, re.I)],
            key=_page_num,
        )
        page_meta = _parse_pages_index(zf)
        pages: list[dict[str, Any]] = []
        for i, name in enumerate(page_files[:_MAX_PAGES]):
            xml = zf.read(name).decode("utf-8", errors="replace")
            meta = page_meta[i] if i < len(page_meta) else {}
            pages.append(_render_page_xml(xml, meta.get("name") or f"Page-{i + 1}", meta))
        if not pages:
            # stencil / template: fall back to any visio xml text
            texts = []
            for n in names:
                if n.lower().endswith(".xml") and "visio/" in n.lower().replace("\\", "/"):
                    raw = zf.read(n).decode("utf-8", errors="replace")
                    t = _xml_text_dump(raw)
                    if t:
                        texts.append(f"[{n}]\n{t}")
            pages.append(
                {
                    "name": path.stem,
                    "text": "\n\n".join(texts)[:20000] or "(empty visio package)",
                    "svg": None,
                }
            )
        return pages


def _parse_pages_index(zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    cand = "visio/pages/pages.xml"
    if cand not in zf.namelist():
        return []
    try:
        root = ET.fromstring(zf.read(cand))
    except ET.ParseError:
        return []
    out: list[dict[str, Any]] = []
    for page in root.iter():
        if _local(page.tag) != "Page":
            continue
        cells = _cells(page)
        for child in page:
            if _local(child.tag) == "PageSheet":
                cells.update(_cells(child))
        out.append(
            {
                "name": page.get("Name") or page.get("NameU") or f"Page-{len(out) + 1}",
                "width": _f(cells.get("PageWidth"), 11.0),
                "height": _f(cells.get("PageHeight"), 8.5),
            }
        )
    return out


def extract_vdx(path: Path) -> list[dict[str, Any]]:
    xml = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [{"name": path.stem, "text": _xml_text_dump(xml), "svg": None}]
    pages: list[dict[str, Any]] = []
    for page in root.iter():
        if _local(page.tag) != "Page":
            continue
        cells = _cells(page)
        for child in page:
            if _local(child.tag) in {"PageSheet", "PageProps"}:
                cells.update(_cells(child))
        name = page.get("Name") or page.get("NameU") or f"Page-{len(pages) + 1}"
        meta = {
            "name": name,
            "width": _f(cells.get("PageWidth"), 11.0),
            "height": _f(cells.get("PageHeight"), 8.5),
        }
        pages.append(_render_page_element(page, name, meta))
        if len(pages) >= _MAX_PAGES:
            break
    if not pages:
        pages.append({"name": path.stem, "text": _xml_text_dump(xml), "svg": None})
    return pages


def _cells(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for child in list(el):
        if _local(child.tag) == "Cell":
            n = child.get("N")
            if n:
                out[n] = child.get("V") or ""
    return out


def _direct_text(el: ET.Element) -> str:
    chunks: list[str] = []
    for child in list(el):
        if _local(child.tag) == "Text":
            t = "".join(child.itertext()).strip()
            if t:
                chunks.append(re.sub(r"\s+", " ", t))
    return " ".join(chunks)


def _collect_shapes(parent: ET.Element, ox: float = 0.0, oy: float = 0.0) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for el in list(parent):
        loc = _local(el.tag)
        if loc == "Shapes":
            found.extend(_collect_shapes(el, ox, oy))
            continue
        if loc != "Shape":
            continue
        cells = _cells(el)
        pin_x = _f(cells.get("PinX")) + ox
        pin_y = _f(cells.get("PinY")) + oy
        width = _f(cells.get("Width"), 1.0)
        height = _f(cells.get("Height"), 0.5)
        loc_pin_x = _f(cells.get("LocPinX"), width / 2.0 if width else 0.0)
        loc_pin_y = _f(cells.get("LocPinY"), height / 2.0 if height else 0.0)
        name_u = (el.get("NameU") or el.get("Name") or "").lower()
        text = _direct_text(el)
        is_connector = (
            "BeginX" in cells
            and "EndX" in cells
            and ("connector" in name_u or "dynamic connector" in name_u or width < 0.05 or height < 0.05)
        ) or (el.get("Type") or "").lower() == "connector"
        if "BeginX" in cells and "EndX" in cells and not text:
            is_connector = True
        shape = {
            "name": el.get("NameU") or el.get("Name") or "",
            "text": text,
            "pin_x": pin_x,
            "pin_y": pin_y,
            "width": width,
            "height": height,
            "loc_pin_x": loc_pin_x,
            "loc_pin_y": loc_pin_y,
            "begin_x": _f(cells.get("BeginX")) if "BeginX" in cells else None,
            "begin_y": _f(cells.get("BeginY")) if "BeginY" in cells else None,
            "end_x": _f(cells.get("EndX")) if "EndX" in cells else None,
            "end_y": _f(cells.get("EndY")) if "EndY" in cells else None,
            "connector": is_connector,
        }
        found.append(shape)
        for sub in list(el):
            if _local(sub.tag) == "Shapes":
                found.extend(_collect_shapes(sub, pin_x, pin_y))
        if len(found) >= _MAX_SHAPES:
            break
    return found[:_MAX_SHAPES]


def _render_page_xml(xml: str, name: str, meta: dict[str, Any]) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {"name": name, "text": _xml_text_dump(xml), "svg": None}
    return _render_page_element(root, name, meta)


def _render_page_element(root: ET.Element, name: str, meta: dict[str, Any]) -> dict[str, Any]:
    shapes = _collect_shapes(root)
    page_w = float(meta.get("width") or 11.0)
    page_h = float(meta.get("height") or 8.5)
    # infer page size from shapes if missing/tiny
    if shapes:
        max_x = max((s["pin_x"] + s["width"] / 2 for s in shapes), default=page_w)
        max_y = max((s["pin_y"] + s["height"] / 2 for s in shapes), default=page_h)
        page_w = max(page_w, max_x + 0.4)
        page_h = max(page_h, max_y + 0.4)
    texts = [s["text"] for s in shapes if s.get("text")]
    svg = _shapes_to_svg(shapes, page_w, page_h, name)
    return {
        "name": name,
        "text": "\n".join(texts) if texts else "(no text on this page)",
        "svg": svg,
        "shape_count": len(shapes),
        "width_in": round(page_w, 3),
        "height_in": round(page_h, 3),
    }


def _fill_for(name_u: str) -> str:
    key = (name_u or "").lower()
    for k, color in _SHAPE_FILL.items():
        if k in key:
            return color
    return "#f8fafc"


def _shapes_to_svg(shapes: list[dict[str, Any]], page_w: float, page_h: float, title: str) -> str:
    w_px = max(120, int(page_w * _DPI))
    h_px = max(80, int(page_h * _DPI))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_px} {h_px}" '
        f'width="100%" role="img" aria-label="{_esc(title)}">',
        f'<rect width="{w_px}" height="{h_px}" fill="#ffffff" stroke="#e2e8f0"/>',
    ]
    for s in shapes:
        if s.get("connector") and s.get("begin_x") is not None and s.get("end_x") is not None:
            x1 = s["begin_x"] * _DPI
            y1 = (page_h - s["begin_y"]) * _DPI
            x2 = s["end_x"] * _DPI
            y2 = (page_h - s["end_y"]) * _DPI
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#64748b" stroke-width="1.5" marker-end="url(#arr)"/>'
            )
            continue
        width = max(s["width"], 0.2)
        height = max(s["height"], 0.15)
        left = (s["pin_x"] - s["loc_pin_x"]) * _DPI
        visio_top = s["pin_y"] - s["loc_pin_y"] + height
        top = (page_h - visio_top) * _DPI
        rw = width * _DPI
        rh = height * _DPI
        fill = _fill_for(s.get("name") or "")
        rx = 8 if "decision" not in (s.get("name") or "").lower() else 2
        if "decision" in (s.get("name") or "").lower():
            cx = left + rw / 2
            cy = top + rh / 2
            parts.append(
                f'<polygon points="{cx:.1f},{top:.1f} {left + rw:.1f},{cy:.1f} '
                f'{cx:.1f},{top + rh:.1f} {left:.1f},{cy:.1f}" '
                f'fill="{fill}" stroke="#334155" stroke-width="1.2"/>'
            )
        else:
            parts.append(
                f'<rect x="{left:.1f}" y="{top:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
                f'rx="{rx}" fill="{fill}" stroke="#334155" stroke-width="1.2"/>'
            )
        label = (s.get("text") or s.get("name") or "").strip()
        if label:
            fs = max(9, min(14, int(rh * 0.28)))
            # wrap roughly
            max_chars = max(6, int(rw / (fs * 0.55)))
            lines = _wrap(label, max_chars)[:4]
            ty = top + rh / 2 - (len(lines) - 1) * (fs + 2) / 2
            for i, line in enumerate(lines):
                parts.append(
                    f'<text x="{left + rw / 2:.1f}" y="{ty + i * (fs + 2):.1f}" '
                    f'text-anchor="middle" dominant-baseline="middle" '
                    f'font-size="{fs}" font-family="Segoe UI, system-ui, sans-serif" '
                    f'fill="#0f172a">{_esc(line)}</text>'
                )
    parts.append("</svg>")
    return "".join(parts)


def _wrap(text: str, width: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= width:
        return [text]
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text[:width]]


def _xml_text_dump(xml: str) -> str:
    texts = re.findall(r"<Text[^>]*>([\s\S]*?)</Text>", xml, flags=re.I)
    cleaned: list[str] = []
    for t in texts:
        t = re.sub(r"<[^>]+>", "", t)
        t = (
            t.replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
        )
        t = re.sub(r"\s+", " ", t).strip()
        if t and t not in cleaned:
            cleaned.append(t)
    return "\n".join(cleaned)


def extract_ole_strings(path: Path, limit: int = 8000) -> str:
    data = path.read_bytes()
    found: list[str] = []
    # UTF-16LE runs
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
        try:
            s = m.group().decode("utf-16le").strip()
        except UnicodeDecodeError:
            continue
        if s and s not in found and not s.startswith(("Microsoft", "Visio", "CLSID")):
            found.append(s)
    # ASCII runs
    for m in re.finditer(rb"[\x20-\x7e]{6,}", data):
        s = m.group().decode("ascii", errors="ignore").strip()
        if s and s not in found and not s.startswith(("Microsoft", "Visio")):
            found.append(s)
    text = "\n".join(found)
    return text[:limit]


def convert_binary_visio(path: Path) -> dict[str, Any] | None:
    vsd2svg = find_vsd2svg()
    if vsd2svg and Path(vsd2svg).name.lower().startswith("vsd2svg"):
        svg_pages = _convert_vsd2svg(path, vsd2svg)
        if svg_pages:
            return {"converter": "libvisio", "pages": svg_pages, "pdf_path": None}

    soffice = find_soffice()
    if soffice:
        pdf = _convert_soffice(path, soffice, "pdf")
        if pdf and pdf.is_file():
            pages = [
                {
                    "name": path.stem,
                    "text": f"Converted to PDF via LibreOffice: {pdf.name}",
                    "svg": None,
                }
            ]
            svg = _convert_soffice(path, soffice, "svg")
            if svg and svg.is_file():
                try:
                    svg_text = svg.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    svg_text = None
                if svg_text:
                    pages = [{"name": path.stem, "text": "(rendered via LibreOffice)", "svg": svg_text}]
            return {"converter": "libreoffice", "pages": pages, "pdf_path": str(pdf)}
    return None


def _cache_dir(src: Path) -> Path:
    key = hashlib.sha256(f"{src.resolve()}::{src.stat().st_mtime_ns}::{src.stat().st_size}".encode()).hexdigest()[:16]
    try:
        from backend.core.config import get_tevarn_home

        root = get_tevarn_home() / "cache" / "visio-preview" / key
    except Exception:
        root = Path(tempfile.gettempdir()) / "tevarn-visio-preview" / key
    root.mkdir(parents=True, exist_ok=True)
    return root


def _convert_soffice(src: Path, soffice: str, fmt: str) -> Path | None:
    outdir = _cache_dir(src)
    dest = outdir / f"{src.stem}.{fmt}"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        fmt,
        "--outdir",
        str(outdir),
        str(src),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=90,
            check=False,
            cwd=str(outdir),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("libreoffice convert failed: %s", e)
        return None
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    logger.info(
        "libreoffice convert no output fmt=%s rc=%s stderr=%s",
        fmt,
        proc.returncode,
        (proc.stderr or b"")[:400],
    )
    return None


def _convert_vsd2svg(src: Path, binary: str) -> list[dict[str, Any]] | None:
    outdir = _cache_dir(src)
    dest = outdir / f"{src.stem}.svg"
    if not dest.is_file():
        try:
            proc = subprocess.run(
                [binary, str(src), str(dest)],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("vsd2svg failed: %s", e)
            return None
        if not dest.is_file() or dest.stat().st_size <= 0:
            logger.info("vsd2svg no output rc=%s", proc.returncode)
            return None
    try:
        svg_text = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return [{"name": src.stem, "text": "(rendered via libvisio)", "svg": svg_text}]


def pages_as_text(preview: dict[str, Any], max_chars: int = 12000) -> str:
    """Flatten preview pages for doc_read / agent consumption."""
    parts: list[str] = []
    for i, page in enumerate(preview.get("pages") or [], start=1):
        title = page.get("name") or f"Page-{i}"
        body = (page.get("text") or "").strip()
        parts.append(f"--- {title} ---\n{body}")
    hint = preview.get("hint")
    if hint:
        parts.append(f"[hint] {hint}")
    text = "\n\n".join(parts)
    return text[:max_chars]


def build_minimal_vsdx_bytes(
    page_name: str = "Page-1",
    shapes: list[dict[str, Any]] | None = None,
) -> bytes:
    """Test helper: a tiny VSDX zip the native renderer can open."""
    shapes = shapes or [
        {
            "name": "Process",
            "text": "HelloVisio",
            "pin_x": 2.0,
            "pin_y": 6.0,
            "width": 1.8,
            "height": 0.7,
        }
    ]
    shape_xml = []
    for i, s in enumerate(shapes, start=1):
        text = _esc(str(s.get("text") or ""))
        shape_xml.append(
            f'<Shape ID="{i}" NameU="{_esc(str(s.get("name") or "Process"))}" Type="Shape">'
            f'<Cell N="PinX" V="{s.get("pin_x", 2)}"/>'
            f'<Cell N="PinY" V="{s.get("pin_y", 6)}"/>'
            f'<Cell N="Width" V="{s.get("width", 1.5)}"/>'
            f'<Cell N="Height" V="{s.get("height", 0.6)}"/>'
            f"<Text>{text}</Text>"
            f"</Shape>"
        )
    ns = "http://schemas.microsoft.com/office/visio/2012/main"
    pages_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Pages xmlns="{ns}">'
        f'<Page ID="0" Name="{_esc(page_name)}" NameU="{_esc(page_name)}">'
        f"<PageSheet>"
        f'<Cell N="PageWidth" V="11"/>'
        f'<Cell N="PageHeight" V="8.5"/>'
        f"</PageSheet></Page></Pages>"
    )
    page_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<PageContents xmlns="{ns}" xml:space="preserve">'
        f"<Shapes>{''.join(shape_xml)}</Shapes></PageContents>"
    )
    ctypes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/visio/pages/pages.xml" '
        'ContentType="application/vnd.ms-visio.pages+xml"/>'
        '<Override PartName="/visio/pages/page1.xml" '
        'ContentType="application/vnd.ms-visio.page+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("visio/pages/pages.xml", pages_xml)
        zf.writestr("visio/pages/page1.xml", page_xml)
    return buf.getvalue()
